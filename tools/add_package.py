#!/usr/bin/env python3
"""Register one reviewed local artifact in the x86QW catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from validate_catalog import DEFAULT_CATALOG, validate_catalog


NAME = re.compile(r"^[a-z0-9][a-z0-9_.+-]*$")


def register_package(
    catalog_path: Path,
    artifact: Path,
    *,
    component: str,
    version: str,
    channel: str,
    platform: str,
    architecture: str,
    origin_url: str,
    license_name: str,
    license_url: str,
    source_urls: list[str],
    mirror_urls: list[str],
    redistribution_reviewed: bool,
) -> dict[str, object]:
    if artifact.is_symlink() or not artifact.is_file():
        raise ValueError(f"artifact must be a regular file: {artifact}")
    if not NAME.fullmatch(component) or component == "id1":
        raise ValueError("component name is invalid or reserved")
    if artifact.name.casefold() in {"pak0.pak", "pak1.pak"}:
        raise ValueError("bare commercial-style PAK names are not accepted; publish a reviewed archive")
    size = artifact.stat().st_size
    if size <= 0:
        raise ValueError("artifact must not be empty")
    if not redistribution_reviewed:
        raise ValueError("redistribution review must be explicitly confirmed")

    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    package: dict[str, object] = {
        "component": component,
        "version": version,
        "channel": channel,
        "platform": platform,
        "architecture": architecture,
        "filename": artifact.name,
        "size": size,
        "sha256": file_sha256(artifact),
        "origin_url": origin_url,
        "license": license_name,
        "license_url": license_url,
        "source_urls": source_urls,
        "redistribution_reviewed": True,
        "urls": mirror_urls,
    }
    packages = catalog.get("packages")
    if not isinstance(packages, list):
        raise ValueError("catalog packages must be a list")
    identity_fields = ("component", "version", "channel", "platform", "architecture")
    identity = tuple(package[field] for field in identity_fields)
    if any(isinstance(current, dict) and tuple(current.get(field) for field in identity_fields) == identity for current in packages):
        raise ValueError("package identity already exists; published versions are immutable")
    packages.append(package)
    packages.sort(key=lambda item: tuple(str(item.get(field, "")) for field in identity_fields))
    catalog["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    validate_catalog(catalog)

    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".catalog-", suffix=".json", dir=catalog_path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
            json.dump(catalog, destination, ensure_ascii=False, indent=2)
            destination.write("\n")
        os.chmod(temporary, 0o644)
        os.replace(temporary, catalog_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return package


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Registra um artefato revisado no catálogo x86QW.")
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--component", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--channel", choices=("stable", "nightly"), required=True)
    parser.add_argument("--platform", choices=("macos", "linux", "windows"), required=True)
    parser.add_argument("--architecture", required=True)
    parser.add_argument("--origin-url", required=True)
    parser.add_argument("--license", dest="license_name", required=True)
    parser.add_argument("--license-url", required=True)
    parser.add_argument("--source-url", dest="source_urls", action="append", required=True)
    parser.add_argument("--url", dest="mirror_urls", action="append", required=True)
    parser.add_argument("--redistribution-reviewed", action="store_true", required=True)
    return parser.parse_args()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    options = parse_arguments()
    package = register_package(
        options.catalog,
        options.artifact,
        component=options.component,
        version=options.version,
        channel=options.channel,
        platform=options.platform,
        architecture=options.architecture,
        origin_url=options.origin_url,
        license_name=options.license_name,
        license_url=options.license_url,
        source_urls=options.source_urls,
        mirror_urls=options.mirror_urls,
        redistribution_reviewed=options.redistribution_reviewed,
    )
    print(f"registered {package['component']} {package['version']} {package['platform']} sha256={package['sha256']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"package not registered: {error}", file=sys.stderr)
        raise SystemExit(1)
