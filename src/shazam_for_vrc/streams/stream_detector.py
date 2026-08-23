"""Pure URL validation and classification for media sources."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from urllib.parse import urlsplit


class InvalidMediaUrl(ValueError):
    """Raised when a URL cannot safely be passed to a network media tool."""


class Provider(StrEnum):
    """Known origin of a media URL."""

    YOUTUBE = "youtube"
    TWITCH = "twitch"
    VRCDN = "vrcdn"
    DIRECT = "direct"
    UNKNOWN = "unknown"


class Transport(StrEnum):
    """How the selected URL exposes its media."""

    HLS = "hls"
    DASH = "dash"
    RTSP = "rtsp"
    DIRECT_MEDIA = "direct_media"
    WEBPAGE = "webpage"
    UNKNOWN = "unknown"


class PlaybackType(StrEnum):
    """Whether independent capture represents what VRChat is playing now."""

    LIVE = "live"
    PRERECORDED = "prerecorded"
    UPCOMING = "upcoming"
    ENDED = "ended"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class UrlClassification:
    """Classification inferred only from a URL, without network access."""

    provider: Provider
    transport: Transport

    @property
    def is_direct(self) -> bool:
        return self.transport in {
            Transport.HLS,
            Transport.DASH,
            Transport.RTSP,
            Transport.DIRECT_MEDIA,
        }


_YOUTUBE_HOSTS = {
    "youtu.be",
    "youtube.com",
    "youtube-nocookie.com",
    "googlevideo.com",
}
_TWITCH_HOSTS = {"twitch.tv", "ttvnw.net"}
_VRCDN_HOSTS = {"vrcdn.live"}
_DIRECT_EXTENSIONS = {
    ".aac",
    ".flac",
    ".m4a",
    ".mp3",
    ".mp4",
    ".ogg",
    ".opus",
    ".ts",
    ".wav",
    ".webm",
}


def classify_url(url: str) -> UrlClassification:
    """Validate and classify a media URL without contacting its host.

    HLS and DASH describe transport only. They intentionally do not imply that
    playback is live because both transports are also used for recorded media.
    """
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https", "rtsp", "rtspt"}:
        raise InvalidMediaUrl("The media URL uses an unsupported protocol.")
    if parsed.hostname is None:
        raise InvalidMediaUrl("The media URL does not contain a valid host.")

    hostname = parsed.hostname.lower().rstrip(".")
    provider = _provider_for_host(hostname)
    if scheme in {"rtsp", "rtspt"} and provider is not Provider.VRCDN:
        raise InvalidMediaUrl("RTSP is only allowed for a recognized media provider.")
    path = parsed.path.lower()
    suffix = PurePosixPath(path).suffix

    if scheme in {"rtsp", "rtspt"}:
        transport = Transport.RTSP
    elif suffix == ".m3u8" or "/manifest/hls" in path:
        transport = Transport.HLS
    elif suffix == ".mpd":
        transport = Transport.DASH
    elif suffix in _DIRECT_EXTENSIONS or _is_known_media_cdn(hostname):
        transport = Transport.DIRECT_MEDIA
    elif provider in {Provider.YOUTUBE, Provider.TWITCH}:
        transport = Transport.WEBPAGE
    elif path in {"", "/"}:
        transport = Transport.UNKNOWN
    else:
        # Generic pages may still be supported by yt-dlp.
        transport = Transport.WEBPAGE

    if provider is Provider.UNKNOWN and transport is not Transport.WEBPAGE:
        provider = Provider.DIRECT

    return UrlClassification(provider=provider, transport=transport)


def _provider_for_host(hostname: str) -> Provider:
    if _matches_domain(hostname, _YOUTUBE_HOSTS):
        return Provider.YOUTUBE
    if _matches_domain(hostname, _TWITCH_HOSTS):
        return Provider.TWITCH
    if _matches_domain(hostname, _VRCDN_HOSTS):
        return Provider.VRCDN
    return Provider.UNKNOWN


def _matches_domain(hostname: str, domains: set[str]) -> bool:
    return any(hostname == domain or hostname.endswith(f".{domain}") for domain in domains)


def _is_known_media_cdn(hostname: str) -> bool:
    return _matches_domain(hostname, {"googlevideo.com", "ttvnw.net"})
