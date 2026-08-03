"""Bounded and platform-neutral I/O contracts for x86QW."""

from __future__ import annotations

from importlib import import_module
from typing import Any


_ARCHIVE_EXPORTS = frozenset({
    "ArchiveError",
    "ArchiveLimits",
    "ArchiveMember",
    "ArchivePlan",
    "DEFAULT_ARCHIVE_LIMITS",
    "extract_archive",
    "read_archive_member",
    "read_archive_members",
    "scan_archive",
    "validate_installer_bundle",
    "validate_installer_history_bundle",
})

__all__ = sorted(_ARCHIVE_EXPORTS)


def __getattr__(name: str) -> Any:
    """Load archive helpers lazily so ``python -m`` remains warning-free."""
    if name not in _ARCHIVE_EXPORTS:
        raise AttributeError(name)
    return getattr(import_module(".archive", __name__), name)
