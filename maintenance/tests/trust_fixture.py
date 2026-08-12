"""Deterministic bytes for the signed public 0.7.3 trust vector."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_GENERATED_AT = "2026-08-04T21:49:05Z"
HISTORICAL_PACKAGE = "x86qw-client-bootstrap"
HISTORICAL_INSTALLER = "x86qw-installer"
HISTORICAL_INSTALLER_MAX = (0, 7, 3)


def catalog_0_7_3_bytes() -> bytes:
    """Reconstruct the signed 0.7.3 catalog from the current catalog shape.

    The live catalog keeps accumulating immutable installer history.  The
    signed vector predates those entries, so its historical boundary must be
    explicit instead of depending on whichever catalog happens to be checked
    out by the test process.
    """

    path = ROOT / "site/public/api/v1/catalog.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    extra = [package for package in document["packages"] if package.get("package") == HISTORICAL_PACKAGE]
    if len(extra) != 1:
        raise AssertionError(
            "the working catalog no longer exposes the known post-0.7.3 delta"
        )
    document["generated_at"] = EXPECTED_GENERATED_AT
    document["packages"] = [
        package for package in document["packages"]
        if package.get("package") != HISTORICAL_PACKAGE
        and not (
            package.get("package") == HISTORICAL_INSTALLER
            and tuple(int(part) for part in str(package.get("version", "")).split("."))
            > HISTORICAL_INSTALLER_MAX
        )
    ]
    historical = next(
        package for package in document["packages"]
        if package.get("package") == HISTORICAL_INSTALLER
        and package.get("version") == "0.7.3"
    )
    historical["current"] = True
    historical["mirror_latest"] = True
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
