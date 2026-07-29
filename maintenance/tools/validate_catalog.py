#!/usr/bin/env python3
"""Validate the public x86QW package catalog using only Python's stdlib."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

try:
    from .component_policy import load_component_policy, require_component
except ImportError:  # Execucao direta: python3 maintenance/tools/validate_catalog.py
    from component_policy import load_component_policy, require_component


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG = ROOT / "site/public/api/v1/catalog.json"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]*$")
PLATFORMS = {"macos", "linux", "windows", "source", "any"}
CHANNELS = {"stable", "nightly", "content"}
PACKAGE_FIELDS = (
    "component", "version", "channel", "platform", "architecture",
    "filename", "size", "sha256", "urls", "origin_url", "license", "license_url",
    "source_urls", "redistribution_reviewed",
)
REQUIRED = frozenset(PACKAGE_FIELDS)


def validate_package(
    package: object,
    label: str = "package",
    *,
    require_reviewed: bool = True,
) -> tuple[str, ...]:
    if not isinstance(package, dict) or not REQUIRED <= package.keys():
        raise ValueError(f"{label} is missing required fields")
    if package["channel"] not in CHANNELS:
        raise ValueError(f"{label}.channel is invalid")
    if package["platform"] not in PLATFORMS:
        raise ValueError(f"{label}.platform is invalid")
    if not all(isinstance(package[key], str) and package[key] for key in (
        "component", "version", "architecture", "filename", "license"
    )):
        raise ValueError(f"{label} has an empty text field")
    require_component(load_component_policy(), package["component"])
    for key in ("component", "version", "architecture"):
        if not SAFE_SEGMENT.fullmatch(package[key]):
            raise ValueError(f"{label}.{key} is not a safe path segment")
    package_id = package.get("package", package["component"])
    if not isinstance(package_id, str) or not SAFE_SEGMENT.fullmatch(package_id):
        raise ValueError(f"{label}.package is not a safe path segment")
    if package["channel"] == "content" and package["component"] == "ezquake":
        raise ValueError(f"{label} content packages must use a content component namespace")
    filename = package["filename"]
    if Path(filename).name != filename or "/" in filename or "\\" in filename or filename in {".", ".."}:
        raise ValueError(f"{label}.filename must not contain a path")
    if not isinstance(package["redistribution_reviewed"], bool):
        raise ValueError(f"{label}.redistribution_reviewed must be a boolean")
    if require_reviewed and package["redistribution_reviewed"] is not True:
        raise ValueError(f"{label}.redistribution_reviewed must be true")
    if not isinstance(package["size"], int) or package["size"] <= 0:
        raise ValueError(f"{label}.size must be a positive integer")
    if not isinstance(package["sha256"], str) or not SHA256.fullmatch(package["sha256"]):
        raise ValueError(f"{label}.sha256 is invalid")
    urls = package["urls"]
    if not isinstance(urls, list) or not urls:
        raise ValueError(f"{label}.urls must contain at least one mirror")
    if not all(isinstance(url, str) for url in urls):
        raise ValueError(f"{label}.urls must contain only strings")
    if len(urls) != len(set(urls)):
        raise ValueError(f"{label}.urls contains duplicates")
    source_urls = package["source_urls"]
    if not isinstance(source_urls, list) or not source_urls:
        raise ValueError(f"{label}.source_urls must contain at least one source")
    if not all(isinstance(url, str) for url in source_urls) or len(source_urls) != len(set(source_urls)):
        raise ValueError(f"{label}.source_urls must contain unique strings")
    for url in [package["origin_url"], package["license_url"], *source_urls, *urls]:
        parsed = urlsplit(url) if isinstance(url, str) else None
        if parsed is None or parsed.scheme != "https" or not parsed.netloc:
            raise ValueError(f"{label} accepts only absolute HTTPS URLs")
    if "release_url" in package:
        release_url = package["release_url"]
        parsed = urlsplit(release_url) if isinstance(release_url, str) else None
        if parsed is None or parsed.scheme != "https" or not parsed.netloc:
            raise ValueError(f"{label}.release_url must be an absolute HTTPS URL")
    if "release_notes" in package and (
        not isinstance(package["release_notes"], str) or not package["release_notes"].strip()
    ):
        raise ValueError(f"{label}.release_notes must be non-empty text")
    if "upstream_version" in package and (
        not isinstance(package["upstream_version"], str)
        or not SAFE_SEGMENT.fullmatch(package["upstream_version"])
    ):
        raise ValueError(f"{label}.upstream_version is invalid")
    if package["component"] == "installer" and not isinstance(package.get("current"), bool):
        raise ValueError(f"{label}.current must be a boolean for installer packages")
    if "distribution_path" in package:
        distribution_path = package["distribution_path"]
        if not isinstance(distribution_path, str):
            raise ValueError(f"{label}.distribution_path is invalid")
        relative = PurePosixPath(distribution_path)
        if (
            relative.is_absolute()
            or any(part in ("", ".", "..") for part in relative.parts)
            or "\\" in distribution_path
            or relative.name != filename
        ):
            raise ValueError(f"{label}.distribution_path is unsafe")
    for url in [package["origin_url"], *urls]:
        if PurePosixPath(unquote(urlsplit(url).path)).name != filename:
            raise ValueError(f"{label} artifact URLs must end with filename")

    return tuple(str(value) for value in (
        package["component"], package_id, package["version"], package["channel"],
        package["platform"], package["architecture"],
    ))


def validate_catalog(catalog: object) -> int:
    if not isinstance(catalog, dict):
        raise ValueError("catalog must be a JSON object")
    if catalog.get("format") != 1 or catalog.get("project") != "x86qw":
        raise ValueError("unsupported catalog identity or format")
    packages = catalog.get("packages")
    if not isinstance(packages, list):
        raise ValueError("packages must be a list")

    identities: set[tuple[str, ...]] = set()
    for index, package in enumerate(packages):
        label = f"packages[{index}]"
        identity = validate_package(package, label)
        if identity in identities:
            raise ValueError(f"{label} duplicates a package identity")
        identities.add(identity)
    installer_packages = [
        package for package in packages
        if isinstance(package, dict) and package.get("component") == "installer"
    ]
    if installer_packages and sum(package.get("current") is True for package in installer_packages) != 1:
        raise ValueError("catalog must identify exactly one current installer package")
    return len(packages)


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT_CATALOG
    if len(argv) > 2:
        raise ValueError("usage: validate_catalog.py [catalog.json]")
    raw = path.read_bytes()
    count = validate_catalog(json.loads(raw))
    digest = hashlib.sha256(raw).hexdigest()
    print(f"catalog valid: {count} package(s), sha256={digest}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"catalog invalid: {error}", file=sys.stderr)
        raise SystemExit(1)
