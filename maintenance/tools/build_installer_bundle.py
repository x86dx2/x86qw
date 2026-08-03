#!/usr/bin/env python3
"""Build the immutable stdlib-only installer bundle consumed by install.sh."""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

try:
    from .build_artifacts import publish_verified_file, read_regular_file, staged_artifact
    from .components import load_catalog as load_component_catalog, runtime_catalog
    from .runtime_catalog import load_inventory as load_runtime_inventory
    from .validate_catalog import validate_catalog
except ImportError:
    from build_artifacts import publish_verified_file, read_regular_file, staged_artifact
    from components import load_catalog as load_component_catalog, runtime_catalog
    from runtime_catalog import load_inventory as load_runtime_inventory
    from validate_catalog import validate_catalog


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from x86qw_runtime.io.archive import (
    ArchiveError,
    read_archive_members,
    scan_archive,
    validate_installer_bundle,
    validate_installer_history_bundle,
)

VERSION_FILE = ROOT / "dist/installer/VERSION"
MAX_BUILD_INPUT_BYTES = 128 * 1024 * 1024
MAX_TEXT_INPUT_BYTES = 16 * 1024 * 1024
VERSION = read_regular_file(VERSION_FILE, maximum_size=4096).decode("utf-8").strip()
BUNDLE_FILES = (
    ("dist/installer/VERSION", "VERSION", 0o644),
    ("dist/installer/bin/x86qw.sh", "x86qw.sh", 0o755),
    ("dist/installer/bin/x86qw.cmd", "x86qw.cmd", 0o644),
)
ZIPAPP_FILES = (
    ("dist/installer/bin/manager.py", "manager.py"),
    ("dist/installer/bin/python_runtime.py", "python_runtime.py"),
    ("dist/installer/bin/menu.py", "menu.py"),
    ("dist/installer/bin/gameplay.py", "gameplay.py"),
    ("dist/installer/bin/services.py", "services.py"),
    ("dist/installer/bin/session_control.py", "session_control.py"),
    ("dist/mods/ktx/1.47/x86qw/catalog/modes.json", "_x86qw/ktx-modes.json"),
    ("dist/mods/ktx/1.47/x86qw/catalog/frogbots/names.json", "_x86qw/ktx-frogbot-names.json"),
    ("maintenance/__init__.py", "maintenance/__init__.py"),
    ("maintenance/tools/__init__.py", "maintenance/tools/__init__.py"),
    ("maintenance/tools/components.py", "maintenance/tools/components.py"),
    ("maintenance/tools/downloader.py", "maintenance/tools/downloader.py"),
    ("maintenance/tools/runtime_catalog.py", "maintenance/tools/runtime_catalog.py"),
    ("x86qw_runtime/__init__.py", "x86qw_runtime/__init__.py"),
    ("x86qw_runtime/io/__init__.py", "x86qw_runtime/io/__init__.py"),
    ("x86qw_runtime/io/archive.py", "x86qw_runtime/io/archive.py"),
    ("x86qw_runtime/io/private_fs.py", "x86qw_runtime/io/private_fs.py"),
    ("x86qw_runtime/platform/__init__.py", "x86qw_runtime/platform/__init__.py"),
    ("x86qw_runtime/platform/windows_acl.py", "x86qw_runtime/platform/windows_acl.py"),
)
RUNTIME_CONTRACT_FILES = (
    ("capabilities", "maintenance/inventory/capabilities.json", "_x86qw/capabilities.json"),
    ("runtimes", "maintenance/inventory/runtimes.json", "_x86qw/runtimes.json"),
    ("games", "maintenance/inventory/games.json", "_x86qw/games.json"),
    ("compatibility", "maintenance/inventory/compatibility.json", "_x86qw/compatibility.json"),
)
FIXED_TIME = (2020, 1, 1, 0, 0, 0)
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
PRIMARY_GITHUB_REPOSITORY = "x86dx2/x86qw"
GITLAB_PROJECT_ID = "84813414"
ARCHIVE_SOURCE = ROOT / "x86qw_runtime/io/archive.py"
SHELL_BOOTSTRAP = ROOT / "dist/installer/bin/install.sh"
POWERSHELL_BOOTSTRAP = ROOT / "dist/installer/bin/install.ps1"
PUBLIC_SHELL_BOOTSTRAP = ROOT / "site/public/install.sh"
PUBLIC_POWERSHELL_BOOTSTRAP = ROOT / "site/public/install.ps1"
ARCHIVE_BASE64_ASSIGNMENTS = {
    SHELL_BOOTSTRAP: "ARCHIVE_HELPER_BASE64",
    POWERSHELL_BOOTSTRAP: "$ArchiveHelperBase64",
}
LEGACY_HANDOFF_SHIM = b"""#!/usr/bin/env python3
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[3]
os.execv(sys.executable, [sys.executable, str(root / "x86qw.pyz"), *sys.argv[1:]])
"""


def bundle_files() -> tuple[str, ...]:
    return tuple(source for source, _, _ in BUNDLE_FILES) + tuple(
        source for source, _ in ZIPAPP_FILES
    ) + tuple(source for _, source, _ in RUNTIME_CONTRACT_FILES)


def runtime_catalog_bytes() -> bytes:
    source = load_component_catalog(ROOT / "maintenance/inventory/components.json")
    return json.dumps(
        runtime_catalog(source), ensure_ascii=False, indent=2, sort_keys=True,
    ).encode("utf-8") + b"\n"


def json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, indent=2, sort_keys=True,
    ).encode("utf-8") + b"\n"


def write_member(
    archive: zipfile.ZipFile, name: str, payload: bytes, mode: int = 0o644,
) -> None:
    if mode not in {0o644, 0o755}:
        raise ValueError(f"installer ZIP member mode is not canonical: {name}: {mode:o}")
    info = zipfile.ZipInfo(name, FIXED_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | mode) << 16
    archive.writestr(info, payload)


def zipapp_bytes(version: str) -> bytes:
    output = io.BytesIO()
    identity = {"format": 1, "project": "x86qw", "version": version}
    component_catalog = load_component_catalog(ROOT / "maintenance/inventory/components.json")
    runtime_inventory = load_runtime_inventory(
        ROOT / "maintenance/inventory", component_catalog=component_catalog,
    )
    with zipfile.ZipFile(output, "w", allowZip64=True) as application:
        write_member(
            application,
            "__main__.py",
            (
                b"import sys\n"
                b"from python_runtime import require_supported_runtime, UnsupportedPythonError\n"
                b"try:\n"
                b"    require_supported_runtime()\n"
                b"except UnsupportedPythonError as error:\n"
                b"    print(f'[ERRO] {error}', file=sys.stderr)\n"
                b"    raise SystemExit(2)\n"
                b"from manager import main\n"
                b"raise SystemExit(main())\n"
            ),
        )
        for source_name, member in ZIPAPP_FILES:
            source = ROOT / source_name
            write_member(
                application,
                member,
                read_regular_file(source, maximum_size=MAX_BUILD_INPUT_BYTES),
            )
        for key, _, member in RUNTIME_CONTRACT_FILES:
            write_member(application, member, json_bytes(runtime_inventory[key]))
        write_member(application, "_x86qw/installer.json", json_bytes(identity))
        write_member(application, "_x86qw/components.json", runtime_catalog_bytes())
    payload = output.getvalue()
    required = (
        "__main__.py",
        *(member for _, member in ZIPAPP_FILES),
        *(member for _, _, member in RUNTIME_CONTRACT_FILES),
        "_x86qw/installer.json",
        "_x86qw/components.json",
    )
    try:
        plan = scan_archive(payload, required_members=required)
    except ArchiveError as error:
        raise ValueError(f"installer zipapp failed archive validation: {error}") from error
    if set(plan.member_names) != set(required):
        raise ValueError("installer zipapp contains an unexpected member")
    return payload


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
        try:
            plan = validate_installer_history_bundle(archive, version)
            read_archive_members(plan, ())
        except ArchiveError as error:
            raise ValueError(
                f"installer history bundle failed canonical archive validation: {archive}: {error}"
            ) from error
        results.append({
            "version": version,
            "filename": filename,
            "distribution_path": archive.relative_to(ROOT / "dist").as_posix(),
            "size": plan.source_size,
            "sha256": plan.source_sha256,
        })
    return sorted(results, key=lambda item: version_key(str(item["version"])))


def update_latest_link(package_root: Path) -> str:
    packages = package_results(package_root)
    if not packages:
        raise ValueError("installer package history is empty")
    version = str(packages[-1]["version"])
    latest = package_root / "latest"
    expected = Path(version)
    if os.path.lexists(latest):
        if latest.is_symlink() and os.readlink(latest) == expected.as_posix():
            return version
        raise ValueError(
            "installer latest pointer differs from the selected immutable package; "
            "candidate promotion requires the dedicated release transaction"
        )
    try:
        latest.symlink_to(expected, target_is_directory=True)
    except FileExistsError:
        if latest.is_symlink() and os.readlink(latest) == expected.as_posix():
            return version
        raise ValueError(
            f"installer latest pointer changed concurrently and was preserved: {latest}"
        ) from None
    return version


def _path_stat_identity(path: Path) -> tuple[int, int, int, int, int, int]:
    metadata = path.lstat()
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_mode),
        int(metadata.st_size),
        int(getattr(metadata, "st_mtime_ns", metadata.st_mtime * 1_000_000_000)),
        int(getattr(metadata, "st_ctime_ns", metadata.st_ctime * 1_000_000_000)),
    )


def _fsync_parent(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(
        path.parent,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def update_public_bootstrap(path: Path, assignments: dict[str, str]) -> None:
    try:
        initial_identity = _path_stat_identity(path)
        original = read_regular_file(path, maximum_size=MAX_TEXT_INPUT_BYTES)
        if _path_stat_identity(path) != initial_identity:
            raise ValueError(f"public bootstrap changed while reading: {path}")
    except OSError as error:
        raise ValueError(f"public bootstrap is unavailable: {path}") from error
    try:
        content = original.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"public bootstrap is not valid UTF-8: {path}") from error
    for name, value in assignments.items():
        pattern = rf'(?m)^({re.escape(name)}[ \t]*=[ \t]*)"[^"\r\n]*"(\r?)$'
        content, count = re.subn(pattern, rf'\g<1>"{value}"\g<2>', content)
        if count != 1:
            raise ValueError(f"public bootstrap assignment is missing or duplicated: {path}:{name}")
    updated = content.encode("utf-8")
    mode = stat.S_IMODE(initial_identity[2])
    with staged_artifact(path, root=path.parent, prefix=f".{path.name}.") as staged:
        if staged.stream.write(updated) != len(updated):
            raise OSError(f"public bootstrap write was incomplete: {path}")
        staged.seal(mode=mode)
        current = read_regular_file(path, maximum_size=MAX_TEXT_INPUT_BYTES)
        if current != original or _path_stat_identity(path) != initial_identity:
            raise ValueError(f"public bootstrap changed before replacement: {path}")
        os.replace(staged.path, path)
        if read_regular_file(path, maximum_size=len(updated)) != updated:
            raise ValueError(f"public bootstrap changed during replacement: {path}")
        _fsync_parent(path)


def archive_source_bytes() -> bytes:
    return read_regular_file(ARCHIVE_SOURCE, maximum_size=MAX_TEXT_INPUT_BYTES)


def archive_source_base64() -> str:
    return base64.b64encode(archive_source_bytes()).decode("ascii")


def embedded_archive_source(path: Path, assignment: str) -> bytes:
    try:
        content = read_regular_file(path, maximum_size=MAX_TEXT_INPUT_BYTES).decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"bootstrap source is not valid UTF-8: {path}") from error
    pattern = (
        rf'(?m)^{re.escape(assignment)}[ \t]*=[ \t]*'
        rf'"([A-Za-z0-9+/]*={{0,2}})"\r?$'
    )
    matches = re.findall(pattern, content)
    if len(matches) != 1:
        raise ValueError(
            f"bootstrap archive source assignment is missing or duplicated: {path}:{assignment}"
        )
    try:
        return base64.b64decode(matches[0], validate=True)
    except ValueError as error:
        raise ValueError(f"bootstrap archive source is not valid Base64: {path}") from error


def synchronize_bootstrap_archive_source() -> None:
    encoded = archive_source_base64()
    for path, assignment in ARCHIVE_BASE64_ASSIGNMENTS.items():
        update_public_bootstrap(path, {assignment: encoded})
    shutil.copyfile(SHELL_BOOTSTRAP, PUBLIC_SHELL_BOOTSTRAP)
    shutil.copyfile(POWERSHELL_BOOTSTRAP, PUBLIC_POWERSHELL_BOOTSTRAP)
    validate_bootstrap_archive_source()


def validate_bootstrap_archive_source() -> None:
    expected = archive_source_bytes()
    for canonical, public in (
        (SHELL_BOOTSTRAP, PUBLIC_SHELL_BOOTSTRAP),
        (POWERSHELL_BOOTSTRAP, PUBLIC_POWERSHELL_BOOTSTRAP),
    ):
        if (
            read_regular_file(canonical, maximum_size=MAX_TEXT_INPUT_BYTES)
            != read_regular_file(public, maximum_size=MAX_TEXT_INPUT_BYTES)
        ):
            raise ValueError(f"public bootstrap differs from canonical source: {public}")
        assignment = ARCHIVE_BASE64_ASSIGNMENTS[canonical]
        if embedded_archive_source(canonical, assignment) != expected:
            raise ValueError(f"bootstrap archive source is stale: {canonical}")


def public_bootstrap_assignments(
    record: dict[str, object],
) -> tuple[dict[str, str], dict[str, str]]:
    values = {
        "version": str(record["version"]),
        "sha256": str(record["sha256"]),
        "size": str(record["size"]),
    }
    return (
        {
            "INSTALLER_VERSION": values["version"],
            "INSTALLER_SHA256": values["sha256"],
            "INSTALLER_SIZE": values["size"],
        },
        {
            "$InstallerVersion": values["version"],
            "$InstallerSha256": values["sha256"],
            "$InstallerSize": values["size"],
        },
    )


def reset_history(package_root: Path) -> None:
    """Remove the local installer release history before a deliberate version reset."""
    if package_root != ROOT / "dist/installer/packages":
        raise ValueError("installer history reset is restricted to the canonical package directory")
    if package_root.exists():
        for entry in package_root.iterdir():
            if entry.name == "latest" and entry.is_symlink():
                entry.unlink()
            elif entry.is_dir() and VERSION_PATTERN.fullmatch(entry.name):
                shutil.rmtree(entry)
            else:
                raise ValueError(f"unexpected entry blocks installer history reset: {entry}")

    catalog_path = ROOT / "site/public/api/v1/catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    packages = catalog.get("packages")
    if not isinstance(packages, list):
        raise ValueError("catalog packages must be a list")
    packages[:] = [
        package for package in packages
        if not isinstance(package, dict) or package.get("component") != "installer"
    ]
    catalog_path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    manifest_path = ROOT / "dist/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise ValueError("distribution manifest files must be an object")
    for path, metadata in dict(files).items():
        if isinstance(metadata, dict) and metadata.get("component") == "installer":
            del files[path]
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build(output: Path, version: str = VERSION) -> dict[str, object]:
    validate_bootstrap_archive_source()
    filename = f"x86qw-installer-{version}.zip"
    target = output / version / filename
    with staged_artifact(target, root=output, prefix=".installer-") as staged:
        with zipfile.ZipFile(staged.stream, "w", allowZip64=True) as bundle:
            prefix = f"x86qw-installer-{version}"
            write_member(bundle, f"{prefix}/x86qw.pyz", zipapp_bytes(version))
            for source_name, member, mode in BUNDLE_FILES:
                source = ROOT / source_name
                write_member(
                    bundle,
                    f"{prefix}/{member}",
                    read_regular_file(source, maximum_size=MAX_BUILD_INPUT_BYTES),
                    mode,
                )
            write_member(
                bundle,
                f"{prefix}/installer.json",
                json_bytes({"format": 1, "project": "x86qw", "version": version}),
            )
            write_member(
                bundle,
                f"{prefix}/dist/installer/bin/manager.py",
                LEGACY_HANDOFF_SHIM,
                0o755,
            )
            write_member(
                bundle,
                f"{prefix}/_x86qw/installer.json",
                json_bytes({"format": 1, "project": "x86qw", "version": version}),
            )
        staged.seal()

        def validate(path: Path):
            try:
                plan = validate_installer_bundle(path, version)
                read_archive_members(plan, ())
            except ArchiveError as error:
                raise ValueError(f"installer bundle failed archive validation: {error}") from error
            return plan

        accepted_plan = publish_verified_file(
            staged,
            target,
            validate=validate,
            fingerprint=lambda value: (value.source_size, value.source_sha256),
            conflict_message=(
                f"installer version {version} is immutable; bump VERSION before rebuilding"
            ),
        )
    result = {
        "version": version,
        "filename": filename,
        "distribution_path": target.relative_to(ROOT / "dist").as_posix(),
        "size": accepted_plan.source_size,
        "sha256": accepted_plan.source_sha256,
    }
    update_latest_link(output)
    return result


def installer_record(result: dict[str, object], *, current: bool) -> dict[str, object]:
    version = str(result["version"])
    filename = str(result["filename"])
    release_notes_path = ROOT / "docs" / "releases" / f"{version}.md"
    release_notes = (
        release_notes_path.read_text(encoding="utf-8").strip()
        if release_notes_path.is_file() and not release_notes_path.is_symlink()
        else "Bootstrap autocontido do instalador público x86QW."
    )
    mirror_notes = (
        release_notes
        if release_notes_path.is_file() and not release_notes_path.is_symlink()
        else "Instalador público e atualizador da distribuição x86QW."
    )
    github_repository = PRIMARY_GITHUB_REPOSITORY
    release_tag = f"x86qw-installer-{version}"
    github = (
        f"https://github.com/{github_repository}/releases/download/"
        f"{release_tag}/{filename}"
    )
    gitlab = (
        f"https://gitlab.com/api/v4/projects/{GITLAB_PROJECT_ID}/packages/generic/"
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
        "release_notes": release_notes,
        "mirror_title": f"x86QW Installer {version}",
        "mirror_notes": mirror_notes,
        "mirror_latest": current,
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
    shell_bootstrap = SHELL_BOOTSTRAP
    powershell_bootstrap = POWERSHELL_BOOTSTRAP
    shell_assignments, powershell_assignments = public_bootstrap_assignments(current_record)
    update_public_bootstrap(shell_bootstrap, shell_assignments)
    update_public_bootstrap(powershell_bootstrap, powershell_assignments)
    shutil.copyfile(shell_bootstrap, PUBLIC_SHELL_BOOTSTRAP)
    shutil.copyfile(powershell_bootstrap, PUBLIC_POWERSHELL_BOOTSTRAP)
    validate_bootstrap_archive_source()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/installer/packages")
    parser.add_argument("--version", default=VERSION)
    parser.add_argument("--register", action="store_true")
    parser.add_argument(
        "--reset-history",
        action="store_true",
        help="descarta todas as versões locais antes de reconstruir a versão informada",
    )
    options = parser.parse_args()
    if options.reset_history:
        reset_history(options.output.resolve())
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
