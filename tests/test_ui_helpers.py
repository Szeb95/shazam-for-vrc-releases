from unittest.mock import Mock

import pytest

from shazam_for_vrc.application import ListeningResultKind
from shazam_for_vrc.output import ChatboxTrackContext
from shazam_for_vrc.ui.app import (
    OverlayApp,
    _chatbox_track_context,
    _DebouncedCanvasLayout,
    _mousewheel_scroll_units,
)


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


def test_minimize_to_taskbar_keeps_app_running() -> None:
    app = object.__new__(OverlayApp)
    app.root = Mock()

    app._minimize_to_taskbar()

    app.root.iconify.assert_called_once_with()


def test_canvas_resize_is_coalesced_and_uses_latest_width() -> None:
    canvas = Mock()
    canvas.after.return_value = "resize-1"
    layout = _DebouncedCanvasLayout(canvas, 42)

    layout.request_width(Mock(width=800))
    layout.request_width(Mock(width=620))

    canvas.after.assert_called_once()
    layout._flush_width()
    canvas.itemconfigure.assert_called_once_with(42, width=620)


def test_canvas_scrollregion_updates_are_coalesced() -> None:
    canvas = Mock()
    canvas.after.return_value = "scroll-1"
    canvas.bbox.return_value = (0, 0, 620, 1200)
    layout = _DebouncedCanvasLayout(canvas, 42)

    layout.request_scrollregion()
    layout.request_scrollregion()

    canvas.after.assert_called_once()
    layout._flush_scrollregion()
    canvas.configure.assert_called_once_with(scrollregion=(0, 0, 620, 1200))


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
