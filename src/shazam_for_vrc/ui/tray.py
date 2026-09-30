"""Windows system-tray integration kept separate from the Tk overlay."""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TrayCallback = Callable[[], None]
TrayBackendLoader = Callable[[], tuple[Any, Any]]


def application_icon_path() -> Path:
    """Return the shared application icon in source and PyInstaller builds."""

    bundled_root = getattr(sys, "_MEIPASS", None)
    if bundled_root:
        return Path(bundled_root) / "Website" / "favicon.ico"
    return Path(__file__).resolve().parents[3] / "Website" / "favicon.ico"


class SystemTray:
    """Own one optional tray icon and its small Open/Exit menu."""

    def __init__(
        self,
        *,
        on_restore: TrayCallback,
        on_exit: TrayCallback,
        on_failure: TrayCallback,
        backend_loader: TrayBackendLoader | None = None,
        icon_path: Path | None = None,
    ) -> None:
        self._on_restore = on_restore
        self._on_exit = on_exit
        self._on_failure = on_failure
        self._backend_loader = backend_loader or _load_backend
        self._icon_path = icon_path or application_icon_path()
        self._icon: Any | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def show(self) -> bool:
        """Show or create the tray icon, returning false if setup failed."""

        with self._lock:
            if self._icon is not None:
                try:
                    self._icon.visible = True
                except Exception:
                    logger.exception("Could not show the existing system-tray icon")
                    return False
                return True

            try:
                pystray, image_module = self._backend_loader()
                with image_module.open(self._icon_path) as source:
                    image = source.convert("RGBA")
                menu = pystray.Menu(
                    pystray.MenuItem(
                        "Open Shazam for VRC",
                        self._restore_from_menu,
                        default=True,
                    ),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem("Exit", self._exit_from_menu),
                )
                icon = pystray.Icon(
                    "shazam-for-vrc",
                    image,
                    "Shazam for VRC by Szeb",
                    menu,
                )
            except Exception:
                logger.exception("Could not prepare the system-tray icon")
                return False

            self._icon = icon
            self._thread = threading.Thread(
                target=self._run_icon,
                args=(icon,),
                name="shazam-for-vrc-system-tray",
                daemon=True,
            )
            self._thread.start()
            return True

    def hide(self) -> None:
        """Hide the icon without shutting down its lightweight message loop."""

        with self._lock:
            icon = self._icon
        if icon is not None:
            try:
                icon.visible = False
            except Exception:
                logger.exception("Could not hide the system-tray icon")

    def stop(self) -> None:
        """Remove the icon and stop its background message loop."""

        with self._lock:
            icon = self._icon
            thread = self._thread
            self._icon = None
            self._thread = None
        if icon is None:
            return
        try:
            icon.stop()
        except Exception:
            logger.exception("Could not stop the system-tray icon")
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def _run_icon(self, icon: Any) -> None:
        try:
            icon.run()
        except Exception:
            logger.exception("The system-tray message loop stopped unexpectedly")
            with self._lock:
                if self._icon is icon:
                    self._icon = None
                    self._thread = None
            self._on_failure()

    def _restore_from_menu(self, _icon: Any, _item: Any) -> None:
        self.hide()
        self._on_restore()

    def _exit_from_menu(self, _icon: Any, _item: Any) -> None:
        self._on_exit()


def _load_backend() -> tuple[Any, Any]:
    if sys.platform != "win32":
        raise RuntimeError("The system tray is available only on Windows.")
    import pystray
    from PIL import Image

    return pystray, Image
