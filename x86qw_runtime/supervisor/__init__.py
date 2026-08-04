"""Process supervision contracts shared by the x86QW runtime entrypoints."""

from x86qw_runtime.platform.processes import ProcessIdentity, ProcessProbe

from .core import (
    Journal,
    Reporter,
    ServiceSignal,
    WindowsJobObject,
    posix_process_group_status,
    run_processes,
    stop_processes,
)
from .models import ProcessSpec, ServiceReadiness, StartupRcon
from .readiness import (
    apply_startup_rcon,
    preflight_ports,
    qtv_http_response_ready,
    wait_http_readiness,
    wait_udp_readiness,
)

__all__ = (
    "Journal",
    "ProcessSpec",
    "ProcessIdentity",
    "ProcessProbe",
    "Reporter",
    "ServiceReadiness",
    "ServiceSignal",
    "StartupRcon",
    "WindowsJobObject",
    "apply_startup_rcon",
    "preflight_ports",
    "posix_process_group_status",
    "qtv_http_response_ready",
    "run_processes",
    "wait_http_readiness",
    "wait_udp_readiness",
    "stop_processes",
)
