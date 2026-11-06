"""Test API payloads, persistence, and clipboard request scheduling."""

import base64
import json
import os
import tempfile
import time
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Queue
from threading import Event, Thread
from typing import cast
from unittest.mock import Mock, patch
from uuid import uuid4

from PySide6.QtCore import QMimeData, QObject, Signal
from PySide6.QtGui import QClipboard, QImage
from PySide6.QtWidgets import QApplication

from api_client import ApiError, RequestInput, complete
from config import DEEPSEEK_PROFILE, Mode, Settings, SettingsStore, completion_url
from credentials import Credentials
from main_window import MainWindow


class MockApiHandler(BaseHTTPRequestHandler):
    """Capture a request and send the configured mock response."""

    def do_POST(self) -> None:
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        server = cast(MockApi, self.server)
        server.requests.put((self.path, self.headers["Authorization"], payload))
        if not server.unblocked.wait(3):
            return
        self.send_response(server.status)
        self.end_headers()
        try:
            self.wfile.write(json.dumps(server.response).encode())
        except OSError:
            pass

    def log_message(self, format: str, *args: object) -> None:
        """Keep the test output free of HTTP server noise."""


class MockApi(ThreadingHTTPServer):
    """Serve deterministic Chat Completions responses on loopback."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), MockApiHandler)
        self.requests: Queue[tuple[str, str, dict]] = Queue()
        self.status = 200
        self.response = {"choices": [{"message": {"content": "测试结果"}}]}
        self.unblocked = Event()
        self.unblocked.set()
        self.thread = Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        """Return the base URL for the running server."""
        return f"http://127.0.0.1:{self.server_port}/v1"

    def stop(self) -> None:
        """Unblock pending requests and stop the server."""
        self.unblocked.set()
        self.shutdown()
        self.server_close()
        self.thread.join()


class ClipboardStub(QObject):
    """Model external clipboard writes and CopyAssistant's own writes."""

    dataChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.data = QMimeData()
        self.owns = False

    def mimeData(self) -> QMimeData:
        return self.data

    def ownsClipboard(self) -> bool:
        return self.owns

    def image(self) -> QImage:
        return self.data.imageData()

    def setText(self, text: str) -> None:
        self.write(text, owned=True)

    def write(self, text: str, image: QImage | None = None, owned: bool = False) -> None:
        self.owns = owned
        self.data = QMimeData()
        self.data.setText(text)
        if image is not None:
            self.data.setImageData(image)
        self.dataChanged.emit()


class ApiTests(unittest.TestCase):
    """Cover request formats, service selection and common API failures."""

    def setUp(self) -> None:
        self.server = MockApi()
        self.addCleanup(self.server.stop)
        self.profile = replace(DEEPSEEK_PROFILE, url=self.server.url, model="mock-vision")

    def test_text_and_three_system_prompts(self) -> None:
        settings = replace(Settings(), target_language="法语")
        for mode in Mode:
            with self.subTest(mode=mode):
                prompt = settings.system_prompt(mode)
                result = complete(self.profile, "test-key", prompt, RequestInput("第一行\nSecond"))
                path, authorization, payload = self.server.requests.get_nowait()
                self.assertEqual(path, "/v1/chat/completions")
                self.assertEqual(authorization, "Bearer test-key")
                self.assertEqual(payload["model"], "mock-vision")
                self.assertEqual(payload["messages"], [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": "第一行\nSecond"},
                ])
                self.assertEqual(result, "测试结果")
        self.assertIn("法语", settings.system_prompt(Mode.TRANSLATE))
        self.assertEqual(len(set(settings.system_prompt(mode) for mode in Mode)), 3)

    def test_image_keeps_resolution_and_text_only_service_refuses_it(self) -> None:
        image = QImage(23, 14, QImage.Format.Format_RGB32)
        image.fill(0xFF008000)
        source = RequestInput(image=image)
        complete(self.profile, "test-key", "解释图片", source)
        _, _, payload = self.server.requests.get_nowait()
        image_url = payload["messages"][1]["content"][1]["image_url"]["url"]
        prefix, encoded = image_url.split(",", 1)
        self.assertEqual(prefix, "data:image/png;base64")
        self.assertEqual(QImage.fromData(base64.b64decode(encoded)).size(), image.size())
        with self.assertRaisesRegex(ApiError, "视觉模型"):
            complete(replace(self.profile, supports_images=False), "test-key", "解释图片", source)
        self.assertTrue(self.server.requests.empty())

    def test_http_and_invalid_response_errors_are_redacted(self) -> None:
        self.server.response = {"error": "private-key-and-input"}
        for status, expected in ((401, "认证"), (429, "受限"), (200, "格式异常"), (503, "HTTP 503")):
            with self.subTest(status=status):
                self.server.status = status
                with self.assertRaisesRegex(ApiError, expected) as error:
                    complete(self.profile, "private-key-and-input", "提示", RequestInput("秘密"))
                self.assertNotIn("private-key-and-input", str(error.exception))

    def test_timeout(self) -> None:
        self.server.unblocked.clear()
        with patch("api_client.REQUEST_TIMEOUT", (0.2, 0.2)):
            with self.assertRaisesRegex(ApiError, "超时"):
                complete(self.profile, "test-key", "提示", RequestInput("文本"))

    def test_base_url_and_explicit_endpoint(self) -> None:
        self.assertEqual(
            completion_url("https://example.org/v1/"), "https://example.org/v1/chat/completions"
        )
        self.assertEqual(
            completion_url("https://example.org/v1/chat/completions"),
            "https://example.org/v1/chat/completions",
        )
        with self.assertRaises(ValueError):
            completion_url("http://example.org/v1")


class SettingsTests(unittest.TestCase):
    """Check separation of persistent settings and credentials."""

    def test_settings_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = SettingsStore(Path(temporary))
            self.assertEqual(store.load(), Settings())
            settings = replace(Settings(), target_language="日语")
            settings.prompts[Mode.EXPLAIN] = "使用通俗语言"
            store.save(settings)
            self.assertEqual(store.load(), settings)
            self.assertNotIn("api_key", store.path.read_text(encoding="utf-8"))

    def test_windows_credential_persists_and_is_scoped_to_endpoint(self) -> None:
        profile = replace(DEEPSEEK_PROFILE, id=uuid4().hex)
        credentials = Credentials()
        self.addCleanup(credentials.delete, profile)
        credentials.save(profile, "test-key")
        self.assertEqual(Credentials().get(profile), "test-key")
        self.assertIsNone(Credentials().get(replace(profile, url="https://example.org/v1")))


class ClipboardTests(unittest.TestCase):
    """Exercise the Qt window's clipboard and in-flight queue behavior."""

    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        store = SettingsStore(Path(directory.name))
        self.server = MockApi()
        self.addCleanup(self.server.stop)
        self.clipboard = ClipboardStub()
        profile = replace(DEEPSEEK_PROFILE, url=self.server.url, model="first")
        second = replace(profile, id="second", name="Other", model="second")
        settings = replace(Settings(), profiles=(profile, second))
        credentials = Mock(spec=Credentials)
        credentials.get.return_value = "mock-key"
        self.window = MainWindow(cast(QClipboard, self.clipboard), settings, store, credentials)
        self.addCleanup(self.window.close)

    def finish_request(self) -> None:
        """Pump Qt events until the current request and pending input finish."""
        deadline = time.monotonic() + 4
        while self.window.busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertFalse(self.window.busy)

    def test_manual_mode_image_priority_and_switch_service(self) -> None:
        self.clipboard.write("默认不发送")
        self.assertEqual(self.window.text_preview.toPlainText(), "默认不发送")
        self.assertTrue(self.server.requests.empty())
        image = QImage(10, 7, QImage.Format.Format_RGB32)
        image.fill(0xFF0000FF)
        self.clipboard.write("与图片同时复制", image)
        self.window.services.setCurrentIndex(1)
        self.window.process_preview()
        self.finish_request()
        _, _, payload = self.server.requests.get_nowait()
        self.assertEqual(payload["model"], "second")
        self.assertEqual(payload["messages"][1]["content"][1]["type"], "image_url")
        self.assertEqual(self.window.result.toPlainText(), "测试结果")

    def test_latest_only_and_own_write_is_ignored(self) -> None:
        self.window.automatic.setChecked(True)
        self.server.unblocked.clear()
        self.clipboard.write("第一条")
        self.clipboard.write("第二条")
        self.clipboard.write("第三条")
        self.server.unblocked.set()
        self.finish_request()
        self.window.copy_result()
        self.assertFalse(self.window.busy)
        self.assertEqual(self.window.text_preview.toPlainText(), "第三条")
        contents = [self.server.requests.get_nowait()[2]["messages"][1]["content"] for _ in range(2)]
        self.assertEqual(contents, ["第一条", "第三条"])
        self.assertTrue(self.server.requests.empty())

    def test_turning_off_automatic_processing_discards_pending_item(self) -> None:
        self.window.automatic.setChecked(True)
        self.server.unblocked.clear()
        self.clipboard.write("执行")
        self.clipboard.write("取消")
        self.window.automatic.setChecked(False)
        self.server.unblocked.set()
        self.finish_request()
        self.assertEqual(self.server.requests.get_nowait()[2]["messages"][1]["content"], "执行")
        self.assertTrue(self.server.requests.empty())
