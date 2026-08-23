"""Resolve a selected VRChat media candidate to a playable stream."""

from __future__ import annotations

import logging
import re
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from shazam_for_vrc.streams.stream_detector import (
    PlaybackType,
    Provider,
    Transport,
    UrlClassification,
    classify_url,
)
from shazam_for_vrc.vrchat.player_tracker import MediaCandidate

logger = logging.getLogger(__name__)

_LONG_FORM_TITLE_RE = re.compile(
    r"\b(?:mix(?:es)?|dj\s+set|live\s+set|full\s+album|compilation|playlist|"
    r"megamix|mixtape|mix\s+tape)\b",
    re.IGNORECASE,
)
_LONG_FORM_DURATION_SECONDS = 20 * 60
_SINGLE_TRACK_MAX_SECONDS = 15 * 60

Metadata = Mapping[str, Any]
MetadataExtractor = Callable[[str, float], Metadata]


class StreamResolutionError(RuntimeError):
    """Base error for failures while resolving a media source."""


class ResolverUnavailableError(StreamResolutionError):
    """Raised when yt-dlp is not installed or cannot be loaded."""


class ResolutionTimeoutError(StreamResolutionError):
    """Raised when media metadata resolution exceeds its network timeout."""


class UnsupportedMediaError(StreamResolutionError):
    """Raised when yt-dlp does not support a media page."""


class NoPlayableAudioError(StreamResolutionError):
    """Raised when resolution succeeds but exposes no usable media URL."""


class ResolutionSource(StrEnum):
    """Where the final playable URL came from."""

    YT_DLP = "yt_dlp"
    VRCHAT_LOG = "vrchat_log"


class ContentKind(StrEnum):
    """Coarse media shape used to choose a safe prerecorded strategy."""

    SINGLE_TRACK = "single_track"
    LONG_FORM = "long_form"
    UNKNOWN = "unknown"


class MetadataConfidence(StrEnum):
    """Confidence in the content-kind classification."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True, slots=True)
class MediaChapter:
    """A chapter reported by the media provider."""

    title: str
    start_seconds: float
    end_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class MediaMetadata:
    """Nonsensitive provider metadata retained alongside a resolved stream."""

    title: str | None = None
    track: str | None = None
    artist: str | None = None
    album: str | None = None
    content_kind: ContentKind = ContentKind.UNKNOWN
    confidence: MetadataConfidence = MetadataConfidence.LOW
    chapters: tuple[MediaChapter, ...] = ()


@dataclass(frozen=True, slots=True)
class ResolvedStream:
    """Normalized output consumed later by the audio capture subsystem."""

    original_url: str = field(repr=False)
    stream_url: str = field(repr=False)
    provider: Provider
    transport: Transport
    playback_type: PlaybackType
    resolution_source: ResolutionSource
    http_headers: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({}), repr=False
    )
    duration_seconds: float | None = None
    current_position_seconds: float | None = None
    rtsp_transport: str | None = None
    metadata: MediaMetadata | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "http_headers", MappingProxyType(dict(self.http_headers)))

    @property
    def supports_current_audio(self) -> bool:
        """Return whether capture is known to represent the current VRChat moment."""
        return self.playback_type is PlaybackType.LIVE


def resolve_stream(
    candidate: MediaCandidate,
    *,
    timeout_seconds: float = 15.0,
    metadata_extractor: MetadataExtractor | None = None,
) -> ResolvedStream:
    """Resolve and classify a selected media candidate.

    A fresh yt-dlp URL is preferred because URLs copied from VRChat logs are
    often signed and temporary. If metadata lookup fails but VRChat logged a
    direct playable URL, that URL remains usable with an UNKNOWN playback type.
    """
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    original_url = candidate.original_url or candidate.preferred_url
    original_classification = classify_url(original_url)
    if direct_vrcdn := _direct_vrcdn_live(candidate, original_url):
        return direct_vrcdn
    fallback = _logged_fallback(candidate)
    extractor = metadata_extractor or _extract_with_yt_dlp

    try:
        metadata = _unwrap_metadata(extractor(original_url, timeout_seconds))
    except Exception as error:
        mapped_error = _map_extractor_error(error)
        if fallback is None:
            raise mapped_error from error
        logger.warning(
            "Media metadata resolution failed; using VRChat's logged stream URL (%s).",
            type(mapped_error).__name__,
        )
        return _stream_from_log(original_url, fallback)

    playback_type = _playback_type(metadata)
    media_metadata = _media_metadata(metadata)
    if _prefer_logged_twitch_live(
        original_classification=original_classification,
        fallback=fallback,
        playback_type=playback_type,
    ):
        # Twitch can expose an audio-only rendition that is valid media but
        # contains digital silence while VRChat's own HLS rendition has the
        # audible program. Metadata still confirms that the source is live;
        # capture then follows the exact direct URL AVPro is currently playing.
        return _stream_from_log(
            original_url,
            fallback,
            playback_type=playback_type,
            duration_seconds=_duration(metadata),
            metadata=media_metadata,
        )

    stream_url, selected_metadata = _select_stream_url(metadata)
    if stream_url is None:
        if fallback is not None:
            logger.warning(
                "Resolved media metadata contained no playable URL; using VRChat's logged URL."
            )
            return _stream_from_log(
                original_url,
                fallback,
                playback_type=_playback_type(metadata),
                duration_seconds=_duration(metadata),
                metadata=media_metadata,
            )
        raise NoPlayableAudioError("The media source did not expose a playable audio stream.")

    selected_classification = classify_url(stream_url)
    provider = original_classification.provider
    if provider is Provider.UNKNOWN:
        provider = selected_classification.provider
    headers = _http_headers(selected_metadata, metadata)

    return ResolvedStream(
        original_url=original_url,
        stream_url=stream_url,
        provider=provider,
        transport=selected_classification.transport,
        playback_type=playback_type,
        resolution_source=ResolutionSource.YT_DLP,
        http_headers=headers,
        duration_seconds=_duration(metadata),
        metadata=media_metadata,
    )


def _prefer_logged_twitch_live(
    *,
    original_classification: UrlClassification,
    fallback: tuple[str, UrlClassification] | None,
    playback_type: PlaybackType,
) -> bool:
    if fallback is None or playback_type is not PlaybackType.LIVE:
        return False
    _fallback_url, fallback_classification = fallback
    return (
        original_classification.provider is Provider.TWITCH
        and fallback_classification.provider is Provider.TWITCH
        and fallback_classification.transport is Transport.HLS
    )


def _logged_fallback(candidate: MediaCandidate) -> tuple[str, UrlClassification] | None:
    if candidate.resolved_url is None:
        return None
    classification = classify_url(candidate.resolved_url)
    if not classification.is_direct:
        return None
    return candidate.resolved_url, classification


def _direct_vrcdn_live(candidate: MediaCandidate, original_url: str) -> ResolvedStream | None:
    stream_url = candidate.resolved_url or original_url
    classification = classify_url(stream_url)
    if classification.provider is not Provider.VRCDN:
        return None

    parsed = urlsplit(stream_url)
    path = parsed.path.lower()
    if not path.startswith("/live/"):
        return None
    if classification.transport not in {
        Transport.HLS,
        Transport.RTSP,
        Transport.DIRECT_MEDIA,
    }:
        return None

    rtsp_transport = None
    if parsed.scheme.lower() == "rtspt":
        parsed = parsed._replace(scheme="rtsp")
        stream_url = urlunsplit(parsed)
        rtsp_transport = "tcp"

    return ResolvedStream(
        original_url=original_url,
        stream_url=stream_url,
        provider=Provider.VRCDN,
        transport=classification.transport,
        playback_type=PlaybackType.LIVE,
        resolution_source=ResolutionSource.VRCHAT_LOG,
        rtsp_transport=rtsp_transport,
    )


def _stream_from_log(
    original_url: str,
    fallback: tuple[str, UrlClassification],
    *,
    playback_type: PlaybackType = PlaybackType.UNKNOWN,
    duration_seconds: float | None = None,
    metadata: MediaMetadata | None = None,
) -> ResolvedStream:
    stream_url, classification = fallback
    return ResolvedStream(
        original_url=original_url,
        stream_url=stream_url,
        provider=classification.provider,
        transport=classification.transport,
        playback_type=playback_type,
        resolution_source=ResolutionSource.VRCHAT_LOG,
        duration_seconds=duration_seconds,
        metadata=metadata,
    )


def _extract_with_yt_dlp(url: str, timeout_seconds: float) -> Metadata:
    try:
        from yt_dlp import YoutubeDL
    except ImportError as error:
        raise ResolverUnavailableError(
            "yt-dlp is required to inspect and refresh media streams."
        ) from error

    options: dict[str, object] = {
        "extract_flat": False,
        "format": "bestaudio/best",
        "noplaylist": True,
        "no_warnings": True,
        "quiet": True,
        "skip_download": True,
        "socket_timeout": timeout_seconds,
    }
    with YoutubeDL(options) as ydl:
        result = ydl.extract_info(url, download=False)
    if not isinstance(result, Mapping):
        raise StreamResolutionError("The media resolver returned an invalid response.")
    return result


def _unwrap_metadata(metadata: Metadata) -> Metadata:
    if metadata.get("_type") not in {"playlist", "multi_video"}:
        return metadata
    entries = metadata.get("entries")
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, Mapping):
                return entry
    raise NoPlayableAudioError("The media page did not contain a playable item.")


def _select_stream_url(metadata: Metadata) -> tuple[str | None, Metadata]:
    if (direct_url := _nonempty_string(metadata.get("url"))) and metadata.get("acodec") != "none":
        return direct_url, metadata

    requested = metadata.get("requested_formats")
    if isinstance(requested, list):
        selected = _best_audio_format(requested)
        if selected is not None:
            return _nonempty_string(selected.get("url")), selected

    formats = metadata.get("formats")
    if isinstance(formats, list):
        selected = _best_audio_format(formats)
        if selected is not None:
            return _nonempty_string(selected.get("url")), selected
    return None, metadata


def _best_audio_format(formats: list[object]) -> Metadata | None:
    candidates = [
        item
        for item in formats
        if isinstance(item, Mapping)
        and _nonempty_string(item.get("url")) is not None
        and item.get("acodec") != "none"
    ]
    if not candidates:
        return None

    def score(item: Metadata) -> tuple[int, float, float]:
        audio_only = int(item.get("vcodec") == "none")
        abr = _number(item.get("abr")) or 0.0
        tbr = _number(item.get("tbr")) or 0.0
        return audio_only, abr, tbr

    return max(candidates, key=score)


def _playback_type(metadata: Metadata) -> PlaybackType:
    live_status = str(metadata.get("live_status") or "").lower()
    if metadata.get("is_live") is True or live_status == "is_live":
        return PlaybackType.LIVE
    if live_status == "is_upcoming":
        return PlaybackType.UPCOMING
    if live_status == "post_live":
        return PlaybackType.ENDED
    if live_status in {"not_live", "was_live"} or _duration(metadata) is not None:
        return PlaybackType.PRERECORDED
    return PlaybackType.UNKNOWN


def _duration(metadata: Metadata) -> float | None:
    return _number(metadata.get("duration"))


def _media_metadata(metadata: Metadata) -> MediaMetadata:
    title = _nonempty_string(metadata.get("title"))
    track = _nonempty_string(metadata.get("track"))
    artist = _nonempty_string(metadata.get("artist"))
    album = _nonempty_string(metadata.get("album"))
    chapters = _chapters(metadata.get("chapters"))
    content_kind, confidence = _content_kind(
        title=title,
        track=track,
        artist=artist,
        duration_seconds=_duration(metadata),
        chapter_count=len(chapters),
    )
    return MediaMetadata(
        title=title,
        track=track,
        artist=artist,
        album=album,
        content_kind=content_kind,
        confidence=confidence,
        chapters=chapters,
    )


def _chapters(value: object) -> tuple[MediaChapter, ...]:
    if not isinstance(value, list):
        return ()
    chapters: list[MediaChapter] = []
    for raw_chapter in value:
        if not isinstance(raw_chapter, Mapping):
            continue
        title = _nonempty_string(raw_chapter.get("title"))
        start = _number(raw_chapter.get("start_time"))
        end = _number(raw_chapter.get("end_time"))
        if title is None or start is None or start < 0:
            continue
        chapters.append(
            MediaChapter(
                title=title,
                start_seconds=start,
                end_seconds=end if end is not None and end >= start else None,
            )
        )
    return tuple(chapters)


def _content_kind(
    *,
    title: str | None,
    track: str | None,
    artist: str | None,
    duration_seconds: float | None,
    chapter_count: int,
) -> tuple[ContentKind, MetadataConfidence]:
    if track is not None and artist is not None:
        return ContentKind.SINGLE_TRACK, MetadataConfidence.HIGH
    if chapter_count >= 2 or (
        duration_seconds is not None and duration_seconds >= _LONG_FORM_DURATION_SECONDS
    ):
        return ContentKind.LONG_FORM, MetadataConfidence.HIGH
    if title is not None and _LONG_FORM_TITLE_RE.search(title):
        return ContentKind.LONG_FORM, MetadataConfidence.MEDIUM
    if duration_seconds is not None and duration_seconds <= _SINGLE_TRACK_MAX_SECONDS:
        return ContentKind.SINGLE_TRACK, MetadataConfidence.MEDIUM
    return ContentKind.UNKNOWN, MetadataConfidence.LOW


def _http_headers(selected: Metadata, parent: Metadata) -> Mapping[str, str]:
    raw_headers = selected.get("http_headers") or parent.get("http_headers")
    if not isinstance(raw_headers, Mapping):
        return {}
    return {
        str(name): str(value)
        for name, value in raw_headers.items()
        if isinstance(name, str) and value is not None
    }


def _map_extractor_error(error: Exception) -> StreamResolutionError:
    if isinstance(error, StreamResolutionError):
        return error
    if isinstance(error, (TimeoutError, socket.timeout)) or _caused_by_timeout(error):
        return ResolutionTimeoutError("Media resolution timed out.")
    message = str(error).lower()
    if "unsupported url" in message:
        return UnsupportedMediaError("The media provider is not supported.")
    if "timed out" in message or "timeout" in message:
        return ResolutionTimeoutError("Media resolution timed out.")
    return StreamResolutionError("The media source could not be resolved.")


def _caused_by_timeout(error: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        if isinstance(current, (TimeoutError, socket.timeout)):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


def _nonempty_string(value: object) -> str | None:
    return value if isinstance(value, str) and bool(value.strip()) else None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)
