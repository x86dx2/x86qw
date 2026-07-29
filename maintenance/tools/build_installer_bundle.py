#!/usr/bin/env python3
"""Build the immutable stdlib-only installer bundle consumed by install.sh."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

try:
    from .validate_catalog import validate_catalog
except ImportError:
    from validate_catalog import validate_catalog


ROOT = Path(__file__).resolve().parents[2]
VERSION = "1.0.8"
FILES = (
    "install-qw.py",
    "play-qw.py",
    "maintenance/__init__.py",
    "maintenance/tools/__init__.py",
    "maintenance/tools/components.py",
    "maintenance/tools/component_sources.py",
    "maintenance/tools/component_releases.py",
    "maintenance/inventory/components.json",
    "maintenance/inventory/component-releases.json",
    "dist/id1/pak0.pak",
    "dist/id1/pak1.pak",
)
FIXED_TIME = (2020, 1, 1, 0, 0, 0)


def bundle_files() -> tuple[str, ...]:
    files = list(FILES)
    catalog = json.loads((ROOT / "maintenance/inventory/components.json").read_text(encoding="utf-8"))
    for component in catalog.get("components", []):
        if not isinstance(component, dict):
            continue
        for entry in component.get("project_sources", []):
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise ValueError("component project source is invalid")
            if entry["path"] not in files:
                files.append(entry["path"])
    return tuple(files)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build(output: Path, version: str = VERSION) -> dict[str, object]:
    filename = f"x86qw-installer-{version}.zip"
    target = output / version / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".installer-", suffix=".zip", dir=target.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", allowZip64=True) as bundle:
            for relative in bundle_files():
                source = ROOT / relative
                if not source.is_file() or source.is_symlink():
                    raise ValueError(f"installer input is missing or unsafe: {source}")
                info = zipfile.ZipInfo(f"x86qw-installer-{version}/{relative}", FIXED_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                executable = relative in {"install-qw.py", "play-qw.py"}
                info.external_attr = (stat.S_IFREG | (0o755 if executable else 0o644)) << 16
                bundle.writestr(info, source.read_bytes())
            identity = json.dumps(
                {"format": 1, "project": "x86qw", "version": version},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ).encode("utf-8") + b"\n"
            info = zipfile.ZipInfo(
                f"x86qw-installer-{version}/_x86qw/installer.json", FIXED_TIME,
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            bundle.writestr(info, identity)
        if target.is_file() and sha256(target) != sha256(temporary):
            raise ValueError(f"installer version {version} is immutable; bump VERSION before rebuilding")
        if not target.exists():
            os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {
        "version": version,
        "filename": filename,
        "distribution_path": target.relative_to(ROOT / "dist").as_posix(),
        "size": target.stat().st_size,
        "sha256": sha256(target),
    }


def register(result: dict[str, object]) -> None:
    version = str(result["version"])
    filename = str(result["filename"])
    github = (
        f"https://github.com/x86dx2/x86qw-dist/releases/download/"
        f"installer-{version}/{filename}"
    )
    gitlab = (
        "https://gitlab.com/api/v4/projects/84856335/packages/generic/"
        f"x86qw-installer/{version}/{filename}"
    )
    record = {
        "component": "installer",
        "package": "x86qw-installer",
        "version": version,
        "channel": "content",
        "platform": "any",
        "architecture": "any",
        "filename": filename,
        "size": result["size"],
        "sha256": result["sha256"],
        "origin_url": github,
        "license": "x86qw-project-terms",
        "license_url": "https://github.com/x86dx2/x86qw",
        "source_urls": ["https://github.com/x86dx2/x86qw"],
        "redistribution_reviewed": True,
        "urls": [github, gitlab],
        "distribution_path": result["distribution_path"],
        "release_url": f"https://github.com/x86dx2/x86qw-dist/releases/tag/installer-{version}",
        "release_notes": "Bootstrap autocontido do instalador público x86QW.",
    }

    catalog_path = ROOT / "site/public/api/v1/catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    packages = catalog.get("packages")
    if not isinstance(packages, list):
        raise ValueError("catalog packages must be a list")
    existing = [
        package for package in packages
        if isinstance(package, dict) and package.get("package") == "x86qw-installer"
        and package.get("version") == version
    ]
    if existing and any(
        package.get("sha256") != result["sha256"] or package.get("size") != result["size"]
        for package in existing
    ):
        raise ValueError(f"published installer version {version} cannot change")
    packages[:] = [
        package for package in packages
        if not isinstance(package, dict) or package.get("package") != "x86qw-installer"
    ]
    packages.append(record)
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

    manifest_path = ROOT / "dist/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise ValueError("distribution manifest files must be an object")
    files_copy = dict(files)
    for path, metadata in files_copy.items():
        if (
            path != result["distribution_path"]
            and isinstance(metadata, dict)
            and metadata.get("component") == "installer"
        ):
            del files[path]
    files[str(result["distribution_path"])] = {
        "component": "installer",
        "consumer": "bootstrap:installer",
        "sha256": result["sha256"],
        "size": result["size"],
        "url": github,
    }
    manifest["captured_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/installer")
    parser.add_argument("--version", default=VERSION)
    parser.add_argument("--register", action="store_true")
    options = parser.parse_args()
    result = build(options.output.resolve(), options.version)
    if options.register:
        register(result)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(f"installer bundle build failed: {error}")
        raise SystemExit(1)
