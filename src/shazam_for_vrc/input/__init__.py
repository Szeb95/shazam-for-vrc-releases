"""User-trigger input adapters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class InputSource(StrEnum):
    """Stable identifiers for trigger and status sources."""

    STEAMVR = "steamvr"
    KEYBOARD = "keyboard"
    OSC = "osc"


class InputStatusKind(StrEnum):
    """Connection lifecycle shared by input adapters and the UI."""

    WAITING = "waiting"
    READY = "ready"
    ERROR = "error"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class InputStatus:
    """A user-facing state update from one input adapter."""

    source: InputSource
    kind: InputStatusKind
    message: str


TriggerCallback = Callable[[], bool]
StatusCallback = Callable[[InputStatus], None]


class InputListener(Protocol):
    """Lifecycle implemented by background input listeners."""

    def start(self) -> None:
        """Start listening without blocking the caller."""

    def stop(self) -> None:
        """Stop listening and release operating-system resources."""


__all__ = [
    "InputListener",
    "InputSource",
    "InputStatus",
    "InputStatusKind",
    "StatusCallback",
    "TriggerCallback",
]
