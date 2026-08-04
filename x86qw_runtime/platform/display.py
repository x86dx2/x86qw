"""Native display facts used by x86QW entrypoints."""

from __future__ import annotations

import json
import subprocess
import sys

from ..errors import InstallerError


class DisplayAdapterError(InstallerError):
    """A native display fact could not be proved."""


def is_macos_host(*, platform_name: str | None = None) -> bool:
    return (sys.platform if platform_name is None else platform_name) == "darwin"


def macos_main_display() -> dict[str, object]:
    """Return the primary macOS display reported by ``system_profiler``."""

    try:
        profile = subprocess.run(
            ["system_profiler", "SPDisplaysDataType", "-json"],
            check=True, capture_output=True, text=True, timeout=8,
        )
        document = json.loads(profile.stdout)
        displays = document.get("SPDisplaysDataType") if isinstance(document, dict) else None
        if not isinstance(displays, list):
            raise ValueError("lista de monitores ausente")
        for gpu in displays:
            if not isinstance(gpu, dict) or not isinstance(gpu.get("spdisplays_ndrvs"), list):
                continue
            for monitor in gpu["spdisplays_ndrvs"]:
                if isinstance(monitor, dict) and monitor.get("spdisplays_main") == "spdisplays_yes":
                    return monitor
        raise ValueError("monitor principal ausente")
    except (
        OSError, subprocess.SubprocessError, json.JSONDecodeError, TypeError, ValueError,
    ) as error:
        raise DisplayAdapterError(str(error)) from error
