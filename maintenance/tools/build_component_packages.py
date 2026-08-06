#!/usr/bin/env python3
"""Build deterministic x86QW component packages from their preserved sources."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

try:
    from .build_artifacts import (
        exact_bytes_validator,
        publish_verified_file,
        staged_artifact,
    )
    from .component_sources import load_source_context, resolve_component_payloads, rewrite_zip_members
    from .validate_catalog import DEFAULT_CATALOG, validate_catalog
except ImportError:  # Execucao direta
    from build_artifacts import exact_bytes_validator, publish_verified_file, staged_artifact
    from component_sources import load_source_context, resolve_component_payloads, rewrite_zip_members
    from validate_catalog import DEFAULT_CATALOG, validate_catalog


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from x86qw_runtime.io.archive import ArchiveError, read_archive_members, scan_archive
from maintenance.tools import release_ownership

COMPONENT_CATALOG = ROOT / "maintenance/inventory/components.json"
COMPONENT_RELEASES = ROOT / "maintenance/inventory/component-releases.json"
FIXED_ZIP_TIME = (2020, 1, 1, 0, 0, 0)
PRIMARY_GITHUB_REPOSITORY = "x86dx2/x86qw"
GITLAB_PROJECT_ID = "84813414"


def component_package_metadata(
    identifier: str,
    version: str,
    strategy: str,
    source_revision: str,
    members: list[dict[str, object]],
    reference_revision: str | None = None,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "format": 1,
        "project": "x86qw",
        "package": identifier,
        "members": members,
    }
    metadata[
        "source_revision" if strategy in {"upstream-package", "upstream-composed"} else "source_commit"
    ] = source_revision
    if reference_revision is not None:
        metadata["source_commit"] = reference_revision
    if version != source_revision[:12]:
        metadata["version"] = version
    return metadata


def _validate_project_ref(value: str | None, *, required: bool) -> str | None:
    if value is None:
        if required:
            raise ValueError("--project-ref é obrigatório quando --ownership-output é usado")
        return None
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise ValueError("project-ref deve ser um SHA-1 hexadecimal minúsculo completo")
    return value


def _project_license_url(project_ref: str) -> str:
    return f"https://github.com/x86dx2/x86qw/blob/{project_ref}/LICENSE"


def _member_ownership(
    source: str,
    overrides: list[dict[str, object]],
    *,
    commit: str,
) -> tuple[str, str, str | None, str]:
    """Classify bytes using explicit builder markers, never a path suffix."""

    markers = [item.get("ownership") for item in overrides if isinstance(item, dict)]
    if "mixed" in markers or any(
        isinstance(item, dict) and item.get("archive_member_override") is True
        for item in overrides
    ):
        return "mixed", "composed-archive", None, "NOASSERTION"
    if "project" in markers:
        return "project", "project-override", _project_license_url(commit), release_ownership.PROJECT_COPYRIGHT
    # The source provenance is recorded in component.json; a source without an
    # explicit project marker remains upstream, even when its spelling happens
    # to contain a project-looking directory.
    return "upstream", "upstream-release", None, "NOASSERTION"


def _ownership_entry(
    *,
    path: str,
    payload: bytes,
    kind: str,
    ownership: str,
    basis: str,
    source: str,
    license_url: str | None,
    copyright_text: str,
    members: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    license_concluded = "MIT" if ownership == "project" else "NOASSERTION"
    return {
        "path": path,
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "kind": kind,
        "ownership": ownership,
        "ownership_basis": basis,
        "source": source,
        "license_concluded": license_concluded,
        "license_url": license_url,
        "copyright_text": copyright_text,
        "members": members or [],
    }


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
    ownership_output: Path | None = None,
    project_ref: str | None = None,
) -> dict[str, object]:
    context = load_source_context(distribution, component_catalog, component_releases)
    components = context.components
    commit = context.commit
    project_ref = _validate_project_ref(project_ref, required=ownership_output is not None)
    reference_release = f"nquake-{commit}"
    build_id = f"components-{commit}"
    release_root = output / build_id
    packages = []
    ownership_entries: list[dict[str, object]] = []
    for identifier in components:
        release_metadata, source_revision, payloads = resolve_component_payloads(context, identifier)
        version = str(release_metadata["version"])
        strategy = str(release_metadata["strategy"])
        filename = f"{identifier}-{version}.zip"
        artifact = release_root / filename
        members: list[dict[str, object]] = []
        ownership_members: list[dict[str, object]] = []
        with staged_artifact(
            artifact, root=output, prefix=f".{identifier}-",
        ) as staged:
            with zipfile.ZipFile(staged.stream, "w", allowZip64=True) as package:
                for upstream_path, member_name, payload, overrides in payloads:
                    member_metadata: dict[str, object] = {
                        "path": member_name,
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "source": upstream_path,
                    }
                    if overrides:
                        member_metadata["overrides"] = overrides
                    members.append(member_metadata)
                    if ownership_output is not None:
                        assert project_ref is not None
                        member_ownership, member_basis, member_license_url, member_copyright = _member_ownership(
                            upstream_path, overrides, commit=project_ref,
                        )
                        ownership_members.append(_ownership_entry(
                            path=member_name,
                            payload=payload,
                            kind="archive" if member_name.casefold().endswith((".zip", ".pk3")) else "file",
                            ownership=member_ownership,
                            basis=member_basis,
                            source=upstream_path,
                            license_url=member_license_url,
                            copyright_text=member_copyright,
                        ))
                    info, data = zip_member(member_name, payload)
                    package.writestr(info, data)
                metadata = component_package_metadata(
                    identifier,
                    version,
                    strategy,
                    source_revision,
                    members,
                    commit if release_metadata.get("archive_layers") else None,
                )
                package_metadata = json.dumps(
                    metadata, ensure_ascii=False, indent=2, sort_keys=True,
                ).encode() + b"\n"
                if ownership_output is not None:
                    assert project_ref is not None
                    ownership_members.append(_ownership_entry(
                        path="_x86qw/component.json",
                        payload=package_metadata,
                        kind="metadata",
                        ownership="project",
                        basis="generated-project-metadata",
                        source="generated:component-metadata",
                        license_url=_project_license_url(project_ref),
                        copyright_text=release_ownership.PROJECT_COPYRIGHT,
                    ))
                info, data = zip_member("_x86qw/component.json", package_metadata)
                package.writestr(info, data)
            staged.seal()

            def validate(path: Path):
                try:
                    plan = scan_archive(
                        path,
                        required_members=(
                            *(str(member["path"]) for member in members),
                            "_x86qw/component.json",
                        ),
                    )
                    read_archive_members(plan, ())
                except ArchiveError as error:
                    raise ValueError(
                        f"component package failed canonical archive validation: {path}: {error}"
                    ) from error
                return plan

            plan = publish_verified_file(
                staged,
                artifact,
                validate=validate,
                fingerprint=lambda value: (value.source_size, value.source_sha256),
                conflict_message=f"component package target already differs: {artifact}",
            )
        if ownership_output is not None:
            assert project_ref is not None
            package_ownership = "project" if all(
                entry["ownership"] == "project" for entry in ownership_members
            ) else "mixed"
            package_basis = "build-output" if package_ownership == "project" else "composed-archive"
            package_license_url = _project_license_url(project_ref) if package_ownership == "project" else None
            package_copyright = (
                release_ownership.PROJECT_COPYRIGHT if package_ownership == "project" else "NOASSERTION"
            )
            ownership_entries.append(_ownership_entry(
                path=f"content/{artifact.relative_to(output).as_posix()}",
                payload=artifact.read_bytes(),
                kind="archive",
                ownership=package_ownership,
                basis=package_basis,
                source="build-component-package",
                license_url=package_license_url,
                copyright_text=package_copyright,
                members=ownership_members,
            ))
        distribution_tag = str(release_metadata.get("distribution_tag", reference_release))
        mirror_url = f"https://github.com/{PRIMARY_GITHUB_REPOSITORY}/releases/download/{distribution_tag}/{filename}"
        gitlab_url = (
            f"https://gitlab.com/api/v4/projects/{GITLAB_PROJECT_ID}/packages/generic/"
            f"{identifier}/{version}/{filename}"
        )
        mirror_title = (
            f"x86QW Content · nQuake {commit[:12]}"
            if distribution_tag == reference_release
            else f"x86QW Content · {components[identifier]['label']} {version}"
        )
        uses_reference = strategy not in {"upstream-package", "upstream-composed"} or bool(
            release_metadata.get("archive_layers")
        )
        source_urls = [f"https://github.com/nQuake/distfiles/tree/{commit}"] if uses_reference else []
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
            "size": plan.source_size,
            "sha256": plan.source_sha256,
            "origin_url": mirror_url,
            "license": str(release_metadata.get("license", "upstream-distfiles-terms")),
            "license_url": str(release_metadata.get("license_url", f"https://github.com/nQuake/distfiles/tree/{commit}")),
            "source_urls": source_urls,
            "redistribution_reviewed": True,
            "urls": [mirror_url, gitlab_url],
            "mirror_title": mirror_title,
            "mirror_notes": "Pacotes de conteúdo versionados da distribuição x86QW.",
            "mirror_latest": False,
        }
        if strategy in {"upstream-package", "upstream-composed"}:
            package_record["source_revision"] = source_revision
            if release_metadata.get("archive_layers"):
                package_record["source_commit"] = commit
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
    manifest_path = release_root / "manifest.json"
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    with staged_artifact(
        manifest_path, root=output, prefix=".manifest-",
    ) as staged:
        staged.stream.write(manifest_bytes)
        staged.seal()
        publish_verified_file(
            staged,
            manifest_path,
            validate=exact_bytes_validator(manifest_bytes),
            fingerprint=lambda value: value,
            conflict_message=f"component manifest already differs: {manifest_path}",
        )
    if ownership_output is not None:
        assert project_ref is not None
        ownership_entries.append(_ownership_entry(
            path=f"content/{manifest_path.relative_to(output).as_posix()}",
            payload=manifest_path.read_bytes(),
            kind="metadata",
            ownership="project",
            basis="generated-project-metadata",
            source="generated:component-manifest",
            license_url=_project_license_url(project_ref),
            copyright_text=release_ownership.PROJECT_COPYRIGHT,
        ))
        ownership_document = release_ownership.validate_document({
            "format": 1,
            "project": "x86qw",
            "artifacts": ownership_entries,
        })
        release_ownership.write_document(Path(ownership_output), ownership_document)
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
            publication_fields = {
                "component", "origin_url", "release_url", "urls",
                "mirror_title", "mirror_notes", "mirror_latest",
            }
            same_payload = all(
                candidate.get(key) == value
                for key, value in package.items() if key not in publication_fields
            )
            if len(existing) != 1 or not same_payload:
                raise ValueError(f"published package identity changed: {identity}")
            candidate["component"] = package["component"]
            for field in publication_fields:
                if field in package:
                    candidate[field] = package[field]
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
    parser.add_argument("--ownership-output", type=Path)
    parser.add_argument("--project-ref", help="SHA-1 do commit x86QW ligado ao fragmento de ownership")
    parser.add_argument("--register", action="store_true", help="registra os pacotes no catálogo público")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    arguments = parser.parse_args()
    manifest = build_packages(
        arguments.distribution.resolve(),
        arguments.output.resolve(),
        ownership_output=arguments.ownership_output,
        project_ref=arguments.project_ref,
    )
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
