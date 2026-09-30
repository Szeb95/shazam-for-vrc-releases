import sys
import tkinter
from types import SimpleNamespace

import pytest

from shazam_for_vrc.self_test import (
    _check_ffmpeg_capabilities,
    _check_gui_runtime,
    _check_media_resolution_runtime,
    _check_openvr_runtime,
    _check_recognition_runtime,
    _check_system_audio_runtime,
    _check_tray_runtime,
    _check_vrchat_audio_runtime,
)


def test_gui_runtime_creates_hides_and_closes_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class FakeRoot:
        def withdraw(self) -> None:
            calls.append("withdraw")

        def update_idletasks(self) -> None:
            calls.append("update_idletasks")

        def destroy(self) -> None:
            calls.append("destroy")

    monkeypatch.setattr(tkinter, "Tk", FakeRoot)

    _check_gui_runtime()

    assert calls == ["withdraw", "update_idletasks", "destroy"]


def test_gui_runtime_reports_missing_tcl_data(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_tk() -> None:
        raise tkinter.TclError("private Tcl diagnostics")

    monkeypatch.setattr(tkinter, "Tk", broken_tk)

    with pytest.raises(RuntimeError, match="packaged Tkinter GUI runtime") as caught:
        _check_gui_runtime()

    assert "private Tcl diagnostics" not in str(caught.value)


def test_system_audio_runtime_requires_wasapi_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        sys.modules,
        "pyaudiowpatch",
        SimpleNamespace(PyAudio=object),
    )

    with pytest.raises(RuntimeError, match="does not support loopback"):
        _check_system_audio_runtime()


def test_vrchat_audio_runtime_requires_supported_process_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from shazam_for_vrc.streams import vrchat_audio_capture

    capture_type = SimpleNamespace(is_supported=lambda: False)
    monkeypatch.setattr(
        vrchat_audio_capture,
        "_load_backend",
        lambda: SimpleNamespace(ProcessAudioCapture=capture_type),
    )

    with pytest.raises(RuntimeError, match="does not support"):
        _check_vrchat_audio_runtime()


def test_tray_runtime_verifies_the_bundled_icon(monkeypatch: pytest.MonkeyPatch) -> None:
    from shazam_for_vrc.ui import tray

    calls: list[str] = []

    class FakeImage:
        def __enter__(self) -> "FakeImage":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def verify(self) -> None:
            calls.append("verify")

    image_module = SimpleNamespace(open=lambda _path: FakeImage())
    monkeypatch.setattr(
        tray,
        "_load_backend",
        lambda: (SimpleNamespace(Icon=object, Menu=object), image_module),
    )

    _check_tray_runtime()

    assert calls == ["verify"]


def test_openvr_runtime_requires_native_library(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "openvr",
        SimpleNamespace(
            VRApplication_Background=object(),
            VRActiveActionSet_t=object(),
            VRInput=object(),
        ),
    )

    with pytest.raises(RuntimeError, match="SteamVR component is incomplete"):
        _check_openvr_runtime()


def test_media_resolution_runtime_loads_youtube_and_twitch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[str] = []

    class FakeYoutubeDL:
        def __init__(self, options: dict[str, object]) -> None:
            assert options == {"quiet": True, "no_warnings": True}

        def get_info_extractor(self, name: str) -> object:
            requested.append(name)
            return object()

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYoutubeDL))

    _check_media_resolution_runtime()

    assert requested == ["Youtube", "TwitchStream"]


def test_recognition_runtime_requires_native_fingerprint_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class IncompleteShazam:
        def __init__(self, **_kwargs: object) -> None:
            self.recognize = lambda: None

    monkeypatch.setitem(sys.modules, "shazamio", SimpleNamespace(Shazam=IncompleteShazam))

    with pytest.raises(RuntimeError, match="recognition component is incomplete"):
        _check_recognition_runtime()


def test_ffmpeg_capability_check_reports_missing_component(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from shazam_for_vrc import self_test

    monkeypatch.setattr(
        self_test,
        "_read_executable_listing",
        lambda _path, _option: "http https tcp tls rtmp hls dash flv matroska mov mpegts "
        "ogg rtsp wav aac mp3 opus vorbis flac pcm_s16le",
    )

    with pytest.raises(RuntimeError, match="missing required filters: aresample"):
        _check_ffmpeg_capabilities(SimpleNamespace())  # type: ignore[arg-type]
