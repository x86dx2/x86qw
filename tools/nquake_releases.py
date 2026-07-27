"""Version, provenance and upstream-override helpers for nQuake components."""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path, PurePosixPath

from nquake_components import components_by_id, load_catalog as load_component_catalog


VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
STRATEGIES = {"reference-snapshot", "upstream-overlay"}
FRESHNESS = {"reference-current", "upstream-current"}
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _safe_path(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError(f"invalid {label}: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError(f"unsafe {label}: {value}")
    return value


def load_releases(path: Path, component_catalog_path: Path) -> dict[str, object]:
    try:
        releases = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read nQuake release inventory: {path}") from error
    component_catalog = load_component_catalog(component_catalog_path)
    validate_releases(releases, set(components_by_id(component_catalog)))
    return releases


def validate_releases(releases: object, component_ids: set[str]) -> None:
    if not isinstance(releases, dict) or releases.get("format") != 1 or releases.get("project") != "x86qw":
        raise ValueError("invalid nQuake release inventory identity")
    reference = releases.get("reference")
    if not isinstance(reference, dict) or not HEX40.fullmatch(str(reference.get("revision", ""))):
        raise ValueError("invalid nQuake reference revision")
    reference_revision = str(reference["revision"])
    components = releases.get("components")
    if not isinstance(components, dict) or set(components) != component_ids:
        raise ValueError("nQuake release inventory must cover every component exactly once")
    artifact_paths: set[str] = set()
    for identifier, release in components.items():
        if not isinstance(release, dict):
            raise ValueError(f"invalid release metadata: {identifier}")
        version = release.get("version")
        if not isinstance(version, str) or not VERSION.fullmatch(version):
            raise ValueError(f"invalid component version: {identifier}")
        if release.get("strategy") not in STRATEGIES or release.get("freshness") not in FRESHNESS:
            raise ValueError(f"invalid release strategy or freshness: {identifier}")
        if release["strategy"] == "reference-snapshot" and version != reference_revision[:12]:
            raise ValueError(f"reference component version differs from snapshot: {identifier}")
        upstream = release.get("upstream")
        if release["strategy"] == "upstream-overlay":
            if not isinstance(upstream, dict):
                raise ValueError(f"upstream overlay has no upstream metadata: {identifier}")
            if not REPOSITORY.fullmatch(str(upstream.get("repository", ""))):
                raise ValueError(f"invalid upstream repository: {identifier}")
            if not VERSION.fullmatch(str(upstream.get("release", ""))):
                raise ValueError(f"invalid upstream release: {identifier}")
            if not HEX40.fullmatch(str(upstream.get("source_revision", ""))):
                raise ValueError(f"invalid upstream source revision: {identifier}")
            for field in ("release_url", "source_url"):
                if not isinstance(upstream.get(field), str) or not upstream[field].startswith("https://"):
                    raise ValueError(f"invalid upstream {field}: {identifier}")
            if not isinstance(release.get("notes"), str) or not release["notes"].strip():
                raise ValueError(f"upstream overlay has no release notes: {identifier}")
            if not VERSION.fullmatch(str(release.get("distribution_tag", ""))):
                raise ValueError(f"invalid distribution tag: {identifier}")
            compatibility = release.get("compatibility")
            if (
                not isinstance(compatibility, dict)
                or not isinstance(compatibility.get("client_scope"), list)
                or not compatibility["client_scope"]
                or not all(isinstance(item, str) and item for item in compatibility["client_scope"])
                or not isinstance(compatibility.get("verified"), list)
                or not compatibility["verified"]
                or not isinstance(compatibility.get("gamecode_runtime"), str)
            ):
                raise ValueError(f"invalid compatibility evidence: {identifier}")
        artifacts = release.get("artifacts", [])
        if not isinstance(artifacts, list):
            raise ValueError(f"invalid artifacts: {identifier}")
        if release["strategy"] == "upstream-overlay" and not artifacts:
            raise ValueError(f"upstream overlay has no artifact: {identifier}")
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                raise ValueError(f"invalid artifact: {identifier}")
            filename = artifact.get("filename")
            if not isinstance(filename, str) or PurePosixPath(filename).name != filename:
                raise ValueError(f"invalid artifact filename: {identifier}")
            archive_path = _safe_path(artifact.get("archive_path"), "artifact archive path")
            if archive_path in artifact_paths or not archive_path.startswith(f"components/nquake/releases/{identifier}/"):
                raise ValueError(f"duplicate or misplaced artifact path: {identifier}")
            artifact_paths.add(archive_path)
            if PurePosixPath(archive_path).name != filename:
                raise ValueError(f"artifact path and filename differ: {identifier}")
            if not isinstance(artifact.get("url"), str) or not artifact["url"].startswith("https://"):
                raise ValueError(f"invalid artifact URL: {identifier}")
            if not isinstance(artifact.get("size"), int) or artifact["size"] <= 0:
                raise ValueError(f"invalid artifact size: {identifier}")
            if not HEX64.fullmatch(str(artifact.get("sha256", ""))):
                raise ValueError(f"invalid artifact hash: {identifier}")
            members = artifact.get("members")
            if not isinstance(members, list) or not members:
                raise ValueError(f"artifact has no consumed members: {identifier}")
            for member in members:
                if not isinstance(member, dict):
                    raise ValueError(f"invalid artifact member: {identifier}")
                _safe_path(member.get("path"), "artifact member")
                _safe_path(member.get("target_archive"), "target archive")
                _safe_path(member.get("target_member"), "target member")
                if not isinstance(member.get("size"), int) or member["size"] <= 0:
                    raise ValueError(f"invalid artifact member size: {identifier}")
                if not HEX64.fullmatch(str(member.get("sha256", ""))):
                    raise ValueError(f"invalid artifact member hash: {identifier}")


def component_release(releases: dict[str, object], identifier: str) -> dict[str, object]:
    components = releases["components"]
    assert isinstance(components, dict)
    release = components.get(identifier)
    if not isinstance(release, dict):
        raise ValueError(f"unknown nQuake component release: {identifier}")
    return release


def component_for_artifact_path(releases: dict[str, object], path: str) -> str | None:
    components = releases["components"]
    assert isinstance(components, dict)
    matches = [
        identifier
        for identifier, release in components.items() if isinstance(release, dict)
        for artifact in release.get("artifacts", [])
        if isinstance(artifact, dict) and artifact.get("archive_path") == path
    ]
    if len(matches) > 1:
        raise ValueError(f"component release artifact is assigned more than once: {path}")
    return matches[0] if matches else None


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def verified_artifact_members(archive_root: Path, artifact: dict[str, object]) -> dict[str, bytes]:
    path = archive_root / str(artifact["archive_path"])
    if not path.is_file() or path.stat().st_size != artifact["size"]:
        raise ValueError(f"missing or invalid component source artifact: {path}")
    payload = path.read_bytes()
    if sha256_bytes(payload) != artifact["sha256"]:
        raise ValueError(f"component source artifact failed SHA-256: {path}")
    selected: dict[str, bytes] = {}
    with zipfile.ZipFile(path) as package:
        if package.testzip() is not None:
            raise ValueError(f"component source artifact contains a corrupt member: {path}")
        for member in artifact["members"]:
            name = str(member["path"])
            try:
                data = package.read(name)
            except KeyError as error:
                raise ValueError(f"component source artifact lacks {name}: {path}") from error
            if len(data) != member["size"] or sha256_bytes(data) != member["sha256"]:
                raise ValueError(f"component source member failed integrity: {name}")
            selected[name] = data
    return selected
