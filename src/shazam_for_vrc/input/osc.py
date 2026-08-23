"""Local VRChat OSC avatar-parameter trigger input."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import OSCUDPServer

from shazam_for_vrc.input import (
    InputSource,
    InputStatus,
    InputStatusKind,
    StatusCallback,
    TriggerCallback,
)

logger = logging.getLogger(__name__)

DEFAULT_VRCHAT_OSC_OUTPUT_HOST = "127.0.0.1"
DEFAULT_VRCHAT_OSC_OUTPUT_PORT = 9001
AVATAR_PARAMETER_PREFIX = "/avatar/parameters/"
MAX_AVATAR_PARAMETER_NAME_LENGTH = 256


OscServerFactory = Callable[[tuple[str, int], Dispatcher], OSCUDPServer]


def normalize_avatar_parameter_name(value: str) -> str:
    """Return a plain avatar parameter name, accepting a pasted full OSC address."""

    if not isinstance(value, str):
        raise ValueError("OSC avatar parameter must be text")
    normalized = value.strip()
    if normalized.startswith(AVATAR_PARAMETER_PREFIX):
        normalized = normalized[len(AVATAR_PARAMETER_PREFIX) :]
    if not normalized:
        raise ValueError("OSC avatar parameter must not be empty")
    if len(normalized) > MAX_AVATAR_PARAMETER_NAME_LENGTH:
        raise ValueError(
            f"OSC avatar parameter must be at most {MAX_AVATAR_PARAMETER_NAME_LENGTH} characters"
        )
    if (
        normalized.startswith("/")
        or normalized.endswith("/")
        or "//" in normalized
        or any(ord(character) < 32 for character in normalized)
    ):
        raise ValueError("OSC avatar parameter contains an invalid path segment")
    return normalized


def avatar_parameter_address(parameter_name: str) -> str:
    """Build VRChat's default outgoing address for one avatar parameter."""

    return AVATAR_PARAMETER_PREFIX + normalize_avatar_parameter_name(parameter_name)


class VRChatOscInputListener:
    """Receive one local avatar parameter and trigger on false-to-true transitions."""

    def __init__(
        self,
        on_trigger: TriggerCallback,
        on_status: StatusCallback,
        *,
        parameter_name: str,
        port: int = DEFAULT_VRCHAT_OSC_OUTPUT_PORT,
        host: str = DEFAULT_VRCHAT_OSC_OUTPUT_HOST,
        server_factory: OscServerFactory = OSCUDPServer,
        socket_timeout_seconds: float = 0.15,
        retry_seconds: float = 2.0,
    ) -> None:
        if host != DEFAULT_VRCHAT_OSC_OUTPUT_HOST:
            raise ValueError("VRChat OSC input must listen only on the local host")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65_535:
            raise ValueError("VRChat OSC input port must be between 1 and 65535")
        if socket_timeout_seconds <= 0 or retry_seconds <= 0:
            raise ValueError("OSC timeout and retry intervals must be positive")
        self._on_trigger = on_trigger
        self._on_status = on_status
        self._parameter_name = normalize_avatar_parameter_name(parameter_name)
        self._parameter_address = avatar_parameter_address(self._parameter_name)
        self._host = host
        self._port = port
        self._server_factory = server_factory
        self._socket_timeout_seconds = socket_timeout_seconds
        self._retry_seconds = retry_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._parameter_active = False
        self._last_status: tuple[InputStatusKind, str] | None = None

    def start(self) -> None:
        """Start listening on the configured local UDP port."""

        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="shazam-for-vrc-osc-input",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the receiver and release its UDP port."""

        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None

    def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                dispatcher = Dispatcher()
                dispatcher.map(self._parameter_address, self._handle_parameter)
                server: OSCUDPServer | None = None
                try:
                    server = self._server_factory((self._host, self._port), dispatcher)
                    server.timeout = self._socket_timeout_seconds
                    self._parameter_active = False
                    self._emit_status(
                        InputStatusKind.READY,
                        f"OSC ready — {self._parameter_address} on port {self._port}.",
                    )
                    while not self._stop_event.is_set():
                        server.handle_request()
                except OSError as error:
                    logger.debug("VRChat OSC input unavailable: %s", error)
                    self._emit_status(
                        InputStatusKind.ERROR,
                        f"OSC port {self._port} is unavailable; retrying.",
                    )
                except Exception:
                    logger.exception("Unexpected VRChat OSC input failure")
                    self._emit_status(
                        InputStatusKind.ERROR,
                        "VRChat OSC input failed; retrying automatically.",
                    )
                finally:
                    if server is not None:
                        server.server_close()

                if not self._stop_event.is_set():
                    self._stop_event.wait(self._retry_seconds)
        finally:
            self._emit_status(InputStatusKind.STOPPED, "VRChat OSC input stopped.")

    def _handle_parameter(self, _address: str, *values: object) -> None:
        if not values:
            return
        value = values[0]
        if isinstance(value, bool):
            active = value
        elif isinstance(value, (int, float)):
            active = value != 0
        else:
            logger.debug("Ignoring unsupported OSC avatar parameter value: %r", value)
            return

        rising_edge = active and not self._parameter_active
        self._parameter_active = active
        if not rising_edge:
            return
        try:
            self._on_trigger()
        except Exception:
            logger.exception("VRChat OSC trigger callback failed")

    def _emit_status(self, kind: InputStatusKind, message: str) -> None:
        value = (kind, message)
        if value == self._last_status:
            return
        self._last_status = value
        try:
            self._on_status(InputStatus(InputSource.OSC, kind, message))
        except Exception:
            logger.exception("VRChat OSC status callback failed")
