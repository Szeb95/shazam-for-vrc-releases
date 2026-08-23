"""Optional VRChat OSC chatbox output."""

from __future__ import annotations

import socket
from collections.abc import Callable
from enum import StrEnum
from typing import Protocol

from shazam_for_vrc.streams.stream_detector import Provider

DEFAULT_VRCHAT_OSC_HOST = "127.0.0.1"
DEFAULT_VRCHAT_OSC_PORT = 9000
VRCHAT_CHATBOX_ADDRESS = "/chatbox/input"
VRCHAT_CHATBOX_MAX_CHARACTERS = 144

_PROVIDER_BRAND_NAMES = {
    Provider.YOUTUBE: "YouTube",
    Provider.VRCDN: "VRCDN",
}
_WORLD_STREAM_PROVIDERS = {Provider.DIRECT, Provider.UNKNOWN}


class ChatboxTrackContext(StrEnum):
    """How a recognized track relates to the media playing in VRChat."""

    LIVE_STREAM = "live_stream"
    MIX_TRACK = "mix_track"
    TRACK = "track"


class ChatboxOutputError(RuntimeError):
    """Raised when a chatbox message could not be handed to VRChat."""


class ChatboxSender(Protocol):
    """Typed boundary implemented by optional chatbox destinations."""

    def send_track(
        self,
        artist: str,
        title: str,
        *,
        provider: Provider,
        context: ChatboxTrackContext,
    ) -> None:
        """Send one recognized track to the user's chatbox."""

        ...

    def send_mix(self, title: str) -> None:
        """Send one whole-mix fallback to the user's chatbox."""

        ...


DatagramSender = Callable[[bytes, tuple[str, int]], None]


class VRChatOscChatbox:
    """Send successful track results to VRChat's local OSC chatbox input."""

    def __init__(
        self,
        *,
        host: str = DEFAULT_VRCHAT_OSC_HOST,
        port: int = DEFAULT_VRCHAT_OSC_PORT,
        datagram_sender: DatagramSender | None = None,
    ) -> None:
        if host != DEFAULT_VRCHAT_OSC_HOST:
            raise ValueError("VRChat chatbox output must use the local host")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65_535:
            raise ValueError("VRChat OSC port must be between 1 and 65535")
        self._address = (host, port)
        self._datagram_sender = datagram_sender or _send_datagram

    def send_track(
        self,
        artist: str,
        title: str,
        *,
        provider: Provider,
        context: ChatboxTrackContext,
    ) -> None:
        """Send a context-labelled track immediately and without chat SFX."""

        message = format_track_message(
            artist,
            title,
            provider=provider,
            context=context,
        )
        self._send_message(message)

    def send_mix(self, title: str) -> None:
        """Send a mix-labelled message when its current track cannot be located."""

        self._send_message(format_mix_message(title))

    def _send_message(self, message: str) -> None:
        payload = _encode_chatbox_message(message)
        try:
            self._datagram_sender(payload, self._address)
        except OSError as error:
            raise ChatboxOutputError(
                "The recognized track could not be sent to the VRChat chatbox."
            ) from error


def format_track_message(
    artist: str,
    title: str,
    *,
    provider: Provider,
    context: ChatboxTrackContext,
) -> str:
    """Create a context-labelled track message within VRChat's character limit."""

    normalized_artist = _normalize_field(artist, "artist")
    normalized_title = _normalize_field(title, "title")
    if not isinstance(provider, Provider):
        raise ValueError("track provider is invalid")
    if not isinstance(context, ChatboxTrackContext):
        raise ValueError("chatbox track context is invalid")
    if context is ChatboxTrackContext.LIVE_STREAM:
        label = f"{provider_stream_name(provider)} stream song"
    elif context is ChatboxTrackContext.MIX_TRACK:
        label = "Song in mix"
    else:
        label = f"{provider_stream_name(provider)} song"
    message = f"{label}: {normalized_title} — {normalized_artist}"
    if len(message) <= VRCHAT_CHATBOX_MAX_CHARACTERS:
        return message
    return message[: VRCHAT_CHATBOX_MAX_CHARACTERS - 1].rstrip() + "…"


def format_mix_message(title: str) -> str:
    """Create a whole-mix message that does not mislabel it as a stream song."""

    normalized_title = _normalize_field(title, "title")
    message = f"Mix: {normalized_title}"
    if len(message) <= VRCHAT_CHATBOX_MAX_CHARACTERS:
        return message
    return message[: VRCHAT_CHATBOX_MAX_CHARACTERS - 1].rstrip() + "…"


def provider_stream_name(provider: Provider) -> str:
    """Return a chatbox source name with automatic support for future providers."""

    if not isinstance(provider, Provider):
        raise ValueError("track provider is invalid")
    if provider in _WORLD_STREAM_PROVIDERS:
        return "World"
    return _PROVIDER_BRAND_NAMES.get(
        provider,
        provider.value.replace("_", " ").title(),
    )


def _normalize_field(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"track {label} must not be empty")
    return " ".join(value.split())


def _encode_chatbox_message(message: str) -> bytes:
    # OSC booleans are represented by their type tags and carry no value bytes.
    # T sends immediately; F suppresses VRChat's additional chatbox SFX.
    return b"".join(
        (
            _encode_osc_string(VRCHAT_CHATBOX_ADDRESS),
            _encode_osc_string(",sTF"),
            _encode_osc_string(message),
        )
    )


def _encode_osc_string(value: str) -> bytes:
    encoded = value.encode("utf-8") + b"\0"
    return encoded + (b"\0" * (-len(encoded) % 4))


def _send_datagram(payload: bytes, address: tuple[str, int]) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        client.sendto(payload, address)
