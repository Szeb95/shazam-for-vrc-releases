import sys
import tkinter
from types import SimpleNamespace

import pytest

from shazam_for_vrc.self_test import _check_gui_runtime, _check_system_audio_runtime


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
