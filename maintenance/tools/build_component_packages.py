#!/usr/bin/env python3
"""Build deterministic x86QW component packages from their preserved sources."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

try:
    from .component_sources import load_source_context, resolve_component_payloads, rewrite_zip_members
    from .validate_catalog import DEFAULT_CATALOG, validate_catalog
except ImportError:  # Execucao direta
    from component_sources import load_source_context, resolve_component_payloads, rewrite_zip_members
    from validate_catalog import DEFAULT_CATALOG, validate_catalog


ROOT = Path(__file__).resolve().parents[2]
COMPONENT_CATALOG = ROOT / "maintenance/inventory/components.json"
COMPONENT_RELEASES = ROOT / "maintenance/inventory/component-releases.json"
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


def build_packages(
    distribution: Path,
    output: Path,
    *,
    component_catalog: Path = COMPONENT_CATALOG,
    component_releases: Path = COMPONENT_RELEASES,
) -> dict[str, object]:
    context = load_source_context(distribution, component_catalog, component_releases)
    components = context.components
    commit = context.commit
    reference_release = f"nquake-{commit}"
    build_id = f"components-{commit}"
    release_root = output / build_id
    release_root.mkdir(parents=True, exist_ok=True)
    packages = []
    for identifier in components:
        release_metadata, source_revision, payloads = resolve_component_payloads(context, identifier)
        version = str(release_metadata["version"])
        strategy = str(release_metadata["strategy"])
        filename = f"{identifier}-{version}.zip"
        artifact = release_root / filename
        members: list[dict[str, str]] = []
        with zipfile.ZipFile(artifact, "w", allowZip64=True) as package:
            for upstream_path, member_name, payload, overrides in payloads:
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
                "members": members,
            }
            metadata["source_revision" if strategy == "upstream-package" else "source_commit"] = source_revision
            if release_metadata["strategy"] != "reference-snapshot":
                metadata["version"] = version
            package_metadata = json.dumps(
                metadata, ensure_ascii=False, indent=2, sort_keys=True,
            ).encode() + b"\n"
            info, data = zip_member("_x86qw/component.json", package_metadata)
            package.writestr(info, data)
        distribution_tag = str(release_metadata.get("distribution_tag", reference_release))
        mirror_url = f"https://github.com/x86dx2/x86qw-dist/releases/download/{distribution_tag}/{filename}"
        source_urls = [] if strategy == "upstream-package" else [f"https://github.com/nQuake/distfiles/tree/{commit}"]
        upstream = release_metadata.get("upstream")
        if isinstance(upstream, dict):
            source_url = upstream.get("source_url")
            release_url = upstream.get("release_url")
            source_urls.extend(str(url) for url in (source_url, release_url) if isinstance(url, str))
        source_urls.extend(str(url) for url in release_metadata.get("source_mirrors", []))
        package_record = {
            "component": str(release_metadata.get("distribution_component", "nquake")),
            "package": identifier,
            "version": version,
            "channel": "content",
            "platform": "any",
            "architecture": "any",
            "filename": filename,
            "size": artifact.stat().st_size,
            "sha256": file_sha256(artifact),
            "origin_url": mirror_url,
            "license": str(release_metadata.get("license", "upstream-distfiles-terms")),
            "license_url": str(release_metadata.get("license_url", f"https://github.com/nQuake/distfiles/tree/{commit}")),
            "source_urls": source_urls,
            "redistribution_reviewed": True,
            "urls": [mirror_url],
        }
        if strategy == "upstream-package":
            package_record["source_revision"] = source_revision
        else:
            package_record["source_commit"] = commit
        if isinstance(upstream, dict):
            package_record["release_url"] = upstream["release_url"]
            package_record["release_notes"] = str(release_metadata.get("notes", ""))
            package_record["upstream_version"] = upstream["release"]
        packages.append(package_record)
    manifest = {
        "format": 1,
        "project": "x86qw",
        "release": build_id,
        "source_commit": commit,
        "release_inventory": component_releases.name,
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
    current_components = {
        str(package["package"])
        for package in manifest["packages"]
        if isinstance(package, dict)
    }
    packages[:] = [
        package for package in packages
        if not (
            isinstance(package, dict)
            and package.get("channel") == "content"
            and package.get("component") != "installer"
            and package.get("package", package.get("component")) not in current_components
        )
    ]
    for package in manifest["packages"]:
        identity = (package["package"], package["version"])
        existing = [item for item in packages if isinstance(item, dict) and (
            item.get("package", item.get("component")), item.get("version")
        ) == identity]
        if existing:
            candidate = existing[0]
            same_payload = all(
                candidate.get(key) == value
                for key, value in package.items() if key not in {"component", "urls"}
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
            candidate["component"] = package["component"]
            candidate.pop("distribution_path", None)
            continue
        packages[:] = [item for item in packages if not isinstance(item, dict) or (
            item.get("package", item.get("component")),
            item.get("channel"), item.get("platform"), item.get("architecture"),
        ) != (
            package["package"], package["channel"],
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
    parser.add_argument("--distribution", type=Path, default=ROOT / "dist")
    parser.add_argument("--output", type=Path, default=ROOT / "maintenance/build/packages")
    parser.add_argument("--register", action="store_true", help="registra os pacotes no catálogo público")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    arguments = parser.parse_args()
    manifest = build_packages(arguments.distribution.resolve(), arguments.output.resolve())
    if arguments.register:
        register_packages(arguments.catalog.resolve(), manifest)
    print(f"built {len(manifest['packages'])} package(s) for {manifest['release']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        print(f"component package build failed: {error}")
        raise SystemExit(1)
