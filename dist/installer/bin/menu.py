#!/usr/bin/env python3
"""Compatibility facade for the canonical x86QW menu engine."""

from x86qw_runtime.ui.menu import (
    MenuCancelled,
    MenuExit,
    MenuOption,
    configure,
    confirm,
    read_key,
    select_many,
    select_one,
    supports_navigation,
)

__all__ = (
    "MenuCancelled",
    "MenuExit",
    "MenuOption",
    "configure",
    "confirm",
    "read_key",
    "select_many",
    "select_one",
    "supports_navigation",
)
