"""Recognition result outputs."""

from shazam_for_vrc.output.notification import (
    Notification,
    NotificationError,
    NotificationSender,
    NotificationStyle,
    XSOverlayNotifier,
)
from shazam_for_vrc.output.osc import (
    ChatboxOutputError,
    ChatboxSender,
    ChatboxTrackContext,
    VRChatOscChatbox,
)

__all__ = [
    "ChatboxOutputError",
    "ChatboxSender",
    "ChatboxTrackContext",
    "Notification",
    "NotificationError",
    "NotificationSender",
    "NotificationStyle",
    "VRChatOscChatbox",
    "XSOverlayNotifier",
]
