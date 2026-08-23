"""Signed GitHub release checks and verified installer downloads."""

from shazam_for_vrc.updates.service import (
    DownloadedUpdate,
    UpdateCheckResult,
    UpdateClient,
    UpdateError,
    UpdateInfo,
    UpdateSecurityError,
)

__all__ = [
    "DownloadedUpdate",
    "UpdateCheckResult",
    "UpdateClient",
    "UpdateError",
    "UpdateInfo",
    "UpdateSecurityError",
]
