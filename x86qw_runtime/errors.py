"""Typed errors and process exit categories shared by x86QW entrypoints."""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """Existing public CLI outcomes, named without changing their values."""

    SUCCESS = 0
    FAILURE = 1
    USAGE = 2
    INTERRUPTED = 130


class X86QWError(RuntimeError):
    """Base class for an expected, user-actionable x86QW failure."""

    def __init__(
        self,
        message: str,
        *,
        exit_code: ExitCode = ExitCode.FAILURE,
    ) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class InstallerError(X86QWError):
    """An expected failure while inspecting or changing an installation."""
