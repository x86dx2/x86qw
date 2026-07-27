#!/usr/bin/env python3
"""Build deterministic x86QW component packages from the preserved nQuake snapshot."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from nquake_components import (
    components_by_id,
    destination_for_source,
    load_catalog,
    validate_tree_partition,
)
from nquake_releases import component_release, load_releases, verified_artifact_members
from validate_catalog import DEFAULT_CATALOG, validate_catalog


ROOT = Path(__file__).resolve().parents[1]
NQUAKE_COMPONENTS = ROOT / "inventory/nquake-components.json"
NQUAKE_RELEASES = ROOT / "inventory/nquake-releases.json"
FIXED_ZIP_TIME = (2020, 1, 1, 0, 0, 0)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def zip_member(name: str, payload: bytes) -> tuple[zipfile.ZipInfo, bytes]:
    info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    return info, payload


def discover_snapshot(archive: Path) -> tuple[str, Path]:
    snapshots = [path for path in (archive / "components/nquake/snapshots").iterdir() if path.is_dir()]
    if len(snapshots) != 1 or len(snapshots[0].name) != 40:
        raise ValueError("archive must contain exactly one commit-addressed nQuake snapshot")
    return snapshots[0].name, snapshots[0]


def rewrite_zip_members(payload: bytes, replacements: dict[str, bytes]) -> bytes:
    source = io.BytesIO(payload)
    output = io.BytesIO()
    with zipfile.ZipFile(source) as original:
        if original.testzip() is not None:
            raise ValueError("base component archive contains a corrupt member")
        original_files = {
            info.filename: original.read(info.filename)
            for info in original.infolist() if not info.is_dir()
        }
    missing = set(replacements) - set(original_files)
    if missing:
        raise ValueError(f"component override target is missing: {sorted(missing)[0]}")
    original_files.update(replacements)
    with zipfile.ZipFile(output, "w", allowZip64=True) as rebuilt:
        for name, data in sorted(original_files.items()):
            info, content = zip_member(name, data)
            rebuilt.writestr(info, content)
    return output.getvalue()


def component_payload(
    archive: Path,
    snapshot: Path,
    upstream_path: str,
    release: dict[str, object],
) -> tuple[bytes, list[dict[str, str]]]:
    payload = (snapshot / upstream_path).read_bytes()
    applied: list[dict[str, str]] = []
    replacements: dict[str, bytes] = {}
    for artifact in release.get("artifacts", []):
        assert isinstance(artifact, dict)
        members = verified_artifact_members(archive, artifact)
        for member in artifact["members"]:
            assert isinstance(member, dict)
            if member["target_archive"] != upstream_path:
                continue
            member_path = str(member["path"])
            replacements[str(member["target_member"])] = members[member_path]
            applied.append({
                "artifact": str(artifact["url"]),
                "member": member_path,
                "sha256": str(member["sha256"]),
            })
    if replacements:
        payload = rewrite_zip_members(payload, replacements)
    return payload, applied


def build_packages(archive: Path, output: Path) -> dict[str, object]:
    catalog = load_catalog(NQUAKE_COMPONENTS)
    releases = load_releases(NQUAKE_RELEASES, NQUAKE_COMPONENTS)
    components = components_by_id(catalog)
    commit, snapshot = discover_snapshot(archive)
    paths = sorted(path.relative_to(snapshot).as_posix() for path in snapshot.rglob("*") if path.is_file())
    partition = validate_tree_partition(catalog, paths)
    release = f"nquake-{commit}"
    release_root = output / release
    release_root.mkdir(parents=True, exist_ok=True)
    packages = []
    for identifier, component in components.items():
        release_metadata = component_release(releases, identifier)
        version = str(release_metadata["version"])
        filename = f"{identifier}-{version}.zip"
        artifact = release_root / filename
        members: list[dict[str, str]] = []
        with zipfile.ZipFile(artifact, "w", allowZip64=True) as package:
            for upstream_path in partition[identifier]:
                destination, mode = destination_for_source(component, upstream_path)
                member_name = f"{'defaults' if mode == 'default' else 'payload'}/{destination}"
                payload, overrides = component_payload(archive, snapshot, upstream_path, release_metadata)
                member_metadata: dict[str, object] = {
                    "path": member_name,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "source": upstream_path,
                }
                if overrides:
                    member_metadata["overrides"] = overrides
                members.append(member_metadata)
                info, data = zip_member(member_name, payload)
                package.writestr(info, data)
            metadata: dict[str, object] = {
                "format": 1,
                "project": "x86qw",
                "package": identifier,
                "source_commit": commit,
                "members": members,
            }
            if release_metadata["strategy"] != "reference-snapshot":
                metadata["version"] = version
            package_metadata = json.dumps(
                metadata, ensure_ascii=False, indent=2, sort_keys=True,
            ).encode() + b"\n"
            info, data = zip_member("_x86qw/component.json", package_metadata)
            package.writestr(info, data)
        distribution_tag = str(release_metadata.get("distribution_tag", release))
        mirror_url = f"https://github.com/x86dx2/x86qw-dist/releases/download/{distribution_tag}/{filename}"
        source_urls = [f"https://github.com/nQuake/distfiles/tree/{commit}"]
        upstream = release_metadata.get("upstream")
        if isinstance(upstream, dict):
            source_url = upstream.get("source_url")
            release_url = upstream.get("release_url")
            source_urls.extend(str(url) for url in (source_url, release_url) if isinstance(url, str))
        package_record = {
            "component": "nquake",
            "package": identifier,
            "version": version,
            "channel": "content",
            "platform": "any",
            "architecture": "any",
            "filename": filename,
            "size": artifact.stat().st_size,
            "sha256": file_sha256(artifact),
            "origin_url": mirror_url,
            "license": "upstream-distfiles-terms",
            "license_url": f"https://github.com/nQuake/distfiles/tree/{commit}",
            "source_urls": source_urls,
            "redistribution_reviewed": True,
            "urls": [mirror_url],
            "source_commit": commit,
        }
        if isinstance(upstream, dict):
            package_record["release_url"] = upstream["release_url"]
            package_record["release_notes"] = str(release_metadata.get("notes", ""))
            package_record["upstream_version"] = upstream["release"]
        packages.append(package_record)
    manifest = {
        "format": 1,
        "project": "x86qw",
        "release": release,
        "source_commit": commit,
        "release_inventory": NQUAKE_RELEASES.name,
        "packages": packages,
    }
    (release_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def register_packages(catalog_path: Path, manifest: dict[str, object]) -> None:
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    packages = catalog.get("packages")
    if not isinstance(packages, list):
        raise ValueError("catalog packages must be a list")
    for package in manifest["packages"]:
        identity = (package["component"], package["package"], package["version"])
        existing = [item for item in packages if isinstance(item, dict) and (
            item.get("component"), item.get("package", item.get("component")), item.get("version")
        ) == identity]
        if existing:
            candidate = existing[0]
            same_payload = all(
                candidate.get(key) == value
                for key, value in package.items() if key != "urls"
            )
            candidate_urls = candidate.get("urls")
            package_urls = package.get("urls")
            mirrors_extend_primary = (
                isinstance(candidate_urls, list)
                and isinstance(package_urls, list)
                and candidate_urls[:len(package_urls)] == package_urls
            )
            if len(existing) != 1 or not same_payload or not mirrors_extend_primary:
                raise ValueError(f"published package identity changed: {identity}")
            continue
        packages[:] = [item for item in packages if not isinstance(item, dict) or (
            item.get("component"), item.get("package", item.get("component")),
            item.get("channel"), item.get("platform"), item.get("architecture"),
        ) != (
            package["component"], package["package"], package["channel"],
            package["platform"], package["architecture"],
        )]
        packages.append(package)
    packages.sort(key=lambda item: (
        str(item.get("component", "")), str(item.get("package", item.get("component", ""))),
        str(item.get("version", "")), str(item.get("platform", "")),
    ))
    catalog["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    validate_catalog(catalog)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".catalog-", suffix=".json", dir=catalog_path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
            json.dump(catalog, destination, ensure_ascii=False, indent=2)
            destination.write("\n")
        os.replace(temporary, catalog_path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=ROOT / "archive")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--register", action="store_true", help="registra os pacotes no catálogo público")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    arguments = parser.parse_args()
    manifest = build_packages(arguments.archive.resolve(), arguments.output.resolve())
    if arguments.register:
        register_packages(arguments.catalog.resolve(), manifest)
    print(f"built {len(manifest['packages'])} package(s) for {manifest['release']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        print(f"nQuake package build failed: {error}")
        raise SystemExit(1)
