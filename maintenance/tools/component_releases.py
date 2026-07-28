"""Version, provenance and upstream-override helpers for x86QW components."""

from __future__ import annotations

import hashlib
import json
import re
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

try:
    from .components import components_by_id, load_catalog as load_component_catalog
except ImportError:  # Executado diretamente por ferramentas em tools/.
    from components import components_by_id, load_catalog as load_component_catalog


VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
STRATEGIES = {"reference-snapshot", "upstream-overlay", "upstream-package"}
FRESHNESS = {"reference-current", "upstream-current"}
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
DISTRIBUTION_COMPONENT = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


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
        raise ValueError(f"cannot read component release inventory: {path}") from error
    component_catalog = load_component_catalog(component_catalog_path)
    validate_releases(
        releases,
        set(components_by_id(component_catalog)),
        set(component_catalog["content_namespaces"]),
    )
    return releases


def validate_releases(
    releases: object, component_ids: set[str], content_namespaces: set[str],
) -> None:
    if not isinstance(releases, dict) or releases.get("format") != 1 or releases.get("project") != "x86qw":
        raise ValueError("invalid component release inventory identity")
    reference = releases.get("reference")
    if not isinstance(reference, dict) or not HEX40.fullmatch(str(reference.get("revision", ""))):
        raise ValueError("invalid nQuake reference revision")
    reference_revision = str(reference["revision"])
    components = releases.get("components")
    if not isinstance(components, dict) or set(components) != component_ids:
        raise ValueError("release inventory must cover every component exactly once")
    distribution_paths: set[str] = set()
    for identifier, release in components.items():
        if not isinstance(release, dict):
            raise ValueError(f"invalid release metadata: {identifier}")
        version = release.get("version")
        if not isinstance(version, str) or not VERSION.fullmatch(version):
            raise ValueError(f"invalid component version: {identifier}")
        if release.get("strategy") not in STRATEGIES or release.get("freshness") not in FRESHNESS:
            raise ValueError(f"invalid release strategy or freshness: {identifier}")
        if release.get("distribution_component", "nquake") not in content_namespaces:
            raise ValueError(f"invalid distribution component: {identifier}")
        if release["strategy"] == "reference-snapshot" and not (
            version == reference_revision[:12]
            or version.startswith(reference_revision[:12] + "+x86qw.")
        ):
            raise ValueError(f"reference component version differs from snapshot: {identifier}")
        upstream = release.get("upstream")
        if release["strategy"] in {"upstream-overlay", "upstream-package"}:
            if not isinstance(upstream, dict):
                raise ValueError(f"upstream release has no upstream metadata: {identifier}")
            if release["strategy"] == "upstream-overlay" and not REPOSITORY.fullmatch(str(upstream.get("repository", ""))):
                raise ValueError(f"invalid upstream repository: {identifier}")
            if not VERSION.fullmatch(str(upstream.get("release", ""))):
                raise ValueError(f"invalid upstream release: {identifier}")
            if release["strategy"] == "upstream-overlay" and not HEX40.fullmatch(str(upstream.get("source_revision", ""))):
                raise ValueError(f"invalid upstream source revision: {identifier}")
            for field in ("release_url", "source_url"):
                if not isinstance(upstream.get(field), str) or not upstream[field].startswith("https://"):
                    raise ValueError(f"invalid upstream {field}: {identifier}")
            if not isinstance(release.get("notes"), str) or not release["notes"].strip():
                raise ValueError(f"upstream overlay has no release notes: {identifier}")
            if not VERSION.fullmatch(str(release.get("distribution_tag", ""))):
                raise ValueError(f"invalid distribution tag: {identifier}")
            for field in ("distribution_component",):
                if not DISTRIBUTION_COMPONENT.fullmatch(str(release.get(field, ""))):
                    raise ValueError(f"invalid {field}: {identifier}")
            source_mirrors = release.get("source_mirrors", [])
            if (
                not isinstance(source_mirrors, list)
                or not all(isinstance(url, str) and url.startswith("https://") for url in source_mirrors)
            ):
                raise ValueError(f"invalid source mirrors: {identifier}")
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
        if release["strategy"] in {"upstream-overlay", "upstream-package"} and not artifacts:
            raise ValueError(f"upstream release has no artifact: {identifier}")
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                raise ValueError(f"invalid artifact: {identifier}")
            filename = artifact.get("filename")
            if not isinstance(filename, str) or PurePosixPath(filename).name != filename:
                raise ValueError(f"invalid artifact filename: {identifier}")
            distribution_path = _safe_path(artifact.get("distribution_path"), "artifact distribution path")
            distribution_component = str(release.get("distribution_component", "nquake"))
            expected_prefix = f"mods/{distribution_component}/"
            if distribution_path in distribution_paths or not distribution_path.startswith(expected_prefix):
                raise ValueError(f"duplicate or misplaced distribution path: {identifier}")
            distribution_paths.add(distribution_path)
            if PurePosixPath(distribution_path).name != filename:
                raise ValueError(f"artifact path and filename differ: {identifier}")
            if not isinstance(artifact.get("url"), str) or not artifact["url"].startswith("https://"):
                raise ValueError(f"invalid artifact URL: {identifier}")
            if not isinstance(artifact.get("size"), int) or artifact["size"] <= 0:
                raise ValueError(f"invalid artifact size: {identifier}")
            if not HEX64.fullmatch(str(artifact.get("sha256", ""))):
                raise ValueError(f"invalid artifact hash: {identifier}")
            members = artifact.get("members", [])
            if not isinstance(members, list) or (release["strategy"] == "upstream-overlay" and not members):
                raise ValueError(f"artifact has invalid consumed members: {identifier}")
            if release["strategy"] == "upstream-package" and members:
                raise ValueError(f"standalone artifact members are selected by the component catalog: {identifier}")
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
        archive_moves = release.get("archive_moves", [])
        if not isinstance(archive_moves, list):
            raise ValueError(f"invalid archive moves: {identifier}")
        move_identities: set[tuple[str, str]] = set()
        for move in archive_moves:
            if not isinstance(move, dict):
                raise ValueError(f"invalid archive move: {identifier}")
            target = _safe_path(move.get("target_archive"), "archive move target")
            source = _safe_path(move.get("source"), "archive move source")
            destination = _safe_path(move.get("destination"), "archive move destination")
            if source == destination or (target, source) in move_identities:
                raise ValueError(f"duplicate or ineffective archive move: {identifier}")
            move_identities.add((target, source))


def component_release(releases: dict[str, object], identifier: str) -> dict[str, object]:
    components = releases["components"]
    assert isinstance(components, dict)
    release = components.get(identifier)
    if not isinstance(release, dict):
        raise ValueError(f"unknown component release: {identifier}")
    return release


def component_for_artifact_path(releases: dict[str, object], path: str) -> str | None:
    components = releases["components"]
    assert isinstance(components, dict)
    matches = [
        identifier
        for identifier, release in components.items() if isinstance(release, dict)
        for artifact in release.get("artifacts", [])
        if isinstance(artifact, dict) and artifact.get("distribution_path") == path
    ]
    if len(matches) > 1:
        raise ValueError(f"component release artifact is assigned more than once: {path}")
    return matches[0] if matches else None


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def verified_artifact_members(distribution_root: Path, artifact: dict[str, object]) -> dict[str, bytes]:
    path = distribution_root / str(artifact["distribution_path"])
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


def verified_package_files(distribution_root: Path, artifact: dict[str, object]) -> dict[str, bytes]:
    path = distribution_root / str(artifact["distribution_path"])
    if not path.is_file() or path.stat().st_size != artifact["size"]:
        raise ValueError(f"missing or invalid component source artifact: {path}")
    if sha256_bytes(path.read_bytes()) != artifact["sha256"]:
        raise ValueError(f"component source artifact failed SHA-256: {path}")
    selected: dict[str, bytes] = {}
    if path.name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(path, "r:gz") as package:
            for member in package.getmembers():
                if member.isdir():
                    continue
                name = _safe_path(member.name, "standalone artifact member")
                if not member.isfile() or member.issym() or member.islnk():
                    raise ValueError(f"standalone artifact contains an unsafe member: {name}")
                extracted = package.extractfile(member)
                if extracted is None:
                    raise ValueError(f"standalone artifact member cannot be read: {name}")
                selected[name] = extracted.read()
    elif path.suffix.casefold() == ".zip":
        with zipfile.ZipFile(path) as package:
            if package.testzip() is not None:
                raise ValueError(f"component source artifact contains a corrupt member: {path}")
            for member in package.infolist():
                if member.is_dir():
                    continue
                name = _safe_path(member.filename, "standalone artifact member")
                selected[name] = package.read(member)
    else:
        raise ValueError(f"unsupported standalone component archive: {path}")
    return selected
