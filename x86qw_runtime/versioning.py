"""Current x86QW version syntax shared by runtime and release tooling.

This module deliberately preserves the pre-1.0 contracts. Prerelease and
schema compatibility rules belong to the dedicated contract-freeze work.
"""

from __future__ import annotations

import re


STABLE_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
NIGHTLY_VERSION = re.compile(r"^[0-9]{8}-[0-9]{6}_[0-9a-f]{7}$")
COMPONENT_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}$")


def version_key(version: str) -> tuple[int, int, int]:
    """Return the existing three-integer ordering key for stable releases."""

    if not isinstance(version, str) or not STABLE_VERSION.fullmatch(version):
        raise ValueError(f"invalid installer version: {version}")
    major, minor, patch = version.split(".")
    return int(major), int(minor), int(patch)
