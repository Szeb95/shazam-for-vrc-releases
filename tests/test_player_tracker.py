import pytest

from shazam_for_vrc.vrchat.log_reader import LogSnapshot, MediaInfo
from shazam_for_vrc.vrchat.player_tracker import (
    NoActiveMediaError,
    SelectionReason,
    select_active_media,
)


def test_prefers_latest_avpro_resolved_url() -> None:
    snapshot = LogSnapshot(
        media=MediaInfo(
            original_url="https://www.youtube.com/watch?v=track",
            resolved_url="https://manifest.googlevideo.com/live/index.m3u8?token=secret",
            player_type="AVPro",
            requested_at="2026-08-20T14:16:32",
            resolved_at="2026-08-20T14:16:37",
            opened_at="2026-08-20T14:16:37",
            opened_offset_seconds=42,
        )
    )

    candidate = select_active_media(snapshot)

    assert candidate.preferred_url.endswith("index.m3u8?token=secret")
    assert candidate.original_url == "https://www.youtube.com/watch?v=track"
    assert candidate.opened_offset_seconds == 42
    assert candidate.reason is SelectionReason.LATEST_AVPRO_OPEN


def test_uses_original_url_while_resolution_is_pending() -> None:
    snapshot = LogSnapshot(
        media=MediaInfo(
            original_url="https://www.twitch.tv/example",
            requested_at="2026-08-20T14:16:32",
        )
    )

    candidate = select_active_media(snapshot)

    assert candidate.preferred_url == "https://www.twitch.tv/example"
    assert candidate.reason is SelectionReason.LATEST_REQUEST


def test_reports_empty_snapshot() -> None:
    with pytest.raises(NoActiveMediaError, match="No media URL"):
        select_active_media(LogSnapshot())


def test_whitespace_only_urls_are_not_candidates() -> None:
    snapshot = LogSnapshot(media=MediaInfo(original_url="  ", resolved_url="\t"))

    with pytest.raises(NoActiveMediaError):
        select_active_media(snapshot)
