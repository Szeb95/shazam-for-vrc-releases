"""On-demand snapshots of the current VRChat world and media source."""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_TAIL_CHUNK_SIZE = 64 * 1024
LOG_GLOB = "output_log_*.txt"

_WORLD_RE = re.compile(r"\bEntering\s+Room\s*:\s*(?P<name>.+?)\s*$", re.IGNORECASE)
_JOIN_RE = re.compile(r"\bJoining\s+(?P<world>wrld_[0-9a-f-]+):(?P<instance>\S+)", re.IGNORECASE)
_ATTEMPT_RE = re.compile(
    r"\[Video\s+Playback\].*?Attempting\s+to\s+resolve\s+URL\s+"
    r"(?P<quote>['\"])(?P<url>.*?)(?P=quote)",
    re.IGNORECASE,
)
_RESOLVING_RE = re.compile(
    r"\[Video\s+Playback\].*?Resolving\s+URL\s+(?P<quote>['\"])(?P<url>.*?)(?P=quote)",
    re.IGNORECASE,
)
_RESOLVED_RE = re.compile(
    r"\[Video\s+Playback\].*?URL\s+(?P<quote1>['\"])(?P<original>.*?)"
    r"(?P=quote1)\s+resolved\s+to\s+(?P<quote2>['\"])(?P<resolved>.*?)"
    r"(?P=quote2)",
    re.IGNORECASE,
)
_AVPRO_RE = re.compile(
    r"\[AVProVideo\]\s+Opening\s+(?P<url>.+?)(?:\s+\(offset\s+"
    r"(?P<offset>-?\d+(?:\.\d+)?)\)|,\s*reload\s*:|\s*$)",
    re.IGNORECASE,
)
_TIMESTAMP_RE = re.compile(
    r"^\s*(?P<date>\d{4}[.-]\d{2}[.-]\d{2})[ T]"
    r"(?P<time>\d{2}:\d{2}:\d{2}(?:[.,]\d{1,6})?)\b"
)
_INSTANCE_VALUE_RE = re.compile(r"~(?P<key>[A-Za-z]+)\((?P<value>[^)]*)\)")


@dataclass(frozen=True, slots=True)
class WorldInfo:
    """The latest world entry present in the selected log."""

    name: str | None = None
    world_id: str | None = None
    instance_id: str | None = None
    group_id: str | None = None
    instance_type: str | None = None


@dataclass(frozen=True, slots=True)
class MediaInfo:
    """The latest media activity after the current world entry."""

    original_url: str | None = None
    resolved_url: str | None = None
    player_type: str | None = None
    requested_at: str | None = None
    resolved_at: str | None = None
    opened_at: str | None = None
    opened_offset_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class LogSnapshot:
    """Structured result returned for every read, including empty states."""

    world: WorldInfo = WorldInfo()
    media: MediaInfo = MediaInfo()

    def to_dict(self) -> dict[str, dict[str, str | None]]:
        """Return a JSON-compatible representation."""
        return asdict(self)


def default_log_directory() -> Path:
    """Return VRChat's standard Windows log directory without hardcoded users."""
    user_profile = os.environ.get("USERPROFILE")
    base = Path(user_profile) if user_profile else Path.home()
    return base / "AppData" / "LocalLow" / "VRChat" / "VRChat"


def find_newest_log(log_directory: Path | None = None) -> Path | None:
    """Find the most recently modified VRChat output log."""
    directory = log_directory or default_log_directory()
    try:
        logs = (path for path in directory.glob(LOG_GLOB) if path.is_file())
        return max(
            logs,
            key=lambda path: (path.stat().st_mtime_ns, path.name),
            default=None,
        )
    except OSError:
        return None


def _read_from_latest_world(path: Path, chunk_size: int = DEFAULT_TAIL_CHUNK_SIZE) -> str:
    """Read backward in chunks, stopping once the latest world entry is included."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")

    marker = re.compile(rb"Entering\s+Room\s*:", re.IGNORECASE)
    with path.open("rb") as log:
        log.seek(0, os.SEEK_END)
        position = log.tell()
        tail = b""
        while position:
            amount = min(chunk_size, position)
            position -= amount
            log.seek(position)
            tail = log.read(amount) + tail
            if marker.search(tail):
                break
    return tail.decode("utf-8", errors="replace")


def _line_timestamp(line: str) -> str | None:
    """Return a VRChat local log timestamp in ISO 8601 form, if present."""
    match = _TIMESTAMP_RE.match(line)
    if match is None:
        return None
    date = match.group("date").replace(".", "-")
    time = match.group("time").replace(",", ".")
    return f"{date}T{time}"


def parse_log_tail(text: str) -> LogSnapshot:
    """Parse text containing the latest world entry and all later log lines."""
    lines = text.splitlines()
    world_index: int | None = None
    world_name: str | None = None
    for index in range(len(lines) - 1, -1, -1):
        match = _WORLD_RE.search(lines[index])
        if match:
            world_index = index
            world_name = match.group("name")
            break

    if world_index is None:
        return LogSnapshot()

    world_id = None
    instance_id = None
    original_url = None
    resolved_url = None
    player_type = None
    requested_at = None
    resolved_at = None
    opened_at = None
    opened_offset_seconds = None

    for line in lines[world_index + 1 :]:
        timestamp = _line_timestamp(line)
        if join := _JOIN_RE.search(line):
            world_id = join.group("world")
            instance_id = join.group("instance")

        if resolved := _RESOLVED_RE.search(line):
            new_original_url = resolved.group("original")
            if original_url != new_original_url:
                requested_at = None
            original_url = new_original_url
            resolved_url = resolved.group("resolved")
            player_type = None
            resolved_at = timestamp
            opened_at = None
            opened_offset_seconds = None
            continue

        attempt = _ATTEMPT_RE.search(line) or _RESOLVING_RE.search(line)
        if attempt:
            original_url = attempt.group("url")
            resolved_url = None
            player_type = None
            requested_at = timestamp
            resolved_at = None
            opened_at = None
            opened_offset_seconds = None
            continue

        if opening := _AVPRO_RE.search(line):
            opened_url = opening.group("url").strip().strip("'\"")
            if original_url is None or opened_url not in {original_url, resolved_url}:
                original_url = opened_url
                requested_at = None
                resolved_at = None
            resolved_url = opened_url
            player_type = "AVPro"
            opened_at = timestamp
            offset = opening.group("offset")
            opened_offset_seconds = float(offset) if offset is not None else None

    group_id, instance_type = parse_instance_details(instance_id)
    return LogSnapshot(
        world=WorldInfo(
            name=world_name,
            world_id=world_id,
            instance_id=instance_id,
            group_id=group_id,
            instance_type=instance_type,
        ),
        media=MediaInfo(
            original_url,
            resolved_url,
            player_type,
            requested_at,
            resolved_at,
            opened_at,
            opened_offset_seconds,
        ),
    )


def read_current_state(log_directory: Path | None = None) -> LogSnapshot:
    """Read one on-demand snapshot; never starts a watcher or retains log content."""
    path = find_newest_log(log_directory)
    if path is None:
        return LogSnapshot()
    try:
        return parse_log_tail(_read_from_latest_world(path))
    except OSError:
        return LogSnapshot()


def parse_instance_details(instance_id: str | None) -> tuple[str | None, str | None]:
    """Extract the group ID and human-readable access type from an instance ID."""

    if not instance_id:
        return None, None
    values = {
        match.group("key").casefold(): match.group("value")
        for match in _INSTANCE_VALUE_RE.finditer(instance_id)
    }
    group_id = values.get("group")
    if group_id:
        group_access = values.get("groupaccesstype", "").casefold()
        label = {
            "public": "Group Public",
            "plus": "Group+",
            "members": "Group Members",
        }.get(group_access, "Group Instance")
        return group_id, label

    normalized = instance_id.casefold()
    if "~private(" in normalized:
        return None, "Invite+" if "~canrequestinvite" in normalized else "Invite Only"
    if "~friends(" in normalized:
        return None, "Friends"
    if "~hidden(" in normalized:
        return None, "Friends+"
    return None, "Public"
