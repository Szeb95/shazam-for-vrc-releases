# VRChat Log Reader

## Purpose

`shazam_for_vrc.vrchat.log_reader` takes a single, on-demand snapshot of the newest
VRChat output log. It returns the current world and the most recent media activity
after entry into that world. It has no watcher, VRCX, VRChat API, UI, capture, or
recognition dependency.

## Log location and reading behavior

On Windows, VRChat normally writes logs to:

`%USERPROFILE%\AppData\LocalLow\VRChat\VRChat\output_log_*.txt`

The newest file is selected by modification time. The reader starts at the end in
64 KiB chunks and stops after finding the latest `Entering Room:` marker. Thus it
normally avoids reading the complete log. If no marker exists, checking the whole
file is necessary. Text is decoded as UTF-8 with replacement for damaged bytes.

## Parsed patterns

Patterns are case-insensitive and tolerate whitespace changes around stable phrases:

- `[Behaviour] Entering Room: World Name`
- `[Behaviour] Joining wrld_<uuid>:<instance>`
- `[Video Playback] Attempting to resolve URL '<url>'`
- `[Video Playback] Resolving URL '<url>'`
- `[Video Playback] URL '<original>' resolved to '<direct>'`
- `[AVProVideo] Opening <url> (offset 0) ...`
- `[AVProVideo] Opening <url>, reload: False.`

Quoted URLs are captured between matching single or double quotes, without URL
decoding, so query strings and unusual characters are preserved.

## Return value

`read_current_state()` always returns a `LogSnapshot`:

```python
{
    "world": {"name": "Example World", "world_id": "wrld_...", "instance_id": "12345~region(eu)"},
    "media": {
        "original_url": "https://youtube.com/watch?v=...",
        "resolved_url": "https://cdn.example/playlist.m3u8",
        "player_type": "AVPro",
        "requested_at": "2026-08-20T13:01:00",
        "resolved_at": "2026-08-20T13:01:01",
        "opened_at": "2026-08-20T13:01:02",
    },
}
```

Use `snapshot.to_dict()` for the dictionary form. Unavailable values are `None`.
No log, an unreadable log, no world entry, and no media activity are safe empty states.
Media timestamps come from the beginning of the corresponding VRChat log line and
are returned as ISO 8601 local times without a UTC offset. `requested_at` describes
the latest resolution request, `resolved_at` its successful resolution, and
`opened_at` the AVPro opening event. Missing event timestamps remain `None`.

## Selection rules and limitations

Only lines after the latest world entry are considered. The latest resolve attempt,
resolution, or AVPro opening wins. A new failed attempt clears an older resolved URL,
preventing a stale stream from being reported. If several players log activity, the
format does not expose a stable player identity, so this version cannot determine
which screen is audible or still playing; it reports the most recent activity.

VRChat's output log is not a documented stable API. World IDs are taken from the
`Joining` line that normally follows room entry. Player stop/pause state and Unity
Video player identity are not reliably exposed by the currently supported patterns.
A raw AVPro opening is treated as both original and resolved when no preceding
resolution event exists.

The log does not reliably expose current playback position. The reader intentionally
does not estimate it from `opened_at`, because buffering, seeking, pausing, and
network synchronization would make that value inaccurate.

## Testing

Install the package in development mode, then run:

```powershell
python -m pytest
python .\log_reader_2026-08-20_13-47-41.py
```

The second command reads the real default log directory once and prints the result.
Tests use synthetic, non-personal log excerpts; real VRChat logs must not be committed.
