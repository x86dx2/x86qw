"""Validate the independent upstream and preserved-source registry."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath

try:
    from .downloader import DownloadPolicyError, MAX_ARTIFACT_BYTES, validate_https_url
except ImportError:  # Executado diretamente por ferramentas em tools/.
    from downloader import DownloadPolicyError, MAX_ARTIFACT_BYTES, validate_https_url


IDENTIFIER = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SOURCE_STATES = {"complete", "partial", "unavailable", "not-applicable"}
BUILD_STATES = {"reproducible", "documented", "unverified", "unavailable", "not-applicable"}
UPDATE_STRATEGIES = {
    "github-release", "git-ref", "ezquake-nightly", "artifact-fingerprint",
    "fixed-release", "manual",
}


def _safe_path(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError(f"invalid {label}: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe {label}: {value}")
    return value


def _persistent_https_url(value: object, label: str, identifier: str) -> str:
    """Validate a stored URL without ever reflecting attacker-controlled text."""

    try:
        parsed = validate_https_url(value, label)
    except DownloadPolicyError as error:
        raise ValueError(f"invalid persistent {label}: {identifier}") from error
    if parsed.query:
        raise ValueError(f"persistent {label} must not contain a query: {identifier}")
    assert isinstance(value, str)
    return value


def load_upstreams(path: Path) -> dict[str, object]:
    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read upstream registry: {path}") from error
    validate_upstreams(registry)
    return registry


def validate_upstreams(registry: object) -> None:
    if not isinstance(registry, dict) or registry.get("format") != 1 or registry.get("project") != "x86qw":
        raise ValueError("invalid upstream registry identity")
    entries = registry.get("upstreams")
    if not isinstance(entries, list) or not entries:
        raise ValueError("upstream registry is empty")
    identifiers: set[str] = set()
    paths: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("invalid upstream entry")
        identifier = entry.get("id")
        if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier) or identifier in identifiers:
            raise ValueError(f"invalid or duplicate upstream id: {identifier}")
        identifiers.add(identifier)
        if not all(isinstance(entry.get(field), str) and entry[field] for field in ("name", "kind", "version", "release_url")):
            raise ValueError(f"upstream lacks metadata: {identifier}")
        _persistent_https_url(entry["release_url"], "upstream release URL", identifier)
        update = entry.get("update")
        if not isinstance(update, dict) or update.get("strategy") not in UPDATE_STRATEGIES:
            raise ValueError(f"invalid update strategy: {identifier}")
        if update["strategy"] == "git-ref":
            _persistent_https_url(update.get("repository"), "Git repository URL", identifier)
        source = entry.get("source")
        if not isinstance(source, dict) or source.get("status") not in SOURCE_STATES:
            raise ValueError(f"invalid source status: {identifier}")
        if source["status"] in {"complete", "partial"}:
            _persistent_https_url(source.get("url"), "preserved source URL", identifier)
            distribution_path = _safe_path(source.get("distribution_path"), "source distribution path")
            if distribution_path in paths:
                raise ValueError(f"duplicate source distribution path: {distribution_path}")
            paths.add(distribution_path)
            has_file_identity = "size" in source or "sha256" in source
            if has_file_identity and (
                not isinstance(source.get("size"), int)
                or source["size"] <= 0
                or source["size"] > MAX_ARTIFACT_BYTES
                or not SHA256.fullmatch(str(source.get("sha256", "")))
            ):
                raise ValueError(f"invalid preserved source identity: {identifier}")
        build = entry.get("build")
        if not isinstance(build, dict) or build.get("status") not in BUILD_STATES:
            raise ValueError(f"invalid build status: {identifier}")
        if build["status"] in {"reproducible", "documented", "unverified"}:
            _safe_path(build.get("recipe"), "build recipe")


def verify_preserved_sources(registry: dict[str, object], distribution: Path, project_root: Path) -> int:
    verified = 0
    entries = registry["upstreams"]
    assert isinstance(entries, list)
    for entry in entries:
        assert isinstance(entry, dict)
        source = entry["source"]
        build = entry["build"]
        assert isinstance(source, dict) and isinstance(build, dict)
        if source["status"] in {"complete", "partial"}:
            path = distribution.joinpath(*PurePosixPath(str(source["distribution_path"])).parts)
            if "size" in source:
                if not path.is_file() or path.is_symlink() or path.stat().st_size != source["size"]:
                    raise ValueError(f"preserved source is missing or has the wrong size: {path}")
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if digest != source["sha256"]:
                    raise ValueError(f"preserved source failed SHA-256: {path}")
            elif not path.is_dir() or path.is_symlink():
                raise ValueError(f"preserved source tree is missing: {path}")
            verified += 1
        recipe = build.get("recipe")
        if isinstance(recipe, str):
            path = project_root.joinpath(*PurePosixPath(recipe).parts)
            if not path.is_file() or path.is_symlink():
                raise ValueError(f"upstream build recipe is missing: {path}")
    return verified


def source_owner(registry: dict[str, object], relative: str) -> str | None:
    entries = registry["upstreams"]
    assert isinstance(entries, list)
    for entry in entries:
        assert isinstance(entry, dict)
        source = entry["source"]
        assert isinstance(source, dict)
        root = source.get("distribution_path")
        if isinstance(root, str) and (relative == root or relative.startswith(root + "/")):
            return str(entry["id"])
    return None
