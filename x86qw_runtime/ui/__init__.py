"""Canonical terminal UI package."""

from __future__ import annotations

from importlib import import_module as _import_module


# Keep the compact engine importable for contracts and direct consumers while
# routing installed entrypoints through the responsive presentation adapter.
canonical_menu = _import_module(".menu", __name__)
menu = _import_module(".polished", __name__)

__all__ = ("canonical_menu", "menu")
