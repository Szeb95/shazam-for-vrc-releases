import pytest

from shazam_for_vrc.application import ListeningResultKind
from shazam_for_vrc.output import ChatboxTrackContext
from shazam_for_vrc.ui.app import _chatbox_track_context, _mousewheel_scroll_units


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (120, -1),
        (-120, 1),
        (240, -2),
        (-240, 2),
        (30, -1),
        (-30, 1),
        (0, 0),
    ],
)
def test_mousewheel_scroll_units(delta: int, expected: int) -> None:
    assert _mousewheel_scroll_units(delta) == expected


@pytest.mark.parametrize(
    ("result_kind", "media_title", "expected"),
    [
        (
            ListeningResultKind.LIVE,
            "Live mix title",
            ChatboxTrackContext.LIVE_STREAM,
        ),
        (
            ListeningResultKind.TRACK,
            "Recognized mix title",
            ChatboxTrackContext.MIX_TRACK,
        ),
        (ListeningResultKind.TRACK, None, ChatboxTrackContext.TRACK),
    ],
)
def test_chatbox_wording_context(
    result_kind: ListeningResultKind,
    media_title: str | None,
    expected: ChatboxTrackContext,
) -> None:
    assert _chatbox_track_context(result_kind, media_title) is expected
