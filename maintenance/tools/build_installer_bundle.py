#!/usr/bin/env python3
"""Build the immutable stdlib-only installer bundle consumed by install.sh."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
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
VERSION = "0.1.2"
FILES = (
    "dist/installer/bin/x86qw.sh",
    "dist/installer/bin/x86qw.cmd",
    "dist/installer/bin/manager.py",
    "dist/installer/bin/gameplay.py",
    "maintenance/__init__.py",
    "maintenance/tools/__init__.py",
    "maintenance/tools/components.py",
    "maintenance/tools/component_sources.py",
    "maintenance/tools/component_releases.py",
    "maintenance/inventory/components.json",
    "maintenance/inventory/component-releases.json",
    "dist/game-data/id1/pak0.pak",
    "dist/game-data/id1/pak1.pak",
)
FIXED_TIME = (2020, 1, 1, 0, 0, 0)
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
PRIMARY_GITHUB_REPOSITORY = "x86dx2/x86qw"


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


def version_key(version: str) -> tuple[int, int, int]:
    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"invalid installer version: {version}")
    return tuple(int(part) for part in version.split("."))  # type: ignore[return-value]


def package_results(package_root: Path) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    if not package_root.is_dir() or package_root.is_symlink():
        raise ValueError(f"installer package directory is missing or unsafe: {package_root}")
    for directory in package_root.iterdir():
        if directory.name == "latest" and directory.is_symlink():
            continue
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError(f"unexpected installer package entry: {directory}")
        version = directory.name
        version_key(version)
        filename = f"x86qw-installer-{version}.zip"
        archive = directory / filename
        if not archive.is_file() or archive.is_symlink():
            raise ValueError(f"installer package is missing or unsafe: {archive}")
        if any(entry.name != filename for entry in directory.iterdir()):
            raise ValueError(f"installer version directory contains an unexpected file: {directory}")
        results.append({
            "version": version,
            "filename": filename,
            "distribution_path": archive.relative_to(ROOT / "dist").as_posix(),
            "size": archive.stat().st_size,
            "sha256": sha256(archive),
        })
    return sorted(results, key=lambda item: version_key(str(item["version"])))


def update_latest_link(package_root: Path) -> str:
    packages = package_results(package_root)
    if not packages:
        raise ValueError("installer package history is empty")
    version = str(packages[-1]["version"])
    latest = package_root / "latest"
    expected = Path(version)
    if os.path.lexists(latest) and not latest.is_symlink():
        raise ValueError(f"installer latest pointer is not a symbolic link: {latest}")
    if latest.is_symlink() and os.readlink(latest) == expected.as_posix():
        return version
    with tempfile.TemporaryDirectory(prefix=".latest-", dir=latest.parent) as temporary:
        replacement = Path(temporary) / "latest"
        replacement.symlink_to(expected, target_is_directory=True)
        os.replace(replacement, latest)
    return version


def update_public_bootstrap(path: Path, assignments: dict[str, str]) -> None:
    content = path.read_text(encoding="utf-8")
    for name, value in assignments.items():
        pattern = rf"(?m)^({re.escape(name)}\s*=\s*)\"[^\"]*\"$"
        content, count = re.subn(pattern, rf'\g<1>"{value}"', content)
        if count != 1:
            raise ValueError(f"public bootstrap assignment is missing or duplicated: {path}:{name}")
    path.write_text(content, encoding="utf-8")


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
                executable = relative in {
                    "dist/installer/bin/x86qw.sh", "dist/installer/bin/manager.py",
                    "dist/installer/bin/gameplay.py",
                }
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
    result = {
        "version": version,
        "filename": filename,
        "distribution_path": target.relative_to(ROOT / "dist").as_posix(),
        "size": target.stat().st_size,
        "sha256": sha256(target),
    }
    update_latest_link(output)
    return result


def installer_record(result: dict[str, object], *, current: bool) -> dict[str, object]:
    version = str(result["version"])
    filename = str(result["filename"])
    github_repository = PRIMARY_GITHUB_REPOSITORY
    release_tag = f"x86qw-installer-{version}"
    github = (
        f"https://github.com/{github_repository}/releases/download/"
        f"{release_tag}/{filename}"
    )
    gitlab = (
        "https://gitlab.com/api/v4/projects/84856335/packages/generic/"
        f"x86qw-installer/{version}/{filename}"
    )
    return {
        "component": "installer",
        "package": "x86qw-installer",
        "version": version,
        "current": current,
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
        "release_url": f"https://github.com/{github_repository}/releases/tag/{release_tag}",
        "release_title": f"x86QW Installer {version}",
        "release_notes": "Bootstrap autocontido do instalador público x86QW.",
    }


def register(result: dict[str, object]) -> None:
    package_root = ROOT / "dist/installer/packages"
    current_version = update_latest_link(package_root)
    discovered = package_results(package_root)
    built = [package for package in discovered if package["version"] == result["version"]]
    if len(built) != 1 or any(
        built[0][field] != result[field]
        for field in ("filename", "distribution_path", "size", "sha256")
    ):
        raise ValueError("only a canonical installer package can be registered")
    records = [
        installer_record(package, current=str(package["version"]) == current_version)
        for package in discovered
    ]
    current_record = next(record for record in records if record["current"] is True)

    catalog_path = ROOT / "site/public/api/v1/catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    packages = catalog.get("packages")
    if not isinstance(packages, list):
        raise ValueError("catalog packages must be a list")
    existing = {
        str(package.get("version")): package
        for package in packages
        if isinstance(package, dict) and package.get("package") == "x86qw-installer"
    }
    for record in records:
        previous = existing.get(str(record["version"]))
        if previous is not None and (
            previous.get("sha256") != record["sha256"]
            or previous.get("size") != record["size"]
        ):
            raise ValueError(f"published installer version {record['version']} cannot change")
    packages[:] = [
        package for package in packages
        if not isinstance(package, dict) or package.get("package") != "x86qw-installer"
    ]
    packages.extend(records)
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
    for path, metadata in dict(files).items():
        if isinstance(metadata, dict) and metadata.get("component") == "installer":
            del files[path]
    for record in records:
        files[str(record["distribution_path"])] = {
            "component": "installer",
            "consumer": (
                "bootstrap:installer" if record["current"] is True
                else "archive:installer-history"
            ),
            "sha256": record["sha256"],
            "size": record["size"],
            "url": record["origin_url"],
        }
    manifest["captured_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    shell_bootstrap = ROOT / "dist/installer/bin/install.sh"
    powershell_bootstrap = ROOT / "dist/installer/bin/install.ps1"
    update_public_bootstrap(shell_bootstrap, {
        "INSTALLER_VERSION": str(current_record["version"]),
        "INSTALLER_SHA256": str(current_record["sha256"]),
    })
    update_public_bootstrap(powershell_bootstrap, {
        "$InstallerVersion": str(current_record["version"]),
        "$InstallerSha256": str(current_record["sha256"]),
    })
    shutil.copyfile(shell_bootstrap, ROOT / "site/public/install.sh")
    shutil.copyfile(powershell_bootstrap, ROOT / "site/public/install.ps1")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/installer/packages")
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
