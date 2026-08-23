import ctypes
import json
import threading
from pathlib import Path
from types import SimpleNamespace

from shazam_for_vrc.input import InputStatus, InputStatusKind
from shazam_for_vrc.input.steamvr import (
    ACTION_SET_PATH,
    HAPTIC_ACTION_PATH,
    LISTEN_ACTION_PATH,
    DigitalActionState,
    DoublePressDetector,
    LongHoldDetector,
    OpenVRBackend,
    SteamVRConnectionError,
    SteamVRInputListener,
    configured_action_manifest_path,
    default_action_manifest_path,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_long_hold_fires_once_and_rearms_after_release() -> None:
    clock = FakeClock()
    detector = LongHoldDetector(0.8, clock=clock)
    held = DigitalActionState(active=True, pressed=True, origin=42)

    assert not detector.update(held)
    clock.now = 0.79
    assert not detector.update(held)
    clock.now = 0.8
    assert detector.update(held)
    clock.now = 2.0
    assert not detector.update(held)

    assert not detector.update(DigitalActionState(active=True, pressed=False))
    clock.now = 3.0
    assert not detector.update(held)
    clock.now = 3.81
    assert detector.update(held)


def test_inactive_action_resets_partial_hold() -> None:
    clock = FakeClock()
    detector = LongHoldDetector(1.0, clock=clock)
    held = DigitalActionState(active=True, pressed=True)

    assert not detector.update(held)
    clock.now = 0.8
    assert not detector.update(DigitalActionState(active=False, pressed=True))
    clock.now = 0.9
    assert not detector.update(held)
    clock.now = 1.8
    assert not detector.update(held)
    clock.now = 1.91
    assert detector.update(held)


def test_double_press_fires_only_for_two_distinct_presses_inside_window() -> None:
    clock = FakeClock()
    detector = DoublePressDetector(0.5, clock=clock)
    pressed = DigitalActionState(active=True, pressed=True)
    released = DigitalActionState(active=True, pressed=False)

    assert not detector.update(pressed)
    clock.now = 0.1
    assert not detector.update(released)
    clock.now = 0.4
    assert detector.update(pressed)
    assert not detector.update(pressed)

    clock.now = 1.0
    assert not detector.update(released)
    assert not detector.update(pressed)
    clock.now = 1.6
    assert not detector.update(released)
    assert not detector.update(pressed)


def test_shipped_manifest_binds_right_b_and_haptic_for_supported_controllers() -> None:
    manifest_path = default_action_manifest_path()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert {item["name"] for item in manifest["actions"]} == {
        LISTEN_ACTION_PATH,
        HAPTIC_ACTION_PATH,
    }
    assert manifest["action_sets"] == [{"name": ACTION_SET_PATH, "usage": "leftright"}]

    controller_types = set()
    for default in manifest["default_bindings"]:
        controller_types.add(default["controller_type"])
        binding = json.loads(
            (manifest_path.parent / default["binding_url"]).read_text(encoding="utf-8")
        )
        action_binding = binding["bindings"][ACTION_SET_PATH]
        assert action_binding["sources"][0]["path"] == "/user/hand/right/input/b"
        assert action_binding["sources"][0]["inputs"]["click"]["output"] == LISTEN_ACTION_PATH
        assert action_binding["haptics"][0]["output"] == HAPTIC_ACTION_PATH

    assert controller_types == {"oculus_touch", "knuckles"}


def test_can_generate_default_bindings_for_another_controller_button(
    tmp_path: Path,
) -> None:
    manifest_path = configured_action_manifest_path("a", tmp_path)

    assert manifest_path == tmp_path / "actions.json"
    for name in ("bindings_oculus_touch.json", "bindings_knuckles.json"):
        binding = json.loads((tmp_path / name).read_text(encoding="utf-8"))
        source = binding["bindings"][ACTION_SET_PATH]["sources"][0]
        assert source["path"] == "/user/hand/right/input/a"


class FakeActiveActionSet(ctypes.Structure):
    _fields_ = [
        ("ulActionSet", ctypes.c_uint64),
        ("ulRestrictedToDevice", ctypes.c_uint64),
        ("ulSecondaryActionSet", ctypes.c_uint64),
        ("unPadding", ctypes.c_uint32),
        ("nPriority", ctypes.c_int32),
    ]


class FakeVRInput:
    def __init__(self) -> None:
        self.manifest_path: str | None = None
        self.updated_sets: object | None = None
        self.haptic_args: tuple[object, ...] | None = None

    def setActionManifestPath(self, path: str) -> None:
        self.manifest_path = path

    def getActionSetHandle(self, path: str) -> int:
        assert path == ACTION_SET_PATH
        return 10

    def getActionHandle(self, path: str) -> int:
        return {LISTEN_ACTION_PATH: 20, HAPTIC_ACTION_PATH: 30}[path]

    def updateActionState(self, sets: object) -> None:
        self.updated_sets = sets

    def getDigitalActionData(self, action: int, device: int) -> object:
        assert action == 20
        assert device == 999
        return SimpleNamespace(bActive=True, bState=True, activeOrigin=77)

    def getOriginTrackedDeviceInfo(self, origin: int) -> object:
        assert origin == 77
        return SimpleNamespace(devicePath=78)

    def triggerHapticVibrationAction(self, *args: object) -> None:
        self.haptic_args = args


def test_openvr_backend_uses_background_actions_and_origin_haptics(tmp_path: Path) -> None:
    manifest = tmp_path / "actions.json"
    manifest.write_text("{}", encoding="utf-8")
    vr_input = FakeVRInput()
    initialized: list[int] = []
    shutdown_count = 0

    def shutdown() -> None:
        nonlocal shutdown_count
        shutdown_count += 1

    fake_openvr = SimpleNamespace(
        VRApplication_Background=3,
        k_ulInvalidInputValueHandle=999,
        k_ulInvalidActionSetHandle=888,
        VRActiveActionSet_t=FakeActiveActionSet,
        init=initialized.append,
        shutdown=shutdown,
        VRInput=lambda: vr_input,
    )
    backend = OpenVRBackend(fake_openvr)  # type: ignore[arg-type]

    backend.connect(manifest)
    state = backend.read_listen_state()
    backend.pulse_haptic(state.origin)
    backend.close()

    assert initialized == [3]
    assert vr_input.manifest_path == str(manifest.resolve())
    assert state == DigitalActionState(active=True, pressed=True, origin=78)
    assert vr_input.haptic_args == (30, 0.0, 0.08, 100.0, 0.55, 78)
    assert shutdown_count == 1


class ScriptedBackend:
    def __init__(
        self,
        states: list[DigitalActionState | Exception],
        *,
        connect_error: Exception | None = None,
    ) -> None:
        self.states = states
        self.connect_error = connect_error
        self.closed = False
        self.haptic_origins: list[int | None] = []

    def connect(self, _manifest_path: Path) -> None:
        if self.connect_error is not None:
            raise self.connect_error

    def read_listen_state(self) -> DigitalActionState:
        if self.states:
            value = self.states.pop(0)
            if isinstance(value, Exception):
                raise value
            return value
        return DigitalActionState(active=True, pressed=True, origin=51)

    def pulse_haptic(self, origin: int | None) -> None:
        self.haptic_origins.append(origin)

    def close(self) -> None:
        self.closed = True


def test_listener_recovers_from_startup_and_runtime_disconnects(tmp_path: Path) -> None:
    manifest = tmp_path / "actions.json"
    manifest.write_text("{}", encoding="utf-8")
    backends = [
        ScriptedBackend([], connect_error=SteamVRConnectionError("not running")),
        ScriptedBackend(
            [
                DigitalActionState(active=False, pressed=False),
                SteamVRConnectionError("connection lost"),
            ]
        ),
        ScriptedBackend([DigitalActionState(active=True, pressed=True, origin=51)]),
    ]
    created: list[ScriptedBackend] = []
    triggered = threading.Event()
    statuses: list[InputStatus] = []

    def backend_factory() -> ScriptedBackend:
        backend = backends[len(created)]
        created.append(backend)
        return backend

    def on_trigger() -> bool:
        triggered.set()
        return True

    listener = SteamVRInputListener(
        on_trigger,
        statuses.append,
        hold_seconds=0.005,
        manifest_path=manifest,
        backend_factory=backend_factory,
        poll_seconds=0.001,
        reconnect_seconds=0.001,
    )

    listener.start()
    assert triggered.wait(1.0)
    listener.stop()

    assert len(created) == 3
    assert all(backend.closed for backend in created)
    assert created[-1].haptic_origins == [51]
    assert any(status.kind is InputStatusKind.WAITING for status in statuses)
    assert any(status.kind is InputStatusKind.READY for status in statuses)
    assert statuses[-1].kind is InputStatusKind.STOPPED


def test_rejected_trigger_does_not_send_haptic(tmp_path: Path) -> None:
    manifest = tmp_path / "actions.json"
    manifest.write_text("{}", encoding="utf-8")
    backend = ScriptedBackend([DigitalActionState(active=True, pressed=True, origin=9)])
    callback_seen = threading.Event()

    def on_trigger() -> bool:
        callback_seen.set()
        return False

    listener = SteamVRInputListener(
        on_trigger,
        lambda _status: None,
        hold_seconds=0.005,
        manifest_path=manifest,
        backend_factory=lambda: backend,
        poll_seconds=0.001,
        reconnect_seconds=0.001,
    )

    listener.start()
    assert callback_seen.wait(1.0)
    listener.stop()

    assert backend.haptic_origins == []
