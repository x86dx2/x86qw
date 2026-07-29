"""Resolve installable x86QW component payloads from canonical distribution sources."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

try:
    from .components import (
        component_for_source,
        components_by_id,
        destination_for_source,
        load_catalog,
        validate_tree_partition,
    )
    from .component_releases import (
        component_release,
        load_releases,
        verified_artifact_members,
        verified_package_files,
    )
except ImportError:  # Executado diretamente por ferramentas em tools/.
    from components import (  # type: ignore[no-redef]
        component_for_source,
        components_by_id,
        destination_for_source,
        load_catalog,
        validate_tree_partition,
    )
    from component_releases import (  # type: ignore[no-redef]
        component_release,
        load_releases,
        verified_artifact_members,
        verified_package_files,
    )


Payload = tuple[str, str, bytes, list[dict[str, str]]]


@dataclass(frozen=True)
class ComponentSourceContext:
    distribution: Path
    catalog: dict[str, object]
    releases: dict[str, object]
    components: dict[str, dict[str, object]]
    commit: str
    snapshot: Path
    partition: dict[str, list[str]]
    manifest_files: dict[str, object] | None


def file_sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def discover_snapshot(distribution: Path) -> tuple[str, Path]:
    root = distribution / "nquake"
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"canonical nQuake source tree is missing: {root}")
    snapshots = [path for path in root.iterdir() if path.is_dir() and not path.is_symlink()]
    if len(snapshots) != 1 or len(snapshots[0].name) != 40:
        raise ValueError("distribution must contain exactly one commit-addressed nQuake source tree")
    return snapshots[0].name, snapshots[0]


def load_manifest_files(distribution: Path) -> dict[str, object] | None:
    path = distribution / "manifest.json"
    if not path.is_file() or path.is_symlink():
        return None
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid distribution manifest: {path}") from error
    if (
        not isinstance(manifest, dict)
        or manifest.get("format") != 1
        or manifest.get("project") != "x86qw"
        or not isinstance(manifest.get("files"), dict)
    ):
        raise ValueError(f"invalid distribution manifest: {path}")
    return manifest["files"]


def load_source_context(
    distribution: Path,
    component_catalog_path: Path,
    component_releases_path: Path,
) -> ComponentSourceContext:
    distribution = distribution.resolve()
    catalog = load_catalog(component_catalog_path)
    releases = load_releases(component_releases_path, component_catalog_path)
    components = components_by_id(catalog)
    commit, snapshot = discover_snapshot(distribution)
    paths: list[str] = []
    for path in snapshot.rglob("*"):
        if not path.is_file():
            continue
        if path.is_symlink():
            raise ValueError(f"canonical component source must not be a symlink: {path}")
        paths.append(path.relative_to(snapshot).as_posix())
    partition = validate_tree_partition(catalog, sorted(paths), "reference")
    return ComponentSourceContext(
        distribution=distribution,
        catalog=catalog,
        releases=releases,
        components=components,
        commit=commit,
        snapshot=snapshot,
        partition=partition,
        manifest_files=load_manifest_files(distribution),
    )


def verified_reference_payload(context: ComponentSourceContext, upstream_path: str) -> bytes:
    path = context.snapshot / upstream_path
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"canonical nQuake source is missing or unsafe: {path}")
    payload = path.read_bytes()
    if context.manifest_files is None:
        return payload
    relative = path.relative_to(context.distribution).as_posix()
    record = context.manifest_files.get(relative)
    if not isinstance(record, dict):
        raise ValueError(f"canonical source is absent from the distribution manifest: {relative}")
    if record.get("size") != len(payload) or record.get("sha256") != file_sha256_bytes(payload):
        raise ValueError(f"canonical source failed distribution integrity: {relative}")
    return payload


def rewrite_zip_members(
    payload: bytes,
    replacements: dict[str, bytes],
    additions: set[str] | None = None,
) -> bytes:
    additions = additions or set()
    if not additions <= set(replacements):
        raise ValueError("component archive additions must have a declared payload")
    source = io.BytesIO(payload)
    output = io.BytesIO()
    with zipfile.ZipFile(source) as original:
        if original.testzip() is not None:
            raise ValueError("base component archive contains a corrupt member")
        original_files = {
            info.filename: original.read(info.filename)
            for info in original.infolist() if not info.is_dir()
        }
    original_names = set(original_files)
    missing = set(replacements) - original_names - additions
    collisions = additions & original_names
    if missing:
        raise ValueError(f"component override target is missing: {sorted(missing)[0]}")
    if collisions:
        raise ValueError(f"component archive addition already exists: {sorted(collisions)[0]}")
    original_files.update(replacements)
    with zipfile.ZipFile(output, "w", allowZip64=True) as rebuilt:
        for name, data in sorted(original_files.items()):
            info = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            rebuilt.writestr(info, data)
    return output.getvalue()


def move_zip_members(payload: bytes, moves: dict[str, str]) -> bytes:
    source = io.BytesIO(payload)
    output = io.BytesIO()
    with zipfile.ZipFile(source) as original:
        if original.testzip() is not None:
            raise ValueError("base component archive contains a corrupt member")
        original_files = {
            info.filename: original.read(info.filename)
            for info in original.infolist() if not info.is_dir()
        }
    missing = set(moves) - set(original_files)
    collisions = set(moves.values()) & (set(original_files) - set(moves))
    if missing:
        raise ValueError(f"component archive move source is missing: {sorted(missing)[0]}")
    if collisions or len(set(moves.values())) != len(moves):
        raise ValueError(f"component archive move destination already exists: {sorted(collisions or moves.values())[0]}")
    for old, new in moves.items():
        original_files[new] = original_files.pop(old)
    with zipfile.ZipFile(output, "w", allowZip64=True) as rebuilt:
        for name, data in sorted(original_files.items()):
            info = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            rebuilt.writestr(info, data)
    return output.getvalue()


def reference_component_payload(
    context: ComponentSourceContext,
    upstream_path: str,
    release: dict[str, object],
) -> tuple[bytes, list[dict[str, str]]]:
    payload = verified_reference_payload(context, upstream_path)
    applied: list[dict[str, str]] = []
    replacements: dict[str, bytes] = {}
    additions: set[str] = set()
    for artifact in release.get("artifacts", []):
        assert isinstance(artifact, dict)
        members = verified_artifact_members(context.distribution, artifact)
        for member in artifact["members"]:
            assert isinstance(member, dict)
            if member["target_archive"] != upstream_path:
                continue
            member_path = str(member["path"])
            target_member = str(member["target_member"])
            replacements[target_member] = members[member_path]
            if member.get("target_mode", "replace") == "add":
                additions.add(target_member)
            applied.append({
                "artifact": str(artifact["url"]),
                "member": member_path,
                "sha256": str(member["sha256"]),
            })
    if replacements:
        payload = rewrite_zip_members(payload, replacements, additions)
    moves = {
        str(move["source"]): str(move["destination"])
        for move in release.get("archive_moves", [])
        if isinstance(move, dict) and move.get("target_archive") == upstream_path
    }
    if moves:
        payload = move_zip_members(payload, moves)
        applied.extend({"source": old, "destination": new} for old, new in moves.items())
    return payload, applied


def standalone_component_payloads(
    context: ComponentSourceContext,
    identifier: str,
    component: dict[str, object],
    release: dict[str, object],
) -> list[Payload]:
    selected: list[Payload] = []
    for artifact in release.get("artifacts", []):
        assert isinstance(artifact, dict)
        for upstream_path, payload in sorted(verified_package_files(context.distribution, artifact).items()):
            if component_for_source(context.catalog, upstream_path, "release") != identifier:
                continue
            destination, mode = destination_for_source(component, upstream_path, "release")
            member = f"{'defaults' if mode == 'default' else 'payload'}/{destination}"
            selected.append((upstream_path, member, payload, []))
    if not selected:
        raise ValueError(f"standalone component selects no files: {identifier}")
    return selected


def project_component_payloads(
    context: ComponentSourceContext,
    component: dict[str, object],
) -> list[Payload]:
    selected: list[Payload] = []
    project_root = context.distribution.parent
    for entry in component.get("project_sources", []):
        assert isinstance(entry, dict)
        source_name = str(entry["path"])
        source = project_root.joinpath(*PurePosixPath(source_name).parts)
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"canonical x86QW project source is missing or unsafe: {source}")
        payload = source.read_bytes()
        if not payload:
            raise ValueError(f"canonical x86QW project source is empty: {source}")
        destination = str(entry["destination"])
        member = f"{'defaults' if entry['mode'] == 'default' else 'payload'}/{destination}"
        selected.append((source_name, member, payload, []))
    return selected


def resolve_component_payloads(
    context: ComponentSourceContext,
    identifier: str,
) -> tuple[dict[str, object], str, list[Payload]]:
    component = context.components.get(identifier)
    if component is None:
        raise ValueError(f"unknown component source: {identifier}")
    release = component_release(context.releases, identifier)
    strategy = str(release["strategy"])
    if strategy == "upstream-package":
        artifacts = release["artifacts"]
        assert isinstance(artifacts, list) and len(artifacts) == 1
        source_revision = str(artifacts[0]["sha256"])
        payloads = standalone_component_payloads(context, identifier, component, release)
    else:
        source_revision = context.commit
        payloads = []
        for upstream_path in context.partition[identifier]:
            destination, mode = destination_for_source(component, upstream_path, "reference")
            payload, overrides = reference_component_payload(context, upstream_path, release)
            member = f"{'defaults' if mode == 'default' else 'payload'}/{destination}"
            payloads.append((upstream_path, member, payload, overrides))
    payloads.extend(project_component_payloads(context, component))
    members = [member for _, member, _, _ in payloads]
    if len(members) != len(set(members)):
        raise ValueError(f"component sources produce a duplicate package member: {identifier}")
    return release, source_revision, payloads
