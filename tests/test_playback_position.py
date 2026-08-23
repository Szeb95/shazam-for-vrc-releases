from datetime import datetime

import pytest

from shazam_for_vrc.streams.playback_position import (
    PositionSource,
    estimate_playback_position,
    format_media_time,
    media_start_offset,
)
from shazam_for_vrc.streams.resolver import ResolutionSource, ResolvedStream
from shazam_for_vrc.streams.stream_detector import PlaybackType, Provider, Transport
from shazam_for_vrc.vrchat.player_tracker import MediaCandidate, SelectionReason


def candidate(
    *,
    original_url: str = "https://www.youtube.com/watch?v=mix",
    opened_at: str | None = "2026-08-21T10:00:00",
    opened_offset_seconds: float | None = 0,
) -> MediaCandidate:
    return MediaCandidate(
        original_url=original_url,
        resolved_url="https://cdn.example/mix.m4a",
        player_type="AVPro",
        requested_at=None,
        resolved_at=None,
        opened_at=opened_at,
        opened_offset_seconds=opened_offset_seconds,
        reason=SelectionReason.LATEST_AVPRO_OPEN,
    )


def stream(
    *,
    playback_type: PlaybackType = PlaybackType.PRERECORDED,
    duration_seconds: float | None = 3600,
) -> ResolvedStream:
    return ResolvedStream(
        original_url="https://www.youtube.com/watch?v=mix",
        stream_url="https://cdn.example/mix.m4a",
        provider=Provider.YOUTUBE,
        transport=Transport.DIRECT_MEDIA,
        playback_type=playback_type,
        resolution_source=ResolutionSource.YT_DLP,
        duration_seconds=duration_seconds,
    )


def test_estimates_position_from_open_time_and_url_start() -> None:
    result = estimate_playback_position(
        candidate(original_url="https://youtu.be/mix?t=1m30s"),
        stream(),
        now=datetime(2026, 8, 21, 10, 2, 5),
    )

    assert result is not None
    assert result.seconds == pytest.approx(215)
    assert result.initial_offset_seconds == 90
    assert result.elapsed_seconds == 125
    assert result.source is PositionSource.AVPRO_OPEN
    assert not result.past_reported_duration


def test_nonzero_avpro_offset_takes_priority_over_url_parameter() -> None:
    result = estimate_playback_position(
        candidate(
            original_url="https://youtu.be/mix?t=10",
            opened_offset_seconds=42.5,
        ),
        stream(),
        now=datetime(2026, 8, 21, 10, 0, 10),
    )

    assert result is not None
    assert result.seconds == pytest.approx(52.5)
    assert result.initial_offset_seconds == pytest.approx(42.5)


def test_wraps_past_reported_duration_and_marks_loop_assumption() -> None:
    result = estimate_playback_position(
        candidate(),
        stream(duration_seconds=60),
        now=datetime(2026, 8, 21, 10, 2, 0),
    )

    assert result is not None
    assert result.seconds == 0
    assert result.unclamped_seconds == 120
    assert result.past_reported_duration


def test_without_open_time_requires_a_url_offset() -> None:
    assert estimate_playback_position(candidate(opened_at=None), stream()) is None
    result = estimate_playback_position(
        candidate(original_url="https://youtu.be/mix?start=75", opened_at=None),
        stream(),
    )
    assert result is not None
    assert result.seconds == 75
    assert result.source is PositionSource.URL_START


def test_live_stream_has_no_prerecorded_position() -> None:
    assert (
        estimate_playback_position(
            candidate(),
            stream(playback_type=PlaybackType.LIVE),
        )
        is None
    )


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://youtu.be/id?t=90", 90),
        ("https://youtu.be/id?t=1h2m3s", 3723),
        ("https://youtu.be/id#t=01:30", 90),
        ("https://youtu.be/id?time_continue=12.5", 12.5),
        ("https://youtu.be/id#45s", 45),
        (None, 0),
    ],
)
def test_reads_common_media_start_offsets(url: str | None, expected: float) -> None:
    assert media_start_offset(url) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "00:00"), (65.9, "01:05"), (3661, "1:01:01"), (None, "Unknown")],
)
def test_formats_media_time(seconds: float | None, expected: str) -> None:
    assert format_media_time(seconds) == expected
