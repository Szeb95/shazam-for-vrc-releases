"""Small local track log used by the overlay."""

from __future__ import annotations

import json
import re
from contextlib import suppress
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from platformdirs import user_data_path

APP_NAME = "Shazam for VRC"
DEFAULT_HISTORY_LIMIT = 100
GROUP_ID_PATTERN = re.compile(r"^grp_[0-9a-fA-F-]{36}$")


class HistoryError(RuntimeError):
    """Raised when the local track log cannot be loaded or saved."""


class TrackLogKind(StrEnum):
    """Stable categories used by the Listen & Track Log filters."""

    TRACK = "track"
    MIX = "mix"
    LIVE = "live"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    """One matched track and the VRChat world in which it was heard."""

    recognized_at: str
    artist: str
    title: str
    world_name: str
    group_id: str | None = None
    group_name: str | None = None
    instance_type: str | None = None
    provider: str = "unknown"
    link: str | None = None
    copied_at: str | None = None
    kind: TrackLogKind = TrackLogKind.TRACK
    notice: str | None = None
    error_stage: str | None = None
    error_message: str | None = None
    recognition_provider: str | None = None
    recording_attempts: int = 0

    def __post_init__(self) -> None:
        for field_name in ("recognized_at", "artist", "title", "world_name"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must contain text")
        for field_name in (
            "group_id",
            "group_name",
            "instance_type",
            "link",
            "copied_at",
            "notice",
            "error_stage",
            "error_message",
            "recognition_provider",
        ):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{field_name} must contain text or be None")
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise ValueError("provider must contain text")
        if not isinstance(self.kind, TrackLogKind):
            try:
                object.__setattr__(self, "kind", TrackLogKind(self.kind))
            except (TypeError, ValueError) as error:
                raise ValueError("kind must be a valid track-log category") from error
        if (
            isinstance(self.recording_attempts, bool)
            or not isinstance(self.recording_attempts, int)
            or self.recording_attempts < 0
        ):
            raise ValueError("recording_attempts must be a non-negative integer")
        if self.kind is TrackLogKind.ERROR and not self.error_message:
            raise ValueError("error entries must include an error message")

    @property
    def copy_text(self) -> str:
        """Return the compact clipboard representation shown by the UI."""

        return self.display_track

    @property
    def is_copyable(self) -> bool:
        """Return whether this row may be included in clipboard output."""

        return self.kind is not TrackLogKind.ERROR

    @property
    def display_track(self) -> str:
        """Return the artist and title used by history and notifications."""

        return f"{self.artist} — {self.title}"

    @property
    def primary_text(self) -> str:
        """Return the compact title displayed in a collapsed track-log row."""

        if self.kind is TrackLogKind.MIX:
            return self.title
        if self.kind is TrackLogKind.ERROR:
            stage = self.error_stage or "Unknown stage"
            return f"Listening failed — {stage}"
        return self.display_track

    @property
    def display_time(self) -> str:
        """Return a short local timestamp, falling back to the stored value."""

        try:
            return datetime.fromisoformat(self.recognized_at).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return self.recognized_at

    @property
    def display_group(self) -> str | None:
        """Return a friendly group label without exposing the raw identifier."""

        if self.group_name:
            return self.group_name
        if self.group_id:
            return "Group instance"
        return None

    @property
    def group_url(self) -> str | None:
        """Return the official group page for a well-formed stored group ID."""

        if self.group_id and GROUP_ID_PATTERN.fullmatch(self.group_id):
            return f"https://vrchat.com/home/group/{self.group_id}"
        return None

    def with_copied_time(self) -> HistoryEntry:
        """Return this entry marked as copied at the current local time."""

        return replace(
            self,
            copied_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )

    @classmethod
    def create(
        cls,
        *,
        artist: str,
        title: str,
        world_name: str,
        group_id: str | None = None,
        group_name: str | None = None,
        instance_type: str | None = None,
        provider: str = "unknown",
        link: str | None = None,
        kind: TrackLogKind = TrackLogKind.TRACK,
        notice: str | None = None,
        recognition_provider: str | None = None,
        recording_attempts: int = 0,
    ) -> HistoryEntry:
        """Create a history item stamped with the current local time."""

        return cls(
            recognized_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            artist=artist.strip(),
            title=title.strip(),
            world_name=world_name.strip() or "Unknown world",
            group_id=group_id,
            group_name=group_name,
            instance_type=instance_type,
            provider=provider.strip() or "unknown",
            link=link,
            kind=kind,
            notice=notice,
            recognition_provider=recognition_provider,
            recording_attempts=recording_attempts,
        )

    @classmethod
    def create_error(
        cls,
        *,
        stage: str,
        message: str,
        world_name: str = "Unknown world",
        group_id: str | None = None,
        group_name: str | None = None,
        instance_type: str | None = None,
        provider: str = "unknown",
        recording_attempts: int = 0,
    ) -> HistoryEntry:
        """Create a non-copyable failed-attempt row stamped with local time."""

        return cls(
            recognized_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            artist="Error",
            title="Listening attempt failed",
            world_name=world_name.strip() or "Unknown world",
            group_id=group_id,
            group_name=group_name,
            instance_type=instance_type,
            provider=provider.strip() or "unknown",
            kind=TrackLogKind.ERROR,
            error_stage=stage.strip() or "Unknown stage",
            error_message=message.strip() or "The listening attempt failed.",
            recording_attempts=recording_attempts,
        )


def default_history_path() -> Path:
    """Return the per-user history path."""

    return user_data_path(APP_NAME, appauthor=False) / "history.json"


class HistoryStore:
    """Load and atomically update a bounded, newest-first history file."""

    def __init__(self, path: Path | None = None, *, limit: int = DEFAULT_HISTORY_LIMIT) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("history limit must be a positive integer")
        self.path = path or default_history_path()
        self.limit = limit

    def load(self) -> list[HistoryEntry]:
        """Return stored entries, or an empty list when history does not exist."""

        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except OSError as error:
            raise HistoryError("The track log could not be read.") from error

        try:
            raw = json.loads(text)
            if not isinstance(raw, list):
                raise ValueError("history root must be a list")
            entries = [self._entry_from_mapping(item) for item in raw]
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            raise HistoryError(
                "The track log is invalid and could not be displayed."
            ) from error
        return entries[: self.limit]

    def add(self, entry: HistoryEntry) -> list[HistoryEntry]:
        """Prepend an entry, save the bounded list, and return the new list."""

        if not isinstance(entry, HistoryEntry):
            raise TypeError("entry must be a HistoryEntry")
        entries = [entry, *self.load()][: self.limit]
        self.save(entries)
        return entries

    def save(self, entries: list[HistoryEntry]) -> None:
        """Atomically save entries in their supplied display order."""

        if any(not isinstance(entry, HistoryEntry) for entry in entries):
            raise TypeError("entries must contain only HistoryEntry values")
        temporary_path = self.path.with_suffix(".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path.write_text(
                json.dumps([asdict(entry) for entry in entries[: self.limit]], indent=2) + "\n",
                encoding="utf-8",
            )
            temporary_path.replace(self.path)
        except OSError as error:
            with suppress(OSError):
                temporary_path.unlink(missing_ok=True)
            raise HistoryError("The track log could not be saved.") from error

    @staticmethod
    def _entry_from_mapping(raw: Any) -> HistoryEntry:
        if not isinstance(raw, dict):
            raise ValueError("history entry must be an object")
        return HistoryEntry(
            recognized_at=raw.get("recognized_at"),
            artist=raw.get("artist"),
            title=raw.get("title"),
            world_name=raw.get("world_name"),
            group_id=raw.get("group_id"),
            group_name=raw.get("group_name"),
            instance_type=raw.get("instance_type"),
            provider=raw.get("provider", "unknown"),
            link=raw.get("link"),
            copied_at=raw.get("copied_at"),
            kind=raw.get("kind", TrackLogKind.TRACK.value),
            notice=raw.get("notice"),
            error_stage=raw.get("error_stage"),
            error_message=raw.get("error_message"),
            recognition_provider=raw.get("recognition_provider"),
            recording_attempts=raw.get("recording_attempts", 0),
        )


def copyable_log_text(entries: list[HistoryEntry]) -> str:
    """Return newline-separated clipboard text while always excluding errors."""

    return "\n".join(entry.copy_text for entry in entries if entry.is_copyable)
