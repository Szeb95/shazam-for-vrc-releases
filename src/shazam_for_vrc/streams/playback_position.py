"""Low-confidence prerecorded playback estimates derived from VRChat logs."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from urllib.parse import parse_qs, urlsplit

from shazam_for_vrc.streams.resolver import ResolvedStream
from shazam_for_vrc.streams.stream_detector import PlaybackType
from shazam_for_vrc.vrchat.player_tracker import MediaCandidate

_TIME_PARTS_RE = re.compile(
    r"^(?:(?P<hours>\d+(?:\.\d+)?)h)?"
    r"(?:(?P<minutes>\d+(?:\.\d+)?)m)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)s)?$",
    re.IGNORECASE,
)


class PositionSource(StrEnum):
    """Evidence used to estimate a prerecorded playback position."""

    AVPRO_OPEN = "avpro_open"
    URL_START = "url_start"


@dataclass(frozen=True, slots=True)
class PlaybackPositionEstimate:
    """An explicitly approximate position, never a claim of exact player state."""

    seconds: float
    source: PositionSource
    initial_offset_seconds: float
    elapsed_seconds: float
    unclamped_seconds: float
    past_reported_duration: bool = False


def estimate_playback_position(
    candidate: MediaCandidate,
    stream: ResolvedStream,
    *,
    now: datetime | None = None,
) -> PlaybackPositionEstimate | None:
    """Estimate a prerecorded position from its AVPro open time and start offset.

    VRChat does not continuously log pause, seek, loop, or network-sync changes,
    so callers must present this value as low confidence.
    """

    if stream.playback_type is not PlaybackType.PRERECORDED:
        return None

    url_offset = media_start_offset(candidate.original_url)
    log_offset = candidate.opened_offset_seconds
    initial_offset = url_offset
    source = PositionSource.URL_START
    if log_offset is not None and math.isfinite(log_offset) and log_offset > 0:
        initial_offset = log_offset
        source = PositionSource.AVPRO_OPEN

    opened_at = _parse_local_timestamp(candidate.opened_at)
    if opened_at is None:
        if initial_offset <= 0:
            return None
        past_duration = _past_duration(initial_offset, stream.duration_seconds)
        position = initial_offset
        if past_duration and stream.duration_seconds is not None and stream.duration_seconds > 0:
            position = initial_offset % stream.duration_seconds
        return PlaybackPositionEstimate(
            seconds=position,
            source=source,
            initial_offset_seconds=initial_offset,
            elapsed_seconds=0,
            unclamped_seconds=initial_offset,
            past_reported_duration=past_duration,
        )

    current_time = now or _now_matching(opened_at)
    if current_time.tzinfo is None and opened_at.tzinfo is not None:
        current_time = current_time.replace(tzinfo=opened_at.tzinfo)
    elif current_time.tzinfo is not None and opened_at.tzinfo is None:
        current_time = current_time.replace(tzinfo=None)
    elapsed = max(0.0, (current_time - opened_at).total_seconds())
    raw_position = max(0.0, initial_offset) + elapsed
    past_duration = _past_duration(raw_position, stream.duration_seconds)
    displayed_position = raw_position
    if past_duration and stream.duration_seconds is not None and stream.duration_seconds > 0:
        displayed_position = raw_position % stream.duration_seconds
    return PlaybackPositionEstimate(
        seconds=displayed_position,
        source=PositionSource.AVPRO_OPEN,
        initial_offset_seconds=max(0.0, initial_offset),
        elapsed_seconds=elapsed,
        unclamped_seconds=raw_position,
        past_reported_duration=past_duration,
    )


def media_start_offset(url: str | None) -> float:
    """Read a YouTube-style start offset from a public media page URL."""

    if not url:
        return 0.0
    try:
        parsed = urlsplit(url)
    except ValueError:
        return 0.0
    query = parse_qs(parsed.query, keep_blank_values=False)
    fragment = parse_qs(parsed.fragment, keep_blank_values=False)
    for name in ("t", "start", "time_continue"):
        for values in (query.get(name, []), fragment.get(name, [])):
            for value in values:
                if (seconds := _parse_time_value(value)) is not None:
                    return seconds
    if parsed.fragment and "=" not in parsed.fragment:
        return _parse_time_value(parsed.fragment) or 0.0
    return 0.0


def format_media_time(seconds: float | None) -> str:
    """Format seconds as MM:SS, adding hours only when needed."""

    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return "Unknown"
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, whole_seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{whole_seconds:02d}"
    return f"{minutes:02d}:{whole_seconds:02d}"


def _parse_time_value(value: str) -> float | None:
    normalized = value.strip().casefold()
    if not normalized:
        return None
    if normalized.endswith("s") and normalized[:-1].replace(".", "", 1).isdigit():
        return float(normalized[:-1])
    try:
        seconds = float(normalized)
    except ValueError:
        seconds = None
    if seconds is not None:
        return seconds if math.isfinite(seconds) and seconds >= 0 else None

    if ":" in normalized:
        parts = normalized.split(":")
        if len(parts) in {2, 3}:
            try:
                values = [float(part) for part in parts]
            except ValueError:
                return None
            if any(not math.isfinite(part) or part < 0 for part in values):
                return None
            if len(values) == 2:
                return values[0] * 60 + values[1]
            return values[0] * 3600 + values[1] * 60 + values[2]

    match = _TIME_PARTS_RE.fullmatch(normalized)
    if match is None or not any(match.groupdict().values()):
        return None
    hours = float(match.group("hours") or 0)
    minutes = float(match.group("minutes") or 0)
    seconds = float(match.group("seconds") or 0)
    return hours * 3600 + minutes * 60 + seconds


def _parse_local_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _now_matching(opened_at: datetime) -> datetime:
    if opened_at.tzinfo is None:
        return datetime.now()
    return datetime.now(opened_at.tzinfo)


def _past_duration(position: float, duration: float | None) -> bool:
    return duration is not None and position >= duration
