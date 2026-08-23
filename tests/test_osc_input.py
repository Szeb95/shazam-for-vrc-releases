import socket
import threading
import time

import pytest
from pythonosc.udp_client import SimpleUDPClient

from shazam_for_vrc.input import InputSource, InputStatus, InputStatusKind
from shazam_for_vrc.input.osc import (
    VRChatOscInputListener,
    avatar_parameter_address,
    normalize_avatar_parameter_name,
)


def _unused_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("ShazamListen", "ShazamListen"),
        ("  ShazamListen  ", "ShazamListen"),
        ("/avatar/parameters/ShazamListen", "ShazamListen"),
        ("Menu/ShazamListen", "Menu/ShazamListen"),
        ("/avatar/parameters/Menu/ShazamListen", "Menu/ShazamListen"),
    ],
)
def test_normalizes_avatar_parameter_name(value: str, expected: str) -> None:
    assert normalize_avatar_parameter_name(value) == expected
    assert avatar_parameter_address(value) == f"/avatar/parameters/{expected}"


@pytest.mark.parametrize(
    "value",
    ["", "   ", "/avatar/parameters/", "/other/address", "group//name", "bad\nname"],
)
def test_rejects_invalid_avatar_parameter_name(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_avatar_parameter_name(value)


def test_loopback_avatar_parameter_triggers_only_on_rising_edge() -> None:
    port = _unused_udp_port()
    ready = threading.Event()
    stopped = threading.Event()
    trigger_condition = threading.Condition()
    trigger_count = 0
    statuses: list[InputStatus] = []

    def on_trigger() -> bool:
        nonlocal trigger_count
        with trigger_condition:
            trigger_count += 1
            trigger_condition.notify_all()
        return True

    def on_status(status: InputStatus) -> None:
        statuses.append(status)
        if status.kind is InputStatusKind.READY:
            ready.set()
        if status.kind is InputStatusKind.STOPPED:
            stopped.set()

    def wait_for_trigger_count(expected: int) -> None:
        deadline = time.monotonic() + 2.0
        with trigger_condition:
            while trigger_count < expected:
                remaining = deadline - time.monotonic()
                assert remaining > 0, f"Timed out waiting for {expected} OSC triggers"
                trigger_condition.wait(remaining)

    listener = VRChatOscInputListener(
        on_trigger,
        on_status,
        parameter_name="ShazamListen",
        port=port,
        socket_timeout_seconds=0.02,
        retry_seconds=0.02,
    )
    listener.start()
    try:
        assert ready.wait(2.0)
        client = SimpleUDPClient("127.0.0.1", port)
        address = "/avatar/parameters/ShazamListen"

        client.send_message(address, True)
        wait_for_trigger_count(1)
        client.send_message(address, True)
        time.sleep(0.1)
        assert trigger_count == 1

        client.send_message(address, False)
        time.sleep(0.05)
        client.send_message(address, True)
        wait_for_trigger_count(2)
    finally:
        listener.stop()

    assert stopped.wait(1.0)
    assert statuses[0].source is InputSource.OSC
    assert statuses[0].kind is InputStatusKind.READY
    assert statuses[-1].kind is InputStatusKind.STOPPED


def test_listener_reports_unavailable_port_and_retries() -> None:
    error_received = threading.Event()
    statuses: list[InputStatus] = []

    def unavailable_server(_address: tuple[str, int], _dispatcher: object) -> object:
        raise OSError("already in use")

    def on_status(status: InputStatus) -> None:
        statuses.append(status)
        if status.kind is InputStatusKind.ERROR:
            error_received.set()

    listener = VRChatOscInputListener(
        lambda: True,
        on_status,
        parameter_name="ShazamListen",
        port=9001,
        server_factory=unavailable_server,  # type: ignore[arg-type]
        socket_timeout_seconds=0.01,
        retry_seconds=0.01,
    )
    listener.start()
    try:
        assert error_received.wait(1.0)
    finally:
        listener.stop()

    assert statuses[0].source is InputSource.OSC
    assert statuses[0].kind is InputStatusKind.ERROR
    assert "unavailable" in statuses[0].message
    assert statuses[-1].kind is InputStatusKind.STOPPED
