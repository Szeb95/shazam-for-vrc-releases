"""Windows global keyboard hotkey input."""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from collections.abc import Callable
from ctypes import wintypes
from typing import Protocol

from shazam_for_vrc.input import (
    InputSource,
    InputStatus,
    InputStatusKind,
    StatusCallback,
    TriggerCallback,
)

logger = logging.getLogger(__name__)

WM_HOTKEY = 0x0312
PM_REMOVE = 0x0001
MOD_NOREPEAT = 0x4000
HOTKEY_ID = 0x5346
FUNCTION_KEY_CODES = {f"F{number}": 0x70 + number - 1 for number in range(1, 13)}


class HotkeyRegistrationError(RuntimeError):
    """Raised when Windows cannot register the requested global hotkey."""


class HotkeyBackend(Protocol):
    """Blocking operating-system hotkey loop used by the threaded adapter."""

    def run(
        self,
        stop_event: threading.Event,
        on_press: Callable[[], None],
        on_ready: Callable[[], None],
    ) -> None:
        """Run until stopped, invoking on_press for each key press."""


class WindowsHotkeyBackend:
    """Register one function key using the Windows thread message queue."""

    def __init__(self, hotkey: str = "F9") -> None:
        if hotkey not in FUNCTION_KEY_CODES:
            raise ValueError("hotkey must be a function key from F1 to F12")
        self.hotkey = hotkey

    def run(
        self,
        stop_event: threading.Event,
        on_press: Callable[[], None],
        on_ready: Callable[[], None],
    ) -> None:
        if sys.platform != "win32":
            raise HotkeyRegistrationError(
                f"The {self.hotkey} fallback is only available on Windows."
            )

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        message = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(message), None, 0, 0, PM_REMOVE)
        if not user32.RegisterHotKey(
            None,
            HOTKEY_ID,
            MOD_NOREPEAT,
            FUNCTION_KEY_CODES[self.hotkey],
        ):
            raise HotkeyRegistrationError(
                f"{self.hotkey} is already registered by another application."
            )

        try:
            on_ready()
            while not stop_event.is_set():
                while user32.PeekMessageW(
                    ctypes.byref(message),
                    None,
                    0,
                    0,
                    PM_REMOVE,
                ):
                    if message.message == WM_HOTKEY and message.wParam == HOTKEY_ID:
                        on_press()
                stop_event.wait(0.03)
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)


HotkeyBackendFactory = Callable[[], HotkeyBackend]
WindowsF9Backend = WindowsHotkeyBackend


class KeyboardHotkeyListener:
    """Own the F9 registration on a short-lived background thread."""

    def __init__(
        self,
        on_trigger: TriggerCallback,
        on_status: StatusCallback,
        *,
        hotkey: str = "F9",
        backend_factory: HotkeyBackendFactory | None = None,
    ) -> None:
        if hotkey not in FUNCTION_KEY_CODES:
            raise ValueError("hotkey must be a function key from F1 to F12")
        self._on_trigger = on_trigger
        self._on_status = on_status
        self._hotkey = hotkey
        self._backend_factory = backend_factory or (lambda: WindowsHotkeyBackend(self._hotkey))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Register the selected key without blocking the UI thread."""

        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="shazam-for-vrc-keyboard-hotkey",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Release the key and stop the message loop."""

        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None

    def _run(self) -> None:
        failed = False
        try:
            self._backend_factory().run(
                self._stop_event,
                self._handle_press,
                lambda: self._emit_status(
                    InputStatusKind.READY,
                    f"Keyboard fallback ready — press {self._hotkey}.",
                ),
            )
        except HotkeyRegistrationError as error:
            failed = True
            self._emit_status(InputStatusKind.ERROR, str(error))
        except Exception:
            failed = True
            logger.exception("Unexpected keyboard hotkey failure")
            self._emit_status(
                InputStatusKind.ERROR,
                f"The {self._hotkey} hotkey stopped unexpectedly.",
            )
        finally:
            if not failed:
                self._emit_status(InputStatusKind.STOPPED, "Keyboard fallback stopped.")

    def _handle_press(self) -> None:
        try:
            self._on_trigger()
        except Exception:
            logger.exception("Keyboard trigger callback failed")

    def _emit_status(self, kind: InputStatusKind, message: str) -> None:
        try:
            self._on_status(InputStatus(InputSource.KEYBOARD, kind, message))
        except Exception:
            logger.exception("Keyboard status callback failed")
