"""A minimal client for OpenAI-compatible Chat Completions."""

import base64
from dataclasses import dataclass

import requests
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage

from config import MAX_IMAGE_BYTES, REQUEST_TIMEOUT, ServiceProfile, completion_url


class ApiError(Exception):
    """An error that can be displayed without exposing request data."""


@dataclass(frozen=True)
class RequestInput:
    """A snapshot of one clipboard item."""

    text: str = ""
    image: QImage | None = None

    def message(self) -> str | list[dict[str, str | dict[str, str]]]:
        """Encode a text or image user message."""
        if self.image is None:
            return self.text
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not self.image.save(buffer, "PNG"):
            raise ApiError("无法编码剪贴板图片。")
        data = buffer.data()
        if data.size() > MAX_IMAGE_BYTES:
            raise ApiError("PNG 图片超过 20 MiB，请复制较小的图片。")
        image_url = "data:image/png;base64," + base64.b64encode(data.data()).decode("ascii")
        return [
            {"type": "text", "text": "请按系统提示处理这张图片。"},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]


def complete(profile: ServiceProfile, key: str, prompt: str, source: RequestInput) -> str:
    """Send one request and return the assistant message text."""
    if source.image is not None and not profile.supports_images:
        raise ApiError("当前模型未启用图片支持，请切换到视觉模型。")
    payload = {
        "model": profile.model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": source.message()},
        ],
        "stream": False,
    }
    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.post(
                completion_url(profile.url),
                headers={"Authorization": f"Bearer {key}"},
                json=payload,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=False,
            )
    except requests.Timeout as error:
        raise ApiError("请求超时，请稍后再试。") from error
    except requests.RequestException as error:
        raise ApiError("无法连接 API，请检查地址和网络。") from error

    status = response.status_code
    if status in (401, 403):
        raise ApiError(f"认证或访问失败（HTTP {status}），请检查 API Key 和模型权限。")
    if status == 429:
        raise ApiError("API 请求受限（HTTP 429），请检查额度或稍后重试。")
    if not 200 <= status < 300:
        raise ApiError(f"API 返回 HTTP {status}，请检查接口地址、模型和输入。")
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as error:
        raise ApiError("API 响应格式异常，请检查接口兼容性。") from error
    if not isinstance(content, str) or not content.strip():
        raise ApiError("模型未返回有效文本。")
    return content
