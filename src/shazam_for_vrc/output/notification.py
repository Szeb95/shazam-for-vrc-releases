"""Optional desktop and VR notification adapters."""

from __future__ import annotations

import json
import math
import socket
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

DEFAULT_XSOVERLAY_HOST = "127.0.0.1"
DEFAULT_XSOVERLAY_PORT = 42069
SOURCE_APP = "Shazam for VRC"


class NotificationError(RuntimeError):
    """Raised when a notification could not be handed to an output adapter."""


class NotificationStyle(StrEnum):
    """Visual and audio treatments understood by XSOverlay."""

    DEFAULT = "default"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Notification:
    """A provider-independent short user notification."""

    title: str
    content: str = ""
    style: NotificationStyle = NotificationStyle.DEFAULT
    timeout_seconds: float = 4.0

    def __post_init__(self) -> None:
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("notification title must not be empty")
        if not isinstance(self.content, str):
            raise ValueError("notification content must be a string")
        if not isinstance(self.style, NotificationStyle):
            raise ValueError("notification style is invalid")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(float(self.timeout_seconds))
            or not 0.5 <= float(self.timeout_seconds) <= 30.0
        ):
            raise ValueError("notification timeout must be between 0.5 and 30 seconds")


class NotificationSender(Protocol):
    """Typed boundary implemented by optional notification destinations."""

    def send(self, notification: Notification) -> None:
        """Send one notification or raise ``NotificationError``."""

        ...


DatagramSender = Callable[[bytes, tuple[str, int]], None]


class XSOverlayNotifier:
    """Send local XSOverlay notification datagrams without retaining their content."""

    def __init__(
        self,
        *,
        host: str = DEFAULT_XSOVERLAY_HOST,
        port: int = DEFAULT_XSOVERLAY_PORT,
        datagram_sender: DatagramSender | None = None,
    ) -> None:
        if host != DEFAULT_XSOVERLAY_HOST:
            raise ValueError("XSOverlay notifications must use the local host")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65_535:
            raise ValueError("XSOverlay notification port must be between 1 and 65535")
        self._address = (host, port)
        self._datagram_sender = datagram_sender or _send_datagram

    def send(self, notification: Notification) -> None:
        """Serialize and send one notification using XSOverlay's local UDP API."""

        if not isinstance(notification, Notification):
            raise TypeError("notification must be a Notification")
        payload = {
            "messageType": 1,
            "index": 0,
            "timeout": float(notification.timeout_seconds),
            "height": 175.0,
            "opacity": 1.0,
            "volume": 0.7,
            "audioPath": notification.style.value,
            "title": _safe_text(notification.title, limit=120),
            "content": _safe_text(notification.content, limit=500),
            "useBase64Icon": False,
            "icon": notification.style.value,
            "sourceApp": SOURCE_APP,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            self._datagram_sender(encoded, self._address)
        except OSError as error:
            raise NotificationError("The notification could not be sent to XSOverlay.") from error


def _send_datagram(payload: bytes, address: tuple[str, int]) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        client.sendto(payload, address)


def _safe_text(value: str, *, limit: int) -> str:
    """Keep external text plain in XSOverlay's rich-text notification fields."""

    normalized = " ".join(value.split())
    normalized = normalized.replace("<", "‹").replace(">", "›")
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"
