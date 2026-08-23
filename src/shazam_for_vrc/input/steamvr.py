"""SteamVR action input with configurable long-hold recognition."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Protocol

from platformdirs import user_data_path

from shazam_for_vrc.input import (
    InputSource,
    InputStatus,
    InputStatusKind,
    StatusCallback,
    TriggerCallback,
)

logger = logging.getLogger(__name__)

ACTION_SET_PATH = "/actions/shazam"
LISTEN_ACTION_PATH = "/actions/shazam/in/listen"
HAPTIC_ACTION_PATH = "/actions/shazam/out/haptic"
APP_NAME = "Shazam for VRC"
CONTROLLER_INPUT_PATHS = {
    "a": "/user/hand/right/input/a",
    "b": "/user/hand/right/input/b",
    "thumbstick": "/user/hand/right/input/thumbstick",
    "trigger": "/user/hand/right/input/trigger",
}
TRIGGER_MODES = frozenset({"long_press", "double_press"})


class SteamVRConnectionError(RuntimeError):
    """Raised when the SteamVR runtime or action API cannot be used."""


@dataclass(frozen=True, slots=True)
class DigitalActionState:
    """Normalized state of the SteamVR Listen boolean action."""

    active: bool
    pressed: bool
    origin: int | None = None


class SteamVRBackend(Protocol):
    """Small boundary around OpenVR so listener behavior is testable."""

    def connect(self, manifest_path: Path) -> None:
        """Connect to SteamVR and load the application's actions."""

    def read_listen_state(self) -> DigitalActionState:
        """Refresh and return the current Listen action state."""

    def pulse_haptic(self, origin: int | None) -> None:
        """Acknowledge one accepted trigger on its originating controller."""

    def close(self) -> None:
        """Release the OpenVR connection, if present."""


class LongHoldDetector:
    """Convert a held digital state into one event per press/release cycle."""

    def __init__(
        self,
        hold_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if hold_seconds <= 0:
            raise ValueError("hold_seconds must be positive")
        self.hold_seconds = float(hold_seconds)
        self._clock = clock
        self._pressed_since: float | None = None
        self._fired = False

    def update(self, state: DigitalActionState) -> bool:
        """Return true once when an active press reaches the hold threshold."""

        if not state.active or not state.pressed:
            self.reset()
            return False

        now = self._clock()
        if self._pressed_since is None:
            self._pressed_since = now
            return False
        if self._fired or now - self._pressed_since < self.hold_seconds:
            return False

        self._fired = True
        return True

    def reset(self) -> None:
        """Re-arm the detector after a release or inactive action."""

        self._pressed_since = None
        self._fired = False


class DoublePressDetector:
    """Convert two rising edges within a time window into one trigger."""

    def __init__(
        self,
        window_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.window_seconds = float(window_seconds)
        self._clock = clock
        self._was_pressed = False
        self._first_press_at: float | None = None

    def update(self, state: DigitalActionState) -> bool:
        """Return true on the second distinct press inside the configured window."""

        if not state.active:
            self.reset()
            return False
        now = self._clock()
        rising_edge = state.pressed and not self._was_pressed
        self._was_pressed = state.pressed
        if self._first_press_at is not None and now - self._first_press_at > self.window_seconds:
            self._first_press_at = None
        if not rising_edge:
            return False
        if self._first_press_at is None:
            self._first_press_at = now
            return False
        self._first_press_at = None
        return True

    def reset(self) -> None:
        self._was_pressed = False
        self._first_press_at = None


class OpenVRBackend:
    """OpenVR implementation loaded lazily so SteamVR remains optional at runtime."""

    def __init__(self, openvr_module: ModuleType | None = None) -> None:
        self._openvr = openvr_module
        self._initialized = False
        self._input: object | None = None
        self._active_sets: object | None = None
        self._listen_action: int | None = None
        self._haptic_action: int | None = None

    def connect(self, manifest_path: Path) -> None:
        if not manifest_path.is_file():
            raise SteamVRConnectionError(f"SteamVR action manifest is missing: {manifest_path}")

        try:
            if self._openvr is None:
                import openvr

                self._openvr = openvr
            openvr = self._openvr
            openvr.init(openvr.VRApplication_Background)
            self._initialized = True
            vr_input = openvr.VRInput()
            vr_input.setActionManifestPath(str(manifest_path.resolve()))
            action_set = vr_input.getActionSetHandle(ACTION_SET_PATH)
            self._listen_action = vr_input.getActionHandle(LISTEN_ACTION_PATH)
            self._haptic_action = vr_input.getActionHandle(HAPTIC_ACTION_PATH)

            active_sets = (openvr.VRActiveActionSet_t * 1)()
            active_sets[0].ulActionSet = action_set
            active_sets[0].ulRestrictedToDevice = openvr.k_ulInvalidInputValueHandle
            active_sets[0].ulSecondaryActionSet = openvr.k_ulInvalidActionSetHandle
            active_sets[0].nPriority = 0
            self._active_sets = active_sets
            self._input = vr_input
        except Exception as error:
            self.close()
            detail = str(error).strip() or type(error).__name__
            raise SteamVRConnectionError(detail) from error

    def read_listen_state(self) -> DigitalActionState:
        if self._input is None or self._active_sets is None or self._listen_action is None:
            raise SteamVRConnectionError("SteamVR input has not been initialized")
        try:
            self._input.updateActionState(self._active_sets)
            data = self._input.getDigitalActionData(
                self._listen_action,
                self._openvr.k_ulInvalidInputValueHandle,
            )
        except Exception as error:
            detail = str(error).strip() or type(error).__name__
            raise SteamVRConnectionError(detail) from error

        active = bool(data.bActive)
        origin = None
        if active:
            origin_info = self._input.getOriginTrackedDeviceInfo(data.activeOrigin)
            origin = int(origin_info.devicePath)
        return DigitalActionState(active=active, pressed=bool(data.bState), origin=origin)

    def pulse_haptic(self, origin: int | None) -> None:
        if self._input is None or self._haptic_action is None:
            return
        device = origin if origin is not None else self._openvr.k_ulInvalidInputValueHandle
        self._input.triggerHapticVibrationAction(
            self._haptic_action,
            0.0,
            0.08,
            100.0,
            0.55,
            device,
        )

    def close(self) -> None:
        self._input = None
        self._active_sets = None
        self._listen_action = None
        self._haptic_action = None
        if self._initialized and self._openvr is not None:
            try:
                self._openvr.shutdown()
            except Exception:
                logger.debug("OpenVR shutdown failed", exc_info=True)
        self._initialized = False


BackendFactory = Callable[[], SteamVRBackend]


def default_action_manifest_path() -> Path:
    """Return the action manifest shipped beside this module."""

    return Path(__file__).resolve().parent / "steamvr_actions" / "actions.json"


def configured_action_manifest_path(
    controller_button: str,
    output_directory: Path | None = None,
) -> Path:
    """Create a user-data manifest whose default bindings use the selected button."""

    if controller_button not in CONTROLLER_INPUT_PATHS:
        raise ValueError("controller_button is not supported")
    if controller_button == "b" and output_directory is None:
        return default_action_manifest_path()

    source_directory = default_action_manifest_path().parent
    target_directory = output_directory or (
        user_data_path(APP_NAME, appauthor=False) / "steamvr" / f"right-{controller_button}"
    )
    target_directory.mkdir(parents=True, exist_ok=True)
    action_manifest = json.loads((source_directory / "actions.json").read_text(encoding="utf-8"))
    for binding_name in ("bindings_oculus_touch.json", "bindings_knuckles.json"):
        binding = json.loads((source_directory / binding_name).read_text(encoding="utf-8"))
        sources = binding["bindings"][ACTION_SET_PATH]["sources"]
        sources[0]["path"] = CONTROLLER_INPUT_PATHS[controller_button]
        (target_directory / binding_name).write_text(
            json.dumps(binding, indent=2) + "\n",
            encoding="utf-8",
        )
    (target_directory / "actions.json").write_text(
        json.dumps(action_manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    return target_directory / "actions.json"


class SteamVRInputListener:
    """Reconnectable SteamVR polling thread that emits accepted long holds."""

    def __init__(
        self,
        on_trigger: TriggerCallback,
        on_status: StatusCallback,
        *,
        hold_seconds: float,
        trigger_mode: str = "long_press",
        double_press_seconds: float = 0.5,
        controller_button: str = "b",
        manifest_path: Path | None = None,
        backend_factory: BackendFactory = OpenVRBackend,
        poll_seconds: float = 0.02,
        reconnect_seconds: float = 2.0,
    ) -> None:
        if hold_seconds <= 0:
            raise ValueError("hold_seconds must be positive")
        if double_press_seconds <= 0:
            raise ValueError("double_press_seconds must be positive")
        if trigger_mode not in TRIGGER_MODES:
            raise ValueError("trigger_mode is not supported")
        if controller_button not in CONTROLLER_INPUT_PATHS:
            raise ValueError("controller_button is not supported")
        if poll_seconds <= 0 or reconnect_seconds <= 0:
            raise ValueError("poll and reconnect intervals must be positive")
        self._on_trigger = on_trigger
        self._on_status = on_status
        self._hold_seconds = float(hold_seconds)
        self._double_press_seconds = float(double_press_seconds)
        self._trigger_mode = trigger_mode
        self._controller_button = controller_button
        self._manifest_path = manifest_path or configured_action_manifest_path(controller_button)
        self._backend_factory = backend_factory
        self._poll_seconds = poll_seconds
        self._reconnect_seconds = reconnect_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_status: tuple[InputStatusKind, str] | None = None

    def start(self) -> None:
        """Start the background listener once."""

        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="shazam-for-vrc-steamvr-input",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop polling promptly and wait briefly for OpenVR cleanup."""

        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None

    def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                backend = self._backend_factory()
                try:
                    backend.connect(self._manifest_path)
                    self._poll_connected(backend)
                except SteamVRConnectionError as error:
                    logger.debug("SteamVR input unavailable: %s", error)
                    self._emit_status(
                        InputStatusKind.WAITING,
                        "SteamVR unavailable; waiting to reconnect.",
                    )
                except Exception:
                    logger.exception("Unexpected SteamVR input failure")
                    self._emit_status(
                        InputStatusKind.ERROR,
                        "SteamVR input failed; retrying automatically.",
                    )
                finally:
                    backend.close()

                if not self._stop_event.is_set():
                    self._stop_event.wait(self._reconnect_seconds)
        finally:
            self._emit_status(InputStatusKind.STOPPED, "SteamVR input stopped.")

    def _poll_connected(self, backend: SteamVRBackend) -> None:
        detector = (
            LongHoldDetector(self._hold_seconds)
            if self._trigger_mode == "long_press"
            else DoublePressDetector(self._double_press_seconds)
        )
        while not self._stop_event.is_set():
            state = backend.read_listen_state()
            if state.active:
                gesture = (
                    f"hold for {self._hold_seconds:g}s"
                    if self._trigger_mode == "long_press"
                    else f"double-press within {self._double_press_seconds:g}s"
                )
                self._emit_status(
                    InputStatusKind.READY,
                    f"SteamVR ready — {gesture} ({self._controller_button.upper()}).",
                )
            else:
                self._emit_status(
                    InputStatusKind.WAITING,
                    "SteamVR connected; waiting for a bound controller.",
                )

            if detector.update(state):
                try:
                    accepted = self._on_trigger()
                except Exception:
                    logger.exception("SteamVR trigger callback failed")
                    accepted = False
                if accepted:
                    try:
                        backend.pulse_haptic(state.origin)
                    except Exception:
                        logger.warning("SteamVR haptic acknowledgement failed", exc_info=True)

            self._stop_event.wait(self._poll_seconds)

    def _emit_status(self, kind: InputStatusKind, message: str) -> None:
        value = (kind, message)
        if value == self._last_status:
            return
        self._last_status = value
        try:
            self._on_status(InputStatus(InputSource.STEAMVR, kind, message))
        except Exception:
            logger.exception("SteamVR status callback failed")
