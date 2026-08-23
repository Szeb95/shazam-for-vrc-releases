import json

import pytest

from shazam_for_vrc.output.notification import (
    Notification,
    NotificationError,
    NotificationStyle,
    XSOverlayNotifier,
)


def test_xsoverlay_notification_uses_expected_local_datagram_format() -> None:
    sent: list[tuple[bytes, tuple[str, int]]] = []
    notifier = XSOverlayNotifier(
        datagram_sender=lambda payload, address: sent.append((payload, address))
    )

    notifier.send(
        Notification(
            title="Track recognized",
            content="Daft Punk — Around the World",
            timeout_seconds=6,
        )
    )

    assert len(sent) == 1
    payload, address = sent[0]
    assert address == ("127.0.0.1", 42069)
    message = json.loads(payload)
    assert message == {
        "messageType": 1,
        "index": 0,
        "timeout": 6.0,
        "height": 175.0,
        "opacity": 1.0,
        "volume": 0.7,
        "audioPath": "default",
        "title": "Track recognized",
        "content": "Daft Punk — Around the World",
        "useBase64Icon": False,
        "icon": "default",
        "sourceApp": "Shazam for VRC",
    }


def test_xsoverlay_error_notification_uses_error_treatment_and_plain_text() -> None:
    sent: list[bytes] = []
    notifier = XSOverlayNotifier(datagram_sender=lambda payload, _address: sent.append(payload))

    notifier.send(
        Notification(
            title="Listening <error>",
            content="Player\nfailed",
            style=NotificationStyle.ERROR,
        )
    )

    message = json.loads(sent[0])
    assert message["title"] == "Listening ‹error›"
    assert message["content"] == "Player failed"
    assert message["audioPath"] == "error"
    assert message["icon"] == "error"


def test_xsoverlay_send_error_is_normalized() -> None:
    def fail(_payload: bytes, _address: tuple[str, int]) -> None:
        raise OSError("test failure")

    notifier = XSOverlayNotifier(datagram_sender=fail)

    with pytest.raises(NotificationError, match="could not be sent"):
        notifier.send(Notification(title="Start listening"))


@pytest.mark.parametrize(
    "notification",
    [
        {"title": ""},
        {"title": "Test", "content": 1},
        {"title": "Test", "timeout_seconds": 0.1},
        {"title": "Test", "timeout_seconds": float("nan")},
    ],
)
def test_notification_rejects_invalid_values(notification: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        Notification(**notification)  # type: ignore[arg-type]
