import pytest

from shazam_for_vrc.streams.stream_detector import (
    InvalidMediaUrl,
    Provider,
    Transport,
    classify_url,
)


@pytest.mark.parametrize(
    ("url", "provider", "transport"),
    [
        (
            "https://www.youtube.com/watch?v=abc&list=RDabc",
            Provider.YOUTUBE,
            Transport.WEBPAGE,
        ),
        ("https://youtu.be/abc", Provider.YOUTUBE, Transport.WEBPAGE),
        ("https://www.twitch.tv/example", Provider.TWITCH, Transport.WEBPAGE),
        ("rtspt://stream.vrcdn.live/live/szeb", Provider.VRCDN, Transport.RTSP),
        (
            "https://stream.vrcdn.live/live/szeb.live.ts",
            Provider.VRCDN,
            Transport.DIRECT_MEDIA,
        ),
        (
            "https://manifest.googlevideo.com/api/manifest/hls_playlist/index.m3u8?token=a%2Fb",
            Provider.YOUTUBE,
            Transport.HLS,
        ),
        (
            "https://rr3---sn.example.googlevideo.com/videoplayback?expire=123&token=x",
            Provider.YOUTUBE,
            Transport.DIRECT_MEDIA,
        ),
        ("https://cdn.example/live/index.M3U8?token=x", Provider.DIRECT, Transport.HLS),
        ("https://cdn.example/manifest.mpd", Provider.DIRECT, Transport.DASH),
        ("https://radio.example/current.mp3", Provider.DIRECT, Transport.DIRECT_MEDIA),
        ("https://media.example/watch/123", Provider.UNKNOWN, Transport.WEBPAGE),
    ],
)
def test_classifies_provider_and_transport(
    url: str, provider: Provider, transport: Transport
) -> None:
    result = classify_url(url)

    assert result.provider is provider
    assert result.transport is transport


@pytest.mark.parametrize(
    "url",
    [
        "file:///C:/private/audio.mp3",
        "pipe:0",
        "concat:https://one|https://two",
        "javascript:alert(1)",
        "https:///missing-host.m3u8",
        "rtspt://untrusted.example/live/channel",
    ],
)
def test_rejects_non_network_or_invalid_urls(url: str) -> None:
    with pytest.raises(InvalidMediaUrl):
        classify_url(url)


def test_hls_transport_does_not_claim_media_is_live() -> None:
    result = classify_url("https://cdn.example/recording.m3u8")

    assert result.transport is Transport.HLS
    assert not hasattr(result, "playback_type")
