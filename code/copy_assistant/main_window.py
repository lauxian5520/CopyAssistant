"""Clipboard preview, automatic processing, and the desktop window."""

import logging
from dataclasses import replace
from queue import Empty, SimpleQueue
from threading import Thread

from keyring.errors import KeyringError
from PySide6.QtCore import QSignalBlocker, Qt, QTimer
from PySide6.QtGui import QClipboard, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from api_client import ApiError, RequestInput, complete
from config import (
    APP_NAME,
    MODE_LABELS,
    PREVIEW_SIZE,
    RESULT_POLL_INTERVAL_MS,
    VERSION,
    WINDOW_SIZE,
    Mode,
    ServiceProfile,
    Settings,
    SettingsStore,
)
from credentials import CredentialError, Credentials
from settings_dialog import SettingsDialog


class MainWindow(QMainWindow):
    """Display clipboard input and serialize AI requests."""

    def __init__(
        self,
        clipboard: QClipboard,
        settings: Settings,
        store: SettingsStore,
        credentials: Credentials,
    ) -> None:
        super().__init__()
        self.clipboard = clipboard
        self.settings = settings
        self.store = store
        self.credentials = credentials
        self.source: RequestInput | None = None
        self.pending: RequestInput | None = None
        self.busy = False
        self.editing_settings = False
        self.outcomes: SimpleQueue[tuple[str, str, str | None]] = SimpleQueue()
        self.setWindowTitle(f"{APP_NAME} {VERSION}")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, settings.stay_on_top)
        self.resize(*WINDOW_SIZE)
        self.build_window()
        self.refresh_profiles()
        self.clipboard.dataChanged.connect(self.clipboard_changed)
        self.preview_clipboard()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.collect_result)
        self.timer.start(RESULT_POLL_INTERVAL_MS)

    def build_window(self) -> None:
        """Create the controls and side-by-side preview and result panes."""
        central = QWidget()
        layout = QVBoxLayout(central)
        controls = QHBoxLayout()
        self.services = QComboBox()
        self.services.setAccessibleName("当前服务")
        self.services.currentIndexChanged.connect(self.select_service)
        self.modes = QComboBox()
        self.modes.setAccessibleName("处理模式")
        for mode, label in MODE_LABELS.items():
            self.modes.addItem(label, mode.value)
        self.automatic = QCheckBox("自动处理")
        self.automatic.setToolTip("开启后，新复制的内容将发送给当前 AI 服务。")
        self.automatic.toggled.connect(self.auto_changed)
        self.stay_on_top = QCheckBox("总是置顶")
        self.stay_on_top.setChecked(self.settings.stay_on_top)
        self.stay_on_top.toggled.connect(self.set_stay_on_top)
        self.process_button = QPushButton("处理")
        self.process_button.clicked.connect(self.process_preview)
        settings_button = QPushButton("设置")
        settings_button.clicked.connect(self.open_settings)
        for widget in (
            self.services, self.modes, self.automatic, self.stay_on_top,
            self.process_button, settings_button,
        ):
            controls.addWidget(widget)
        layout.addLayout(controls)

        panes = QSplitter()
        input_pane = QWidget()
        input_layout = QVBoxLayout(input_pane)
        self.source_title = QLabel("剪贴板预览")
        input_layout.addWidget(self.source_title)
        self.preview = QStackedWidget()
        self.text_preview = QPlainTextEdit()
        self.text_preview.setAccessibleName("剪贴板文本预览")
        self.text_preview.setReadOnly(True)
        self.text_preview.setPlaceholderText("复制一段文字或一张图片。")
        self.image_preview = QLabel()
        self.image_preview.setAccessibleName("剪贴板图片预览")
        self.image_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.addWidget(self.text_preview)
        self.preview.addWidget(self.image_preview)
        input_layout.addWidget(self.preview)
        panes.addWidget(input_pane)

        output_pane = QWidget()
        output_layout = QVBoxLayout(output_pane)
        self.result_title = QLabel("处理结果")
        output_layout.addWidget(self.result_title)
        self.result = QPlainTextEdit()
        self.result.setAccessibleName("处理结果")
        self.result.setReadOnly(True)
        output_layout.addWidget(self.result)
        self.copy_button = QPushButton("复制结果")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self.copy_result)
        output_layout.addWidget(self.copy_button)
        panes.addWidget(output_pane)
        panes.setStretchFactor(0, 1)
        panes.setStretchFactor(1, 1)
        layout.addWidget(panes, 1)
        self.status = QLabel("手动模式。先预览，再点击处理。")
        self.status.setWordWrap(True)
        self.status.setAccessibleName("处理状态")
        layout.addWidget(self.status)
        self.setCentralWidget(central)

    def refresh_profiles(self) -> None:
        """Refresh the active service selector from settings."""
        self.services.blockSignals(True)
        self.services.clear()
        for profile in self.settings.profiles:
            self.services.addItem(f"{profile.name} · {profile.model}", profile.id)
        row = self.services.findData(self.settings.active_profile_id)
        self.services.setCurrentIndex(max(row, 0))
        self.services.blockSignals(False)
        self.refresh_process_button()

    def select_service(self) -> None:
        """Persist a new active service selection."""
        profile_id = self.services.currentData()
        if profile_id is None:
            return
        updated = replace(self.settings, active_profile_id=profile_id)
        try:
            self.store.save(updated)
        except OSError:
            self.refresh_profiles()
            self.status.setText("无法保存当前服务。")
        else:
            self.settings = updated

    def refresh_process_button(self) -> None:
        """Enable manual requests only when an input and service are ready."""
        self.process_button.setEnabled(
            self.source is not None and not self.busy and self.services.count() > 0
        )

    def set_stay_on_top(self, enabled: bool) -> None:
        """Save the preference and apply it without closing the window."""
        updated = replace(self.settings, stay_on_top=enabled)
        try:
            self.store.save(updated)
        except OSError:
            with QSignalBlocker(self.stay_on_top):
                self.stay_on_top.setChecked(self.settings.stay_on_top)
            self.status.setText("无法保存置顶设置。")
            return
        self.settings = updated
        was_visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        if was_visible:
            self.show()

    def preview_clipboard(self) -> None:
        """Read and display the current clipboard snapshot."""
        data = self.clipboard.mimeData()
        image = self.clipboard.image() if data is not None and data.hasImage() else None
        if image is not None and not image.isNull():
            self.source = RequestInput(image=image.copy())
            thumbnail = QPixmap.fromImage(image).scaled(
                *PREVIEW_SIZE, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.image_preview.setPixmap(thumbnail)
            self.preview.setCurrentWidget(self.image_preview)
            self.source_title.setText(f"剪贴板图片 · {image.width()} × {image.height()}")
        else:
            files = data is not None and data.hasUrls() and all(url.isLocalFile() for url in data.urls())
            text = data.text() if data is not None and data.hasText() and not files else ""
            self.source = RequestInput(text=text) if text.strip() else None
            self.image_preview.clear()
            self.text_preview.setPlainText(text)
            self.preview.setCurrentWidget(self.text_preview)
            self.source_title.setText("剪贴板文本" if self.source else "剪贴板预览")
        self.refresh_process_button()

    def clipboard_changed(self) -> None:
        """Preview external clipboard writes and schedule the newest input."""
        if self.clipboard.ownsClipboard() or self.editing_settings:
            return
        self.preview_clipboard()
        if not self.automatic.isChecked():
            return
        if self.busy:
            self.pending = self.source
        elif self.source is not None:
            self.send(self.source)

    def auto_changed(self, enabled: bool) -> None:
        """Switch automatic processing for this session only."""
        if enabled:
            self.status.setText("自动处理已开启。新复制的内容会发送给当前 AI 服务。")
        else:
            self.pending = None
            self.status.setText("手动模式。先预览，再点击处理。")

    def process_preview(self) -> None:
        """Process the preview on demand."""
        if self.source is not None and not self.busy:
            self.send(self.source)

    def send(self, source: RequestInput) -> None:
        """Validate and snapshot the request, then start a worker."""
        profile_id = self.services.currentData()
        profile = next((item for item in self.settings.profiles if item.id == profile_id), None)
        if profile is None:
            self.status.setText("请先在设置中添加 AI 服务。")
            return
        if source.image is not None and not profile.supports_images:
            self.status.setText("当前模型未启用图片支持，请切换到视觉模型。")
            return
        try:
            key = self.credentials.get(profile)
        except (OSError, CredentialError, KeyringError, ValueError):
            self.status.setText("无法读取 API Key，请检查服务地址或 Windows 凭据。")
            return
        if not key:
            self.status.setText(f"请在设置中为「{profile.name}」填写 API Key。")
            return
        mode = Mode(self.modes.currentData())
        label = f"{MODE_LABELS[mode]} · {profile.name} · {profile.model}"
        self.busy = True
        self.refresh_process_button()
        self.status.setText(f"正在处理 · {label}")
        Thread(
            target=self.run_request,
            args=(profile, key, self.settings.system_prompt(mode), source, label),
            daemon=True,
        ).start()

    def run_request(
        self, profile: ServiceProfile, key: str, prompt: str, source: RequestInput, label: str
    ) -> None:
        """Perform network and image work without accessing GUI widgets."""
        try:
            result = complete(profile, key, prompt, source)
        except ApiError as error:
            self.outcomes.put((label, "", str(error)))
        except Exception as error:
            logging.error("Unexpected request failure (%s).", type(error).__name__)
            self.outcomes.put((label, "", "处理失败，请检查服务配置或查看应用日志。"))
        else:
            self.outcomes.put((label, result, None))

    def collect_result(self) -> None:
        """Apply a worker outcome and start the latest queued item if any."""
        try:
            label, result, error = self.outcomes.get_nowait()
        except Empty:
            return
        self.busy = False
        self.refresh_process_button()
        if error is not None:
            logging.warning("Chat Completions request failed.")
            self.status.setText(error)
        else:
            self.result_title.setText(f"处理结果 · {label}")
            self.result.setPlainText(result)
            self.copy_button.setEnabled(True)
            self.status.setText("处理完成。")
        pending, self.pending = self.pending, None
        if pending is not None and self.automatic.isChecked():
            self.send(pending)

    def copy_result(self) -> None:
        """Write the result to the system clipboard."""
        self.clipboard.setText(self.result.toPlainText())
        self.status.setText("已复制结果。")

    def open_settings(self) -> None:
        """Pause clipboard capture while the user configures credentials."""
        self.automatic.setChecked(False)
        self.editing_settings = True
        dialog = SettingsDialog(self.settings, self.store, self.credentials, self)
        dialog.exec()
        self.settings = dialog.settings
        self.editing_settings = False
        self.refresh_profiles()
        self.status.setText("手动模式。复制新的内容后可继续处理。")
