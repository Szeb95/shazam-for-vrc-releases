from collections.abc import Mapping

import pytest

from shazam_for_vrc.streams.resolver import (
    ContentKind,
    MetadataConfidence,
    NoPlayableAudioError,
    ResolutionSource,
    ResolutionTimeoutError,
    ResolvedStream,
    UnsupportedMediaError,
    resolve_stream,
)
from shazam_for_vrc.streams.stream_detector import PlaybackType, Provider, Transport
from shazam_for_vrc.vrchat.player_tracker import MediaCandidate, SelectionReason


def candidate(
    original_url: str | None,
    resolved_url: str | None = None,
) -> MediaCandidate:
    return MediaCandidate(
        original_url=original_url,
        resolved_url=resolved_url,
        player_type="AVPro" if resolved_url else None,
        requested_at=None,
        resolved_at=None,
        opened_at=None,
        opened_offset_seconds=None,
        reason=SelectionReason.LATEST_AVPRO_OPEN
        if resolved_url
        else SelectionReason.LATEST_REQUEST,
    )


def test_resolves_fresh_live_audio_and_preserves_headers() -> None:
    seen: list[tuple[str, float]] = []

    def extract(url: str, timeout: float) -> Mapping[str, object]:
        seen.append((url, timeout))
        return {
            "url": "https://fresh.example/live/audio.m3u8?token=new-secret",
            "acodec": "aac",
            "vcodec": "none",
            "is_live": True,
            "http_headers": {"Referer": "https://www.youtube.com/", "X-Number": 4},
        }

    result = resolve_stream(
        candidate(
            "https://www.youtube.com/watch?v=live",
            "https://manifest.googlevideo.com/api/manifest/hls_playlist/old.m3u8",
        ),
        timeout_seconds=7.5,
        metadata_extractor=extract,
    )

    assert seen == [("https://www.youtube.com/watch?v=live", 7.5)]
    assert result.provider is Provider.YOUTUBE
    assert result.transport is Transport.HLS
    assert result.playback_type is PlaybackType.LIVE
    assert result.resolution_source is ResolutionSource.YT_DLP
    assert result.supports_current_audio
    assert result.http_headers == {
        "Referer": "https://www.youtube.com/",
        "X-Number": "4",
    }
    assert "new-secret" not in repr(result)
    assert "youtube.com/watch" not in repr(result)


def test_prefers_vrchat_logged_hls_for_confirmed_twitch_live() -> None:
    logged_url = "https://playlist.ttvnw.net/live/channel-token.m3u8"

    result = resolve_stream(
        candidate("https://www.twitch.tv/torb_music", logged_url),
        metadata_extractor=lambda _url, _timeout: {
            "url": "https://fresh.ttvnw.net/audio-only.m3u8",
            "acodec": "aac",
            "vcodec": "none",
            "is_live": True,
        },
    )

    assert result.stream_url == logged_url
    assert result.provider is Provider.TWITCH
    assert result.transport is Transport.HLS
    assert result.playback_type is PlaybackType.LIVE
    assert result.resolution_source is ResolutionSource.VRCHAT_LOG
    assert result.supports_current_audio


def test_does_not_trust_logged_twitch_hls_when_metadata_is_not_live() -> None:
    fresh_url = "https://fresh.ttvnw.net/recording.m3u8"

    result = resolve_stream(
        candidate(
            "https://www.twitch.tv/videos/12345",
            "https://playlist.ttvnw.net/vod/token.m3u8",
        ),
        metadata_extractor=lambda _url, _timeout: {
            "url": fresh_url,
            "acodec": "aac",
            "vcodec": "none",
            "duration": 300,
        },
    )

    assert result.stream_url == fresh_url
    assert result.playback_type is PlaybackType.PRERECORDED
    assert result.resolution_source is ResolutionSource.YT_DLP


def test_resolves_vrcdn_rtspt_as_tcp_rtsp_without_yt_dlp() -> None:
    def must_not_extract(_url: str, _timeout: float) -> Mapping[str, object]:
        raise AssertionError("Direct VRCDN streams must not invoke yt-dlp")

    result = resolve_stream(
        candidate("rtspt://stream.vrcdn.live/live/szeb"),
        metadata_extractor=must_not_extract,
    )

    assert result.stream_url == "rtsp://stream.vrcdn.live/live/szeb"
    assert result.provider is Provider.VRCDN
    assert result.transport is Transport.RTSP
    assert result.playback_type is PlaybackType.LIVE
    assert result.resolution_source is ResolutionSource.VRCHAT_LOG
    assert result.rtsp_transport == "tcp"
    assert result.supports_current_audio


def test_resolves_vrcdn_https_transport_stream_without_yt_dlp() -> None:
    def must_not_extract(_url: str, _timeout: float) -> Mapping[str, object]:
        raise AssertionError("Direct VRCDN streams must not invoke yt-dlp")

    result = resolve_stream(
        candidate("https://stream.vrcdn.live/live/szeb.live.ts"),
        metadata_extractor=must_not_extract,
    )

    assert result.stream_url == "https://stream.vrcdn.live/live/szeb.live.ts"
    assert result.provider is Provider.VRCDN
    assert result.transport is Transport.DIRECT_MEDIA
    assert result.playback_type is PlaybackType.LIVE
    assert result.rtsp_transport is None
    assert result.supports_current_audio


def test_prerecorded_media_is_not_marked_as_current_audio() -> None:
    result = resolve_stream(
        candidate("https://www.youtube.com/watch?v=recording"),
        metadata_extractor=lambda _url, _timeout: {
            "url": "https://cdn.example/recording.m4a",
            "acodec": "aac",
            "vcodec": "none",
            "live_status": "not_live",
            "duration": 4400.157,
        },
    )

    assert result.playback_type is PlaybackType.PRERECORDED
    assert result.duration_seconds == pytest.approx(4400.157)
    assert not result.supports_current_audio


def test_preserves_exact_track_metadata_for_single_song_video() -> None:
    result = resolve_stream(
        candidate("https://www.youtube.com/watch?v=song"),
        metadata_extractor=lambda _url, _timeout: {
            "url": "https://cdn.example/song.m4a",
            "acodec": "aac",
            "vcodec": "none",
            "duration": 242,
            "title": "Example upload title",
            "track": "Digital Love",
            "artist": "Daft Punk",
            "album": "Discovery",
        },
    )

    assert result.metadata is not None
    assert result.metadata.track == "Digital Love"
    assert result.metadata.artist == "Daft Punk"
    assert result.metadata.album == "Discovery"
    assert result.metadata.content_kind is ContentKind.SINGLE_TRACK
    assert result.metadata.confidence is MetadataConfidence.HIGH


def test_classifies_long_mix_from_duration_and_preserves_chapters() -> None:
    result = resolve_stream(
        candidate("https://www.youtube.com/watch?v=mix"),
        metadata_extractor=lambda _url, _timeout: {
            "url": "https://cdn.example/mix.m4a",
            "acodec": "aac",
            "vcodec": "none",
            "duration": 3600,
            "title": "Late Night Music",
            "chapters": [
                {"title": "First song", "start_time": 0, "end_time": 180},
                {"title": "Second song", "start_time": 180, "end_time": 400},
            ],
        },
    )

    assert result.metadata is not None
    assert result.metadata.content_kind is ContentKind.LONG_FORM
    assert result.metadata.confidence is MetadataConfidence.HIGH
    assert [chapter.title for chapter in result.metadata.chapters] == [
        "First song",
        "Second song",
    ]


def test_remix_title_is_not_mistaken_for_a_long_mix() -> None:
    result = resolve_stream(
        candidate("https://www.youtube.com/watch?v=remix"),
        metadata_extractor=lambda _url, _timeout: {
            "url": "https://cdn.example/remix.m4a",
            "acodec": "aac",
            "duration": 240,
            "title": "Example Song (Club Remix)",
        },
    )

    assert result.metadata is not None
    assert result.metadata.content_kind is ContentKind.SINGLE_TRACK
    assert result.metadata.confidence is MetadataConfidence.MEDIUM


@pytest.mark.parametrize(
    ("live_status", "expected"),
    [
        ("is_upcoming", PlaybackType.UPCOMING),
        ("post_live", PlaybackType.ENDED),
        ("was_live", PlaybackType.PRERECORDED),
    ],
)
def test_normalizes_non_live_statuses(live_status: str, expected: PlaybackType) -> None:
    result = resolve_stream(
        candidate("https://www.youtube.com/watch?v=status"),
        metadata_extractor=lambda _url, _timeout: {
            "url": "https://cdn.example/media.m4a",
            "acodec": "aac",
            "live_status": live_status,
        },
    )

    assert result.playback_type is expected


def test_unwraps_single_video_from_playlist_metadata() -> None:
    result = resolve_stream(
        candidate("https://www.youtube.com/watch?v=one&list=mix"),
        metadata_extractor=lambda _url, _timeout: {
            "_type": "playlist",
            "entries": [
                {
                    "url": "https://cdn.example/one.m4a",
                    "acodec": "aac",
                    "duration": 120,
                },
                {"url": "https://cdn.example/two.m4a", "acodec": "aac"},
            ],
        },
    )

    assert result.duration_seconds == 120
    assert result.playback_type is PlaybackType.PRERECORDED


def test_selects_best_audio_only_format() -> None:
    result = resolve_stream(
        candidate("https://media.example/watch/123"),
        metadata_extractor=lambda _url, _timeout: {
            "formats": [
                {
                    "url": "https://cdn.example/video.mp4",
                    "acodec": "aac",
                    "vcodec": "h264",
                    "tbr": 2000,
                },
                {
                    "url": "https://cdn.example/low.m4a",
                    "acodec": "aac",
                    "vcodec": "none",
                    "abr": 64,
                },
                {
                    "url": "https://cdn.example/high.m4a",
                    "acodec": "aac",
                    "vcodec": "none",
                    "abr": 128,
                    "http_headers": {"Origin": "https://media.example"},
                },
            ],
            "is_live": True,
        },
    )

    assert result.stream_url == "https://cdn.example/high.m4a"
    assert result.transport is Transport.DIRECT_MEDIA
    assert result.http_headers["Origin"] == "https://media.example"


def test_uses_logged_direct_url_when_metadata_times_out() -> None:
    def time_out(_url: str, _timeout: float) -> Mapping[str, object]:
        raise TimeoutError("connection included sensitive-url-token")

    result = resolve_stream(
        candidate(
            "https://www.youtube.com/watch?v=live",
            "https://manifest.googlevideo.com/api/manifest/hls_playlist/index.m3u8?secret=x",
        ),
        metadata_extractor=time_out,
    )

    assert result.resolution_source is ResolutionSource.VRCHAT_LOG
    assert result.playback_type is PlaybackType.UNKNOWN
    assert result.transport is Transport.HLS
    assert not result.supports_current_audio


def test_timeout_without_fallback_uses_safe_error_message() -> None:
    def time_out(_url: str, _timeout: float) -> Mapping[str, object]:
        raise TimeoutError("https://secret.example/?token=do-not-repeat")

    with pytest.raises(ResolutionTimeoutError) as caught:
        resolve_stream(
            candidate("https://secret.example/watch?token=also-secret"),
            metadata_extractor=time_out,
        )

    assert "secret" not in str(caught.value)
    assert "token" not in str(caught.value)


def test_maps_unsupported_provider_without_exposing_url() -> None:
    def unsupported(_url: str, _timeout: float) -> Mapping[str, object]:
        raise RuntimeError("Unsupported URL: https://private.example/?key=secret")

    with pytest.raises(UnsupportedMediaError, match="provider is not supported"):
        resolve_stream(
            candidate("https://private.example/?key=secret"),
            metadata_extractor=unsupported,
        )


def test_reports_metadata_without_playable_audio() -> None:
    with pytest.raises(NoPlayableAudioError, match="playable audio"):
        resolve_stream(
            candidate("https://media.example/watch/123"),
            metadata_extractor=lambda _url, _timeout: {
                "formats": [
                    {
                        "url": "https://cdn.example/video-only.mp4",
                        "acodec": "none",
                        "vcodec": "h264",
                    }
                ]
            },
        )


def test_resolved_stream_headers_are_immutable() -> None:
    result = ResolvedStream(
        original_url="https://example.test/page",
        stream_url="https://example.test/audio.mp3",
        provider=Provider.UNKNOWN,
        transport=Transport.DIRECT_MEDIA,
        playback_type=PlaybackType.UNKNOWN,
        resolution_source=ResolutionSource.YT_DLP,
        http_headers={"Referer": "https://example.test"},
    )

    with pytest.raises(TypeError):
        result.http_headers["Referer"] = "changed"  # type: ignore[index]
