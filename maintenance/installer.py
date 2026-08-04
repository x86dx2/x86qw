#!/usr/bin/env python3
"""Development composition root for the installed x86QW manager facade."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INSTALLER_BIN = PROJECT_ROOT / "dist/installer/bin"
for location in (PROJECT_ROOT, INSTALLER_BIN):
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))

import manager  # noqa: E402
from maintenance.tools import component_sources  # noqa: E402


def main(arguments: list[str] | None = None) -> int:
    manager.configure_development_source_provider(manager.ComponentSourceProvider(
        load_context=component_sources.load_source_context,
        resolve_payloads=component_sources.resolve_component_payloads,
    ))
    return manager.main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
