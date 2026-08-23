"""Provider-independent music-recognition results."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RecognitionStatus(StrEnum):
    """The normalized outcome of a music-recognition request."""

    MATCHED = "matched"
    NO_MATCH = "no_match"


@dataclass(frozen=True, slots=True)
class RecognizedTrack:
    """Track metadata normalized independently of the recognition provider."""

    title: str
    artist: str
    album: str | None = None
    genre: str | None = None
    artwork_url: str | None = None
    track_url: str | None = None
    provider_track_id: str | None = None


@dataclass(frozen=True, slots=True)
class RecognitionResult:
    """A successful provider response, with or without a catalog match."""

    status: RecognitionStatus
    track: RecognizedTrack | None
    provider: str
    attempt_count: int

    def __post_init__(self) -> None:
        if self.attempt_count < 1:
            raise ValueError("attempt_count must be at least one")
        if self.status is RecognitionStatus.MATCHED and self.track is None:
            raise ValueError("A matched result must include a track")
        if self.status is RecognitionStatus.NO_MATCH and self.track is not None:
            raise ValueError("A no-match result cannot include a track")

    @property
    def is_match(self) -> bool:
        """Return whether the provider identified a track."""

        return self.status is RecognitionStatus.MATCHED
