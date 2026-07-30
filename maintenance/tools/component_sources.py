"""Resolve installable x86QW component payloads from canonical distribution sources."""

from __future__ import annotations

import hashlib
import io
import json
import struct
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
    root = distribution / "distributions/nquake"
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


def build_zip_members(files: dict[str, bytes]) -> bytes:
    """Build a deterministic PK3/ZIP from explicitly selected upstream files."""
    if not files:
        raise ValueError("component archive selects no files")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", allowZip64=True) as rebuilt:
        for name, data in sorted(files.items()):
            relative = PurePosixPath(name)
            if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
                raise ValueError(f"unsafe component archive member: {name}")
            info = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            rebuilt.writestr(info, data)
    return output.getvalue()


def read_zip_members(payload: bytes, label: str) -> dict[str, bytes]:
    """Read a ZIP/PK3 into a safe, duplicate-free member map."""
    members: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(payload)) as package:
        corrupt = package.testzip()
        if corrupt is not None:
            raise ValueError(f"{label} contains a corrupt member: {corrupt}")
        for info in package.infolist():
            if info.is_dir():
                continue
            name = info.filename
            relative = PurePosixPath(name)
            if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
                raise ValueError(f"{label} contains an unsafe member: {name}")
            if name in members:
                raise ValueError(f"{label} contains a duplicate member: {name}")
            members[name] = package.read(info)
    if not members:
        raise ValueError(f"{label} contains no files")
    return members


def load_archive_layer_policy(
    context: ComponentSourceContext,
    identifier: str,
    release: dict[str, object],
    layer: dict[str, object],
) -> tuple[dict[str, dict[str, str]], str]:
    source_name = str(layer["policy"])
    declared_inputs = {
        str(entry["path"])
        for entry in context.components[identifier].get("project_inputs", [])
        if isinstance(entry, dict)
    }
    if source_name not in declared_inputs:
        raise ValueError(f"archive layer policy is not a declared project input: {source_name}")
    source = context.distribution.parent.joinpath(*PurePosixPath(source_name).parts)
    if not source.is_file() or source.is_symlink():
        raise ValueError(f"archive layer policy is missing or unsafe: {source}")
    payload = source.read_bytes()
    try:
        policy = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid archive layer policy: {source}") from error
    upstream = release.get("upstream")
    if not isinstance(upstream, dict):
        raise ValueError(f"archive layer has no upstream metadata: {identifier}")
    expected = {
        "format": 1,
        "project": "x86qw",
        "component": identifier,
        "target_archive": str(layer["target_archive"]),
        "reference_source": str(layer["reference_source"]),
        "reference_revision": context.commit,
        "upstream_release": str(upstream["release"]),
        "upstream_revision": str(upstream["source_revision"]),
    }
    if not isinstance(policy, dict) or any(policy.get(key) != value for key, value in expected.items()):
        raise ValueError(f"archive layer policy identity does not match its sources: {source}")
    if not isinstance(policy.get("policy"), str) or not str(policy["policy"]).strip():
        raise ValueError(f"archive layer policy has no rationale: {source}")
    conflicts = policy.get("conflicts")
    if not isinstance(conflicts, list):
        raise ValueError(f"archive layer policy has invalid conflicts: {source}")
    selected: dict[str, dict[str, str]] = {}
    for conflict in conflicts:
        if not isinstance(conflict, dict):
            raise ValueError(f"archive layer policy has an invalid conflict: {source}")
        member = conflict.get("member")
        if not isinstance(member, str):
            raise ValueError(f"archive layer policy has an invalid member: {source}")
        relative = PurePosixPath(member)
        if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
            raise ValueError(f"archive layer policy has an unsafe member: {member}")
        if member in selected:
            raise ValueError(f"archive layer policy repeats a conflict: {member}")
        reference_sha256 = conflict.get("reference_sha256")
        upstream_sha256 = conflict.get("upstream_sha256")
        if not all(
            isinstance(value, str)
            and len(value) == 64
            and all(character in "0123456789abcdef" for character in value)
            for value in (reference_sha256, upstream_sha256)
        ):
            raise ValueError(f"archive layer policy has an invalid conflict hash: {member}")
        resolution = conflict.get("resolution")
        if resolution not in {"reference", "upstream"}:
            raise ValueError(f"archive layer policy has an invalid resolution: {member}")
        selected[member] = {
            "reference_sha256": str(reference_sha256),
            "upstream_sha256": str(upstream_sha256),
            "resolution": str(resolution),
        }
    return selected, file_sha256_bytes(payload)


def apply_archive_layers(
    context: ComponentSourceContext,
    identifier: str,
    component: dict[str, object],
    release: dict[str, object],
    payloads: list[Payload],
) -> list[Payload]:
    layers = release.get("archive_layers", [])
    if not layers:
        return payloads
    updated = list(payloads)
    consumed_references: set[str] = set()
    for raw_layer in layers:
        assert isinstance(raw_layer, dict)
        target_archive = str(raw_layer["target_archive"])
        reference_source = str(raw_layer["reference_source"])
        destination, mode = destination_for_source(component, reference_source, "reference")
        if (
            component_for_source(context.catalog, reference_source, "reference") != identifier
            or reference_source not in context.partition.get(identifier, [])
            or destination != target_archive
            or mode != "archive-base"
        ):
            raise ValueError(
                f"archive layer reference is not the declared base of {identifier}: {reference_source}"
            )
        package_member = f"payload/{target_archive}"
        matching = [index for index, payload in enumerate(updated) if payload[1] == package_member]
        if len(matching) != 1:
            raise ValueError(f"archive layer target is not produced exactly once: {target_archive}")
        index = matching[0]
        upstream_source, _, upstream_payload, metadata = updated[index]
        reference_payload = verified_reference_payload(context, reference_source)
        reference_files = read_zip_members(reference_payload, f"nQuake base {reference_source}")
        upstream_files = read_zip_members(upstream_payload, f"KTX upstream {target_archive}")
        actual_conflicts = {
            name
            for name in set(reference_files) & set(upstream_files)
            if reference_files[name] != upstream_files[name]
        }
        policy, policy_sha256 = load_archive_layer_policy(
            context, identifier, release, raw_layer,
        )
        if set(policy) != actual_conflicts:
            missing = sorted(actual_conflicts - set(policy))
            stale = sorted(set(policy) - actual_conflicts)
            detail = missing[0] if missing else stale[0]
            kind = "unresolved" if missing else "stale"
            raise ValueError(f"archive layer has a {kind} conflict: {detail}")
        merged = dict(reference_files)
        for name, payload in upstream_files.items():
            if name not in actual_conflicts:
                merged[name] = payload
                continue
            decision = policy[name]
            reference_sha256 = file_sha256_bytes(reference_files[name])
            upstream_sha256 = file_sha256_bytes(payload)
            if (
                decision["reference_sha256"] != reference_sha256
                or decision["upstream_sha256"] != upstream_sha256
            ):
                raise ValueError(f"archive layer conflict changed since review: {name}")
            if decision["resolution"] == "upstream":
                merged[name] = payload
        composed = build_zip_members(merged)
        composed, x86qw_metadata = apply_archive_text_replacements(
            composed, target_archive, release,
        )
        layer_metadata: list[dict[str, str]] = [
            {
                "layer": "nquake",
                "source": reference_source,
                "revision": context.commit,
                "sha256": file_sha256_bytes(reference_payload),
            },
            {
                "layer": "upstream",
                "source": upstream_source,
                "revision": str(release["upstream"]["source_revision"]),  # type: ignore[index]
                "members": str(len(upstream_files)),
            },
            {
                "layer": "x86qw-policy",
                "source": str(raw_layer["policy"]),
                "sha256": policy_sha256,
                "conflicts": str(len(policy)),
            },
        ]
        updated[index] = (
            f"{reference_source} + {upstream_source} + x86QW",
            package_member,
            composed,
            [*metadata, *layer_metadata, *x86qw_metadata],
        )
        consumed_references.add(reference_source)
    expected_references = {
        str(entry["path"])
        for entry in component["sources"]
        if entry.get("origin", "reference") == "reference"
        and entry.get("mode") == "archive-base"
    }
    if consumed_references != expected_references:
        missing = sorted(expected_references - consumed_references)
        extra = sorted(consumed_references - expected_references)
        raise ValueError(f"archive base has no unique layer: {(missing or extra)[0]}")
    return updated


def apply_archive_text_replacements(
    payload: bytes,
    target_archive: str,
    release: dict[str, object],
) -> tuple[bytes, list[dict[str, str]]]:
    replacements = [
        replacement
        for replacement in release.get("archive_text_replacements", [])
        if isinstance(replacement, dict) and replacement.get("target_archive") == target_archive
    ]
    if not replacements:
        return payload, []
    member_payloads: dict[str, bytes] = {}
    applied: list[dict[str, str]] = []
    with zipfile.ZipFile(io.BytesIO(payload)) as package:
        for replacement in replacements:
            member = str(replacement["member"])
            if member in member_payloads:
                member_payload = member_payloads[member]
            else:
                try:
                    member_payload = package.read(member)
                except KeyError as error:
                    raise ValueError(f"archive text replacement member is missing: {member}") from error
            source_sha256 = file_sha256_bytes(member_payload)
            if source_sha256 != replacement["source_sha256"]:
                raise ValueError(f"archive member source failed integrity: {member}")
            before = str(replacement["before"]).encode("utf-8")
            after = str(replacement["after"]).encode("utf-8")
            count = int(replacement.get("count", 1))
            if member_payload.count(before) != count:
                raise ValueError(f"archive text replacement is not uniquely applicable: {member}")
            member_payloads[member] = member_payload.replace(before, after, count)
            applied.append({
                "target_archive": target_archive,
                "member": member,
                "source_sha256": source_sha256,
                "replacement_sha256": file_sha256_bytes(after),
            })
    return rewrite_zip_members(payload, member_payloads), applied


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


def remove_zip_members(payload: bytes, removals: set[str]) -> bytes:
    source = io.BytesIO(payload)
    output = io.BytesIO()
    with zipfile.ZipFile(source) as original:
        if original.testzip() is not None:
            raise ValueError("base component archive contains a corrupt member")
        original_files = {
            info.filename: original.read(info.filename)
            for info in original.infolist() if not info.is_dir()
        }
    missing = removals - set(original_files)
    if missing:
        raise ValueError(f"component archive removal target is missing: {sorted(missing)[0]}")
    for name in removals:
        del original_files[name]
    with zipfile.ZipFile(output, "w", allowZip64=True) as rebuilt:
        for name, data in sorted(original_files.items()):
            info = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            rebuilt.writestr(info, data)
    return output.getvalue()


def read_pak_members(payload: bytes) -> list[tuple[str, bytes]]:
    """Read a Quake PAK without accepting unsafe or duplicate member names."""
    if len(payload) < 12 or payload[:4] != b"PACK":
        raise ValueError("base component PAK has an invalid header")
    directory_offset, directory_size = struct.unpack("<II", payload[4:12])
    if (
        directory_size == 0
        or directory_size % 64
        or directory_offset < 12
        or directory_offset + directory_size > len(payload)
    ):
        raise ValueError("base component PAK has an invalid directory")
    members: list[tuple[str, bytes]] = []
    seen: set[str] = set()
    for offset in range(directory_offset, directory_offset + directory_size, 64):
        raw_name, file_offset, file_size = struct.unpack("<56sII", payload[offset:offset + 64])
        try:
            name = raw_name.split(b"\0", 1)[0].decode("ascii")
        except UnicodeDecodeError as error:
            raise ValueError("base component PAK has a non-ASCII member") from error
        path = PurePosixPath(name)
        if (
            not name
            or "\\" in name
            or path.is_absolute()
            or any(part in ("", ".", "..") for part in path.parts)
            or name in seen
            or file_offset < 12
            or file_offset + file_size > directory_offset
        ):
            raise ValueError(f"base component PAK has an unsafe member: {name!r}")
        seen.add(name)
        members.append((name, payload[file_offset:file_offset + file_size]))
    return members


def build_pak_members(members: list[tuple[str, bytes]]) -> bytes:
    if not members:
        raise ValueError("component PAK cannot be empty")
    body = io.BytesIO()
    body.write(b"\0" * 12)
    directory: list[tuple[str, int, int]] = []
    for name, data in members:
        encoded = name.encode("ascii")
        if len(encoded) > 56:
            raise ValueError(f"component PAK member name is too long: {name}")
        offset = body.tell()
        body.write(data)
        directory.append((name, offset, len(data)))
    directory_offset = body.tell()
    for name, offset, size in directory:
        body.write(struct.pack("<56sII", name.encode("ascii"), offset, size))
    payload = body.getvalue()
    return b"PACK" + struct.pack("<II", directory_offset, len(directory) * 64) + payload[12:]


def remove_pak_members(payload: bytes, removals: set[str]) -> bytes:
    members = read_pak_members(payload)
    names = {name for name, _ in members}
    missing = removals - names
    if missing:
        raise ValueError(f"component PAK removal target is missing: {sorted(missing)[0]}")
    return build_pak_members([
        (name, data) for name, data in members if name not in removals
    ])


def reference_component_payload(
    context: ComponentSourceContext,
    upstream_path: str,
    release: dict[str, object],
) -> tuple[bytes, list[dict[str, str]]]:
    payload = verified_reference_payload(context, upstream_path)
    applied: list[dict[str, str]] = []
    original_sha256 = file_sha256_bytes(payload)
    for replacement in release.get("text_replacements", []):
        assert isinstance(replacement, dict)
        if replacement["target"] != upstream_path:
            continue
        if original_sha256 != replacement["source_sha256"]:
            raise ValueError(f"text replacement source failed integrity: {upstream_path}")
        before = str(replacement["before"]).encode("utf-8")
        after = str(replacement["after"]).encode("utf-8")
        count = int(replacement.get("count", 1))
        if payload.count(before) != count:
            raise ValueError(f"text replacement is not uniquely applicable: {upstream_path}")
        payload = payload.replace(before, after, count)
        applied.append({
            "target": upstream_path,
            "source_sha256": str(replacement["source_sha256"]),
            "replacement_sha256": file_sha256_bytes(after),
        })
    replacements: dict[str, bytes] = {}
    additions: set[str] = set()
    for artifact in release.get("artifacts", []):
        assert isinstance(artifact, dict)
        artifact_members = artifact.get("members", [])
        if not artifact_members:
            continue
        members = verified_artifact_members(context.distribution, artifact)
        for member in artifact_members:
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
    payload, archive_applied = apply_archive_text_replacements(payload, upstream_path, release)
    applied.extend(archive_applied)
    moves = {
        str(move["source"]): str(move["destination"])
        for move in release.get("archive_moves", [])
        if isinstance(move, dict) and move.get("target_archive") == upstream_path
    }
    if moves:
        payload = move_zip_members(payload, moves)
        applied.extend({"source": old, "destination": new} for old, new in moves.items())
    removals = {
        str(removal["member"])
        for removal in release.get("archive_removals", [])
        if isinstance(removal, dict) and removal.get("target_archive") == upstream_path
    }
    if removals:
        payload = (
            remove_pak_members(payload, removals)
            if upstream_path.casefold().endswith(".pak")
            else remove_zip_members(payload, removals)
        )
        applied.extend({"removed": member} for member in sorted(removals))
    return payload, applied


def standalone_component_payloads(
    context: ComponentSourceContext,
    identifier: str,
    component: dict[str, object],
    release: dict[str, object],
) -> list[Payload]:
    selected: list[Payload] = []
    package_files: dict[str, bytes] = {}
    archive_files: dict[str, dict[str, bytes]] = {}
    archive_metadata: dict[str, list[dict[str, str]]] = {}
    layered_targets = {
        str(layer["target_archive"])
        for layer in release.get("archive_layers", [])
        if isinstance(layer, dict)
    }
    matched_release_roots: set[str] = set()
    for artifact in release.get("artifacts", []):
        assert isinstance(artifact, dict)
        artifact_files = verified_package_files(context.distribution, artifact)
        duplicate_sources = set(package_files) & set(artifact_files)
        if duplicate_sources:
            raise ValueError(f"standalone artifacts contain a duplicate member: {sorted(duplicate_sources)[0]}")
        package_files.update(artifact_files)
    for upstream_path, payload in sorted(package_files.items()):
        if component_for_source(context.catalog, upstream_path, "release") != identifier:
            continue
        destination, mode = destination_for_source(component, upstream_path, "release")
        matching_roots = {
            str(entry["path"])
            for entry in component["sources"]
            if entry.get("origin", "reference") == "release"
            and (
                upstream_path == entry["path"]
                or upstream_path.startswith(str(entry["path"]) + "/")
            )
            and not any(
                upstream_path == excluded
                or upstream_path.startswith(str(excluded) + "/")
                for excluded in entry.get("exclude", [])
            )
        }
        if len(matching_roots) != 1:
            raise ValueError(f"release source has no unique selector: {upstream_path}")
        matched_release_roots.update(matching_roots)
        if mode == "preserve":
            continue
        if mode == "archive":
            entries = [
                entry for entry in component["sources"]
                if entry.get("origin", "reference") == "release"
                and (
                    upstream_path == entry["path"]
                    or upstream_path.startswith(str(entry["path"]) + "/")
                )
                and not any(
                    upstream_path == excluded
                    or upstream_path.startswith(str(excluded) + "/")
                    for excluded in entry.get("exclude", [])
                )
            ]
            if len(entries) != 1:
                raise ValueError(f"archive source has no unique selector: {upstream_path}")
            root = str(entries[0]["path"])
            archive_member = upstream_path.removeprefix(root).lstrip("/") or PurePosixPath(upstream_path).name
            members = archive_files.setdefault(destination, {})
            if archive_member in members:
                raise ValueError(f"component archive member collision: {archive_member}")
            members[archive_member] = payload
            archive_metadata.setdefault(destination, []).append({
                "source": upstream_path,
                "sha256": file_sha256_bytes(payload),
            })
            continue
        member = f"{'defaults' if mode == 'default' else 'payload'}/{destination}"
        selected.append((upstream_path, member, payload, []))
    expected_release_roots = {
        str(entry["path"])
        for entry in component["sources"]
        if entry.get("origin", "reference") == "release"
    }
    missing_release_roots = expected_release_roots - matched_release_roots
    if missing_release_roots:
        raise ValueError(
            f"standalone component source selects no files: {sorted(missing_release_roots)[0]}"
        )
    for destination, files in sorted(archive_files.items()):
        payload = build_zip_members(files)
        if destination in layered_targets:
            applied: list[dict[str, str]] = []
        else:
            payload, applied = apply_archive_text_replacements(payload, destination, release)
        selected.append((
            f"official upstream archive: {destination}",
            f"payload/{destination}",
            payload,
            [*archive_metadata[destination], *applied],
        ))
    for copy in release.get("package_copies", []):
        assert isinstance(copy, dict)
        source = str(copy["source"])
        destination_path = str(copy["destination"])
        payload = package_files.get(source)
        if payload is None:
            raise ValueError(f"standalone package copy source is missing: {source}")
        if len(payload) != copy["size"] or file_sha256_bytes(payload) != copy["sha256"]:
            raise ValueError(f"standalone package copy source failed integrity: {source}")
        if component_for_source(context.catalog, source, "release") != identifier:
            raise ValueError(f"standalone package copy source belongs to another component: {source}")
        if component_for_source(context.catalog, destination_path, "release") != identifier:
            raise ValueError(f"standalone package copy destination belongs to another component: {destination_path}")
        destination, mode = destination_for_source(component, destination_path, "release")
        if mode == "preserve":
            continue
        member = f"{'defaults' if mode == 'default' else 'payload'}/{destination}"
        selected.append((f"{source} -> {destination_path}", member, payload, [{
            "source": source,
            "destination": destination_path,
            "sha256": str(copy["sha256"]),
        }]))
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


def apply_project_overrides(
    context: ComponentSourceContext,
    component: dict[str, object],
    payloads: list[Payload],
) -> list[Payload]:
    overrides = {
        str(entry["target"]): str(entry["path"])
        for entry in component.get("project_overrides", [])
        if isinstance(entry, dict)
    }
    if not overrides:
        return payloads
    project_root = context.distribution.parent
    updated: list[Payload] = []
    applied: set[str] = set()
    for upstream_path, member, payload, metadata in payloads:
        source_name = overrides.get(upstream_path)
        if source_name is None:
            updated.append((upstream_path, member, payload, metadata))
            continue
        source = project_root.joinpath(*PurePosixPath(source_name).parts)
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"canonical x86QW override is missing or unsafe: {source}")
        replacement = source.read_bytes()
        if not replacement:
            raise ValueError(f"canonical x86QW override is empty: {source}")
        updated.append((source_name, member, replacement, [*metadata, {
            "target": upstream_path,
            "project_override": source_name,
            "sha256": file_sha256_bytes(replacement),
        }]))
        applied.add(upstream_path)
    missing = set(overrides) - applied
    if missing:
        raise ValueError(f"project override target is not consumed: {sorted(missing)[0]}")
    return updated


def resolve_component_payloads(
    context: ComponentSourceContext,
    identifier: str,
) -> tuple[dict[str, object], str, list[Payload]]:
    component = context.components.get(identifier)
    if component is None:
        raise ValueError(f"unknown component source: {identifier}")
    release = component_release(context.releases, identifier)
    strategy = str(release["strategy"])
    if strategy in {"upstream-package", "upstream-composed"}:
        artifacts = release["artifacts"]
        assert isinstance(artifacts, list) and artifacts
        if strategy == "upstream-composed":
            upstream = release["upstream"]
            assert isinstance(upstream, dict)
            source_revision = str(upstream["source_revision"])
        else:
            assert len(artifacts) == 1
            source_revision = str(artifacts[0]["sha256"])
        payloads = standalone_component_payloads(context, identifier, component, release)
        payloads = apply_archive_layers(context, identifier, component, release, payloads)
    else:
        source_revision = context.commit
        payloads = []
        for upstream_path in context.partition[identifier]:
            destination, mode = destination_for_source(component, upstream_path, "reference")
            if mode == "preserve":
                continue
            payload, overrides = reference_component_payload(context, upstream_path, release)
            member = f"{'defaults' if mode == 'default' else 'payload'}/{destination}"
            payloads.append((upstream_path, member, payload, overrides))
        declared_replacements = {
            str(replacement["target"])
            for replacement in release.get("text_replacements", [])
            if isinstance(replacement, dict)
        }
        applied_replacements = {
            str(override["target"])
            for _, _, _, overrides in payloads
            for override in overrides
            if "source_sha256" in override and "target" in override
        }
        missing_replacements = declared_replacements - applied_replacements
        if missing_replacements:
            raise ValueError(
                f"text replacement target is not consumed by {identifier}: {sorted(missing_replacements)[0]}"
            )
        if any(
            entry.get("origin", "reference") == "release"
            for entry in component["sources"]
        ):
            payloads.extend(standalone_component_payloads(
                context, identifier, component, release,
            ))
    declared_archive_replacements = {
        (str(replacement["target_archive"]), str(replacement["member"]))
        for replacement in release.get("archive_text_replacements", [])
        if isinstance(replacement, dict)
    }
    applied_archive_replacements = {
        (str(override["target_archive"]), str(override["member"]))
        for _, _, _, overrides in payloads
        for override in overrides
        if "target_archive" in override and "member" in override
    }
    missing_archive_replacements = declared_archive_replacements - applied_archive_replacements
    if missing_archive_replacements:
        target, member = sorted(missing_archive_replacements)[0]
        raise ValueError(
            f"archive text replacement is not consumed by {identifier}: {target}:{member}"
        )
    payloads = apply_project_overrides(context, component, payloads)
    payloads.extend(project_component_payloads(context, component))
    members = [member for _, member, _, _ in payloads]
    if len(members) != len(set(members)):
        raise ValueError(f"component sources produce a duplicate package member: {identifier}")
    return release, source_revision, payloads
