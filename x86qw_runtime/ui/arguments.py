"""Argument parser behavior shared by installed x86QW entrypoints."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from collections.abc import Sequence


class FriendlyArgumentParser(argparse.ArgumentParser):
    """Render argparse failures with the stable Portuguese CLI contract."""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: erro: {message}\n")


def public_launcher_name(*, os_name: str | None = None) -> str:
    """Return the installed launcher for the selected host shell."""

    active_os_name = os.name if os_name is None else os_name
    return "x86qw.cmd" if active_os_name == "nt" else "./x86qw.sh"


def public_bootstrap_command(
    unix_command: str,
    powershell_command: str,
    *,
    os_name: str | None = None,
) -> str:
    """Select the already validated public bootstrap for the host shell."""

    active_os_name = os.name if os_name is None else os_name
    return powershell_command if active_os_name == "nt" else unix_command


def render_public_command(
    arguments: Sequence[str], *, os_name: str | None = None,
) -> str:
    """Render one copyable x86QW command for the selected host shell."""

    active_os_name = os.name if os_name is None else os_name
    launcher = public_launcher_name(os_name=active_os_name)
    command = [launcher, *arguments]
    return (
        subprocess.list2cmdline(command)
        if active_os_name == "nt"
        else shlex.join(command)
    )


__all__ = (
    "FriendlyArgumentParser",
    "public_bootstrap_command",
    "public_launcher_name",
    "render_public_command",
)
