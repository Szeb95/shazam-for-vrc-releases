import threading

from shazam_for_vrc.input import InputStatus, InputStatusKind
from shazam_for_vrc.input.hotkey import (
    HotkeyRegistrationError,
    KeyboardHotkeyListener,
)


class TriggeringBackend:
    def run(self, stop_event, on_press, on_ready) -> None:  # type: ignore[no-untyped-def]
        on_ready()
        on_press()
        stop_event.wait(1.0)


class FailingBackend:
    def run(self, stop_event, on_press, on_ready) -> None:  # type: ignore[no-untyped-def]
        raise HotkeyRegistrationError("F9 is busy.")


def test_keyboard_listener_reports_ready_and_forwards_f9() -> None:
    statuses: list[InputStatus] = []
    triggered = threading.Event()

    def on_trigger() -> bool:
        triggered.set()
        return True

    listener = KeyboardHotkeyListener(
        on_trigger,
        statuses.append,
        backend_factory=TriggeringBackend,
    )

    listener.start()
    assert triggered.wait(1.0)
    listener.stop()

    assert statuses[0].kind is InputStatusKind.READY
    assert statuses[-1].kind is InputStatusKind.STOPPED


def test_keyboard_listener_reports_configured_function_key() -> None:
    statuses: list[InputStatus] = []
    triggered = threading.Event()
    listener = KeyboardHotkeyListener(
        lambda: triggered.set() or True,
        statuses.append,
        hotkey="F4",
        backend_factory=TriggeringBackend,
    )

    listener.start()
    assert triggered.wait(1.0)
    listener.stop()

    assert "F4" in statuses[0].message


def test_keyboard_listener_keeps_registration_error_visible() -> None:
    statuses: list[InputStatus] = []
    failed = threading.Event()

    def on_status(status: InputStatus) -> None:
        statuses.append(status)
        if status.kind is InputStatusKind.ERROR:
            failed.set()

    listener = KeyboardHotkeyListener(
        lambda: True,
        on_status,
        backend_factory=FailingBackend,
    )

    listener.start()
    assert failed.wait(1.0)
    listener.stop()

    assert statuses[-1].kind is InputStatusKind.ERROR
    assert statuses[-1].message == "F9 is busy."
