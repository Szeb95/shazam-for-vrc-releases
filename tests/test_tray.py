from __future__ import annotations

from pathlib import Path

from PIL import Image

from shazam_for_vrc.ui.tray import SystemTray, application_icon_path


class _FakeImage:
    def __enter__(self) -> _FakeImage:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def convert(self, mode: str) -> tuple[str, str]:
        return ("converted", mode)


class _FakeImageModule:
    @staticmethod
    def open(path: Path) -> _FakeImage:
        assert path.name == "app.ico"
        return _FakeImage()


class _FakeMenuItem:
    def __init__(self, text: str, action, *, default: bool = False) -> None:
        self.text = text
        self.action = action
        self.default = default


class _FakeMenu:
    SEPARATOR = object()

    def __init__(self, *items: object) -> None:
        self.items = items


class _FakeIcon:
    instances: list[_FakeIcon] = []

    def __init__(self, name: str, image: object, title: str, menu: _FakeMenu) -> None:
        self.name = name
        self.image = image
        self.title = title
        self.menu = menu
        self.visible = False
        self.stopped = False
        self.instances.append(self)

    def run(self) -> None:
        self.visible = True

    def stop(self) -> None:
        self.stopped = True


class _FakePystray:
    Icon = _FakeIcon
    Menu = _FakeMenu
    MenuItem = _FakeMenuItem


def test_application_icon_exists_in_source_checkout() -> None:
    assert application_icon_path().is_file()


def test_application_icon_contains_windows_sizes() -> None:
    with Image.open(application_icon_path()) as icon:
        assert icon.format == "ICO"
        sizes = icon.info["sizes"]
        alpha_extrema = icon.convert("RGBA").getchannel("A").getextrema()

    assert {
        (16, 16),
        (20, 20),
        (24, 24),
        (32, 32),
        (48, 48),
        (64, 64),
        (96, 96),
        (128, 128),
        (256, 256),
    } <= sizes
    assert alpha_extrema == (0, 255)


def test_system_tray_exposes_restore_and_exit_actions(tmp_path: Path) -> None:
    restored: list[bool] = []
    exited: list[bool] = []
    failures: list[bool] = []
    tray = SystemTray(
        on_restore=lambda: restored.append(True),
        on_exit=lambda: exited.append(True),
        on_failure=lambda: failures.append(True),
        backend_loader=lambda: (_FakePystray, _FakeImageModule),
        icon_path=tmp_path / "app.ico",
    )

    assert tray.show()
    icon = _FakeIcon.instances[-1]
    if tray._thread is not None:
        tray._thread.join(timeout=1)

    open_item = icon.menu.items[0]
    exit_item = icon.menu.items[2]
    open_item.action(icon, open_item)
    exit_item.action(icon, exit_item)

    assert restored == [True]
    assert exited == [True]
    assert not failures
    assert not icon.visible

    tray.stop()
    assert icon.stopped


def test_system_tray_setup_failure_leaves_taskbar_fallback_available(tmp_path: Path) -> None:
    def fail_to_load() -> tuple[object, object]:
        raise RuntimeError("not available")

    tray = SystemTray(
        on_restore=lambda: None,
        on_exit=lambda: None,
        on_failure=lambda: None,
        backend_loader=fail_to_load,
        icon_path=tmp_path / "app.ico",
    )

    assert not tray.show()
