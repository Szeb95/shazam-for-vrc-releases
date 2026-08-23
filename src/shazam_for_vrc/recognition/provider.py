"""Typed interface implemented by music-recognition providers."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from shazam_for_vrc.recognition.models import RecognitionResult


class RecognitionProvider(Protocol):
    """Recognize a short-lived audio sample without owning its lifecycle."""

    async def recognize(self, sample_path: Path) -> RecognitionResult:
        """Return a normalized match or no-match result for ``sample_path``."""

        ...
