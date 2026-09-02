"""Canonical terminal UI package."""

from __future__ import annotations

from . import menu
from . import polished


# Preserve a single canonical module, state machine and exception set. The
# polished layer only replaces private renderers inside that module.
canonical_menu = menu
polished.install(menu)

__all__ = ("canonical_menu", "menu", "polished")
