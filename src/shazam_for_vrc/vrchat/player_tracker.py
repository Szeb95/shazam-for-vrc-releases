"""Select the most useful media candidate from a VRChat log snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from shazam_for_vrc.vrchat.log_reader import LogSnapshot


class MediaSelectionError(RuntimeError):
    """Base error raised when a usable media candidate cannot be selected."""


class NoActiveMediaError(MediaSelectionError):
    """Raised when the current VRChat world has no reported media URL."""


class SelectionReason(StrEnum):
    """Why a candidate was selected from the available log information."""

    LATEST_AVPRO_OPEN = "latest_avpro_open"
    LATEST_RESOLUTION = "latest_resolution"
    LATEST_REQUEST = "latest_request"


@dataclass(frozen=True, slots=True)
class MediaCandidate:
    """Media information selected from the current VRChat world."""

    original_url: str | None
    resolved_url: str | None
    player_type: str | None
    requested_at: str | None
    resolved_at: str | None
    opened_at: str | None
    reason: SelectionReason
    opened_offset_seconds: float | None = None

    @property
    def preferred_url(self) -> str:
        """Return VRChat's resolved URL when present, otherwise the original URL."""
        url = self.resolved_url or self.original_url
        if url is None:  # Guard against manually constructed invalid candidates.
            raise NoActiveMediaError("No media URL is available for the current world.")
        return url


def select_active_media(snapshot: LogSnapshot) -> MediaCandidate:
    """Select the latest media activity already isolated by the log reader.

    VRChat's supported log messages do not contain a stable player-object ID, so
    version 1 deliberately uses the latest activity in the current world rather
    than pretending it can distinguish several simultaneous video players.
    """
    media = snapshot.media
    original_url = media.original_url if _has_text(media.original_url) else None
    resolved_url = media.resolved_url if _has_text(media.resolved_url) else None
    if original_url is None and resolved_url is None:
        raise NoActiveMediaError("No media URL is available for the current world.")

    if media.opened_at is not None or media.player_type is not None:
        reason = SelectionReason.LATEST_AVPRO_OPEN
    elif resolved_url is not None:
        reason = SelectionReason.LATEST_RESOLUTION
    else:
        reason = SelectionReason.LATEST_REQUEST

    return MediaCandidate(
        original_url=original_url,
        resolved_url=resolved_url,
        player_type=media.player_type,
        requested_at=media.requested_at,
        resolved_at=media.resolved_at,
        opened_at=media.opened_at,
        opened_offset_seconds=media.opened_offset_seconds,
        reason=reason,
    )


def _has_text(value: str | None) -> bool:
    return value is not None and bool(value.strip())
