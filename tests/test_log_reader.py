from pathlib import Path

from shazam_for_vrc.vrchat.log_reader import (
    LogSnapshot,
    find_newest_log,
    parse_log_tail,
    read_current_state,
)


def test_parses_only_media_after_latest_world() -> None:
    result = parse_log_tail(
        """2026.08.20 12:00:00 Log - [Behaviour] Entering Room: Old World
2026.08.20 12:00:01 Log - [Video Playback] URL 'old' resolved to 'old.m3u8'
2026.08.20 13:00:00 Log - [Behaviour] Entering Room: Example Club
2026.08.20 13:00:00 Log - [Behaviour] Joining \
wrld_12345678-abcd-abcd-abcd-123456789abc:42~region(eu)
2026.08.20 13:01:00 Log - [Video Playback] Attempting to resolve URL \
'https://example.test/watch?v=a&x=one%20two'
2026.08.20 13:01:01 Log - [Video Playback] URL \
'https://example.test/watch?v=a&x=one%20two' resolved to \
'https://cdn.test/live/index.m3u8?token=a%2Fb'
2026.08.20 13:01:02 Log - [AVProVideo] Opening \
https://cdn.test/live/index.m3u8?token=a%2Fb (offset 0) with API MediaFoundation
"""
    )
    assert result.world.name == "Example Club"
    assert result.world.world_id == "wrld_12345678-abcd-abcd-abcd-123456789abc"
    assert result.world.instance_id == "42~region(eu)"
    assert result.world.group_id is None
    assert result.world.instance_type == "Public"
    assert result.media.original_url == "https://example.test/watch?v=a&x=one%20two"
    assert result.media.resolved_url == "https://cdn.test/live/index.m3u8?token=a%2Fb"
    assert result.media.player_type == "AVPro"
    assert result.media.requested_at == "2026-08-20T13:01:00"
    assert result.media.resolved_at == "2026-08-20T13:01:01"
    assert result.media.opened_at == "2026-08-20T13:01:02"
    assert result.media.opened_offset_seconds == 0


def test_latest_failed_attempt_does_not_return_previous_stream() -> None:
    result = parse_log_tail(
        """[Behaviour] Entering Room: Club
[Video Playback] URL 'first' resolved to 'first.m3u8'
[AVProVideo] Opening first.m3u8 (offset 0) with API MediaFoundation
[Video Playback] Attempting to resolve URL "https://example.test/fails?q='quoted'"
[Video Playback] ERROR: Unsupported URL
"""
    )
    assert result.media.original_url == "https://example.test/fails?q='quoted'"
    assert result.media.resolved_url is None
    assert result.media.player_type is None
    assert result.media.resolved_at is None
    assert result.media.opened_at is None


def test_parses_fractional_timestamp_and_resets_unrelated_opening() -> None:
    result = parse_log_tail(
        """2026.08.20 13:00:00 Log - [Behaviour] Entering Room: Club
2026.08.20 13:01:00 Log - [Video Playback] URL 'old' resolved to 'old.m3u8'
2026.08.20 13:02:03.456 Log - [AVProVideo] Opening new.mp4 (offset 0) with API MediaFoundation
"""
    )
    assert result.media.original_url == "new.mp4"
    assert result.media.resolved_url == "new.mp4"
    assert result.media.requested_at is None
    assert result.media.resolved_at is None
    assert result.media.opened_at == "2026-08-20T13:02:03.456"


def test_preserves_nonzero_avpro_open_offset() -> None:
    result = parse_log_tail(
        """2026.08.20 13:00:00 Log - [Behaviour] Entering Room: Club
2026.08.20 13:02:03 Log - [AVProVideo] Opening https://media.test/mix.mp4 \
(offset 91.5) with API MediaFoundation
"""
    )

    assert result.media.opened_offset_seconds == 91.5


def test_empty_states_are_safe(tmp_path: Path) -> None:
    assert read_current_state(tmp_path) == LogSnapshot()
    log = tmp_path / "output_log_2026-08-20_13-00-00.txt"
    log.write_text("no room event\n", encoding="utf-8")
    assert read_current_state(tmp_path) == LogSnapshot()


def test_finds_newest_log_by_modification_time(tmp_path: Path) -> None:
    older = tmp_path / "output_log_2026-08-20_10-00-00.txt"
    newer = tmp_path / "output_log_2026-08-20_11-00-00.txt"
    older.write_text("old", encoding="utf-8")
    newer.write_text("new", encoding="utf-8")
    older.touch()
    newer.touch()
    assert find_newest_log(tmp_path) == newer


def test_reader_scans_back_across_chunks(tmp_path: Path) -> None:
    log = tmp_path / "output_log_2026-08-20_13-00-00.txt"
    log.write_text(
        "ignored\n" * 10_000
        + "[Behaviour] Entering Room: Chunky World\n"
        + "noise\n" * 10_000
        + "[AVProVideo] Opening https://media.test/file.mp4, reload: False.\n",
        encoding="utf-8",
    )
    result = read_current_state(tmp_path)
    assert result.world.name == "Chunky World"
    assert result.media.resolved_url == "https://media.test/file.mp4"


def test_extracts_group_and_instance_access_type() -> None:
    result = parse_log_tail(
        """[Behaviour] Entering Room: Group Club
[Behaviour] Joining wrld_12345678-abcd-abcd-abcd-123456789abc:\
42~group(grp_example)~groupAccessType(plus)~region(eu)
"""
    )

    assert result.world.group_id == "grp_example"
    assert result.world.instance_type == "Group+"
