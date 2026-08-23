import pytest

from shazam_for_vrc.output.osc import (
    VRCHAT_CHATBOX_MAX_CHARACTERS,
    ChatboxOutputError,
    ChatboxTrackContext,
    VRChatOscChatbox,
    format_mix_message,
    format_track_message,
    provider_stream_name,
)
from shazam_for_vrc.streams.stream_detector import Provider


def test_vrchat_chatbox_sends_track_to_default_osc_input() -> None:
    sent: list[tuple[bytes, tuple[str, int]]] = []
    chatbox = VRChatOscChatbox(
        datagram_sender=lambda payload, address: sent.append((payload, address))
    )

    chatbox.send_track(
        "Daft Punk",
        "Around the World",
        provider=Provider.TWITCH,
        context=ChatboxTrackContext.LIVE_STREAM,
    )

    assert len(sent) == 1
    payload, address = sent[0]
    assert address == ("127.0.0.1", 9000)
    assert payload == (
        b"/chatbox/input\0\0,sTF\0\0\0\0"
        b"Twitch stream song: Around the World \xe2\x80\x94 Daft Punk\0\0"
    )
    assert len(payload) % 4 == 0


def test_track_message_is_single_line_and_limited_to_vrchat_maximum() -> None:
    message = format_track_message(
        "  Example\nArtist ",
        "A" * 200,
        provider=Provider.VRCDN,
        context=ChatboxTrackContext.LIVE_STREAM,
    )

    assert message.startswith("VRCDN stream song: ")
    assert "\n" not in message
    assert len(message) == VRCHAT_CHATBOX_MAX_CHARACTERS
    assert message.endswith("…")


def test_vrchat_chatbox_send_error_is_normalized() -> None:
    def fail(_payload: bytes, _address: tuple[str, int]) -> None:
        raise OSError("test failure")

    chatbox = VRChatOscChatbox(datagram_sender=fail)

    with pytest.raises(ChatboxOutputError, match="could not be sent"):
        chatbox.send_track(
            "Artist",
            "Title",
            provider=Provider.YOUTUBE,
            context=ChatboxTrackContext.TRACK,
        )


@pytest.mark.parametrize(
    ("artist", "title"),
    [
        ("", "Title"),
        ("Artist", ""),
        (1, "Title"),
    ],
)
def test_track_message_rejects_missing_metadata(artist: object, title: object) -> None:
    with pytest.raises(ValueError):
        format_track_message(  # type: ignore[arg-type]
            artist,
            title,
            provider=Provider.TWITCH,
            context=ChatboxTrackContext.LIVE_STREAM,
        )


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        (Provider.TWITCH, "Twitch"),
        (Provider.YOUTUBE, "YouTube"),
        (Provider.VRCDN, "VRCDN"),
        (Provider.DIRECT, "World"),
        (Provider.UNKNOWN, "World"),
    ],
)
def test_provider_stream_names(provider: Provider, expected: str) -> None:
    assert provider_stream_name(provider) == expected


def test_track_message_formats_each_playback_context() -> None:
    assert format_track_message(
        "Artist",
        "Title",
        provider=Provider.TWITCH,
        context=ChatboxTrackContext.LIVE_STREAM,
    ) == (
        "Twitch stream song: Title — Artist"
    )
    assert format_track_message(
        "Artist",
        "Title",
        provider=Provider.YOUTUBE,
        context=ChatboxTrackContext.LIVE_STREAM,
    ) == (
        "YouTube stream song: Title — Artist"
    )
    assert format_track_message(
        "Artist",
        "Title",
        provider=Provider.VRCDN,
        context=ChatboxTrackContext.LIVE_STREAM,
    ) == (
        "VRCDN stream song: Title — Artist"
    )
    assert format_track_message(
        "Artist",
        "Title",
        provider=Provider.DIRECT,
        context=ChatboxTrackContext.LIVE_STREAM,
    ) == (
        "World stream song: Title — Artist"
    )
    assert format_track_message(
        "Artist",
        "Title",
        provider=Provider.YOUTUBE,
        context=ChatboxTrackContext.MIX_TRACK,
    ) == "Song in mix: Title — Artist"
    assert format_track_message(
        "Artist",
        "Title",
        provider=Provider.YOUTUBE,
        context=ChatboxTrackContext.TRACK,
    ) == "YouTube song: Title — Artist"


def test_whole_mix_output_uses_only_mix_title() -> None:
    sent: list[bytes] = []
    chatbox = VRChatOscChatbox(datagram_sender=lambda payload, _address: sent.append(payload))

    chatbox.send_mix("Long mix")

    assert format_mix_message("Long mix") == "Mix: Long mix"
    assert b"Mix:" in sent[0]
    assert b"YouTube" not in sent[0]
