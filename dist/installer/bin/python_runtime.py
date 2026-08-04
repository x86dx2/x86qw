"""Compatibility facade for the canonical Python runtime contract."""

from x86qw_runtime.platform.python_runtime import (
    LAUNCHER_PLACEHOLDER,
    MINIMUM_VERSION,
    VERSION_PROBE,
    UnsupportedPythonError,
    render_launcher,
    require_supported_runtime,
    validated_executable,
    version_is_supported,
)


__all__ = (
    "LAUNCHER_PLACEHOLDER",
    "MINIMUM_VERSION",
    "VERSION_PROBE",
    "UnsupportedPythonError",
    "render_launcher",
    "require_supported_runtime",
    "validated_executable",
    "version_is_supported",
)
