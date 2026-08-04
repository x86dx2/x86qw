"""Shared Python runtime contract used by the x86QW installer and launchers."""

from __future__ import annotations

import ntpath
import os
import shlex
import subprocess
import sys
from typing import Optional, Sequence, Union


MINIMUM_VERSION = (3, 10)
LAUNCHER_PLACEHOLDER = "@X86QW_PYTHON@"
VERSION_PROBE = (
    "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
)


class UnsupportedPythonError(RuntimeError):
    """Raised before x86QW performs network access or mutates an installation."""


def version_is_supported(version_info: Sequence[int]) -> bool:
    return tuple(version_info[:2]) >= MINIMUM_VERSION


def require_supported_runtime(version_info: Optional[Sequence[int]] = None) -> None:
    current = sys.version_info if version_info is None else version_info
    if not version_is_supported(current):
        raise UnsupportedPythonError(
            "x86QW requer Python 3.10 ou mais recente; "
            f"o runtime atual é {current[0]}.{current[1]}."
        )


def validated_executable(
    executable: Optional[Union[str, os.PathLike[str]]] = None,
) -> str:
    require_supported_runtime()
    value = os.fspath(executable if executable is not None else sys.executable)
    if not value or any(ord(character) < 32 for character in value):
        raise ValueError("O caminho do Python validado não pode conter caracteres de controle.")
    expanded = os.path.expanduser(value)
    if not os.path.isabs(expanded) and not ntpath.isabs(expanded):
        expanded = os.path.abspath(expanded)
    return expanded


def run_handoff(
    application: Union[str, os.PathLike[str]],
    arguments: Sequence[str],
    *,
    executable: Optional[Union[str, os.PathLike[str]]] = None,
) -> int:
    """Run a validated Python application directly and return its exit code."""

    command = [
        validated_executable(executable),
        os.fspath(application),
        *arguments,
    ]
    return int(subprocess.run(command, check=False).returncode)


def _cmd_literal(value: str) -> str:
    # Percent expansion happens before quoted SET parsing in cmd.exe.
    return value.replace("%", "%%")


def render_launcher(
    name: str,
    template: str,
    executable: Optional[Union[str, os.PathLike[str]]] = None,
) -> str:
    """Render one launcher with the exact Python executable used by this CLI."""

    if template.count(LAUNCHER_PLACEHOLDER) != 1:
        raise ValueError(
            f"O template {name} deve conter exatamente um marcador {LAUNCHER_PLACEHOLDER}."
        )
    runtime = validated_executable(executable)
    if name == "x86qw.sh":
        literal = shlex.quote(runtime)
    elif name == "x86qw.cmd":
        literal = _cmd_literal(runtime)
    else:
        raise ValueError(f"Launcher x86QW desconhecido: {name}")
    return template.replace(LAUNCHER_PLACEHOLDER, literal)
