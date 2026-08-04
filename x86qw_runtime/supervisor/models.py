"""Value objects used to describe supervised x86QW services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..platform.host import LaunchTarget


@dataclass(frozen=True)
class StartupRcon:
    address: str
    port: int
    password: str
    config_name: str
    expected_map: str
    expected_gamedir: str


@dataclass(frozen=True)
class ServiceReadiness:
    kind: str
    address: str
    port: int
    upstream: str | None = None


@dataclass(frozen=True)
class ProcessSpec:
    label: str
    arguments: tuple[str, ...]
    cwd: Path
    startup_rcon: StartupRcon | None = None
    readiness: ServiceReadiness | None = None
    parameters: tuple[tuple[str, str], ...] = ()
    launch_target: LaunchTarget | None = None


__all__ = ("ProcessSpec", "ServiceReadiness", "StartupRcon")
