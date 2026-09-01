#!/usr/bin/env python3
"""Build the immutable stdlib-only installer bundle consumed by install.sh."""

from __future__ import annotations

import argparse
import base64
import hashlib
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
from pathlib import Path, PurePosixPath

try:
    from .build_artifacts import publish_verified_file, read_regular_file, staged_artifact
    from .components import load_catalog as load_component_catalog, runtime_catalog
    from .launcher_contract import validate_public_launcher_contract
    from .runtime_catalog import load_inventory as load_runtime_inventory
    from .validate_catalog import validate_catalog
except ImportError:
    from build_artifacts import publish_verified_file, read_regular_file, staged_artifact
    from components import load_catalog as load_component_catalog, runtime_catalog
    from launcher_contract import validate_public_launcher_contract
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
from x86qw_runtime.io import atomic as atomic_io
from x86qw_runtime.versioning import (
    SEMVER as VERSION_PATTERN,
    parse_semver,
    version_key,
)
from maintenance.tools import release_ownership

VERSION_FILE = ROOT / "dist/installer/VERSION"
MAX_BUILD_INPUT_BYTES = 128 * 1024 * 1024
MAX_TEXT_INPUT_BYTES = 16 * 1024 * 1024
VERSION = read_regular_file(VERSION_FILE, maximum_size=4096).decode("utf-8").strip()
BUNDLE_FILES = (
    ("dist/installer/VERSION", "VERSION", 0o644),
    ("dist/installer/bin/x86qw.sh", "x86qw.sh", 0o755),
    ("dist/installer/bin/x86qw.cmd", "x86qw.cmd", 0o644),
)
# Project-owned notices are carried by the first 1.0 bundle and newer.  They
# are deliberately not part of the 0.x outer layout so immutable historical
# artifacts remain byte-for-byte compatible.
LEGAL_FILES = (
    ("LICENSE", "LICENSE", 0o644),
    ("NOTICE", "NOTICE", 0o644),
)
RUNTIME_MEMBER_MANIFEST = ROOT / "maintenance/inventory/installer-runtime-members.json"
RUNTIME_DEPENDENCY_MANIFEST = ROOT / "maintenance/inventory/runtime-dependencies.json"
RUNTIME_DEPENDENCY_WHEELS = ROOT / "maintenance/vendor/wheels"
TRUST_ROOT_SOURCE = "maintenance/trust/root.json"
TRUST_ROOT_SHA256 = "660af63e52a033290adf8899d2078a779c04e04cf5d1fac465b4aa2e04937201"
RUNTIME_MEMBER_FIELDS = frozenset({"member", "source", "consumer", "contract"})
RUNTIME_DEPENDENCY_FIELDS = frozenset({
    "name", "version", "filename", "sha256", "upstream_sha256", "transformation",
    "license", "source", "package_prefixes",
})
RUNTIME_DEPENDENCY_TRANSFORMATIONS = frozenset({
    "none",
    "add-empty-package-marker:securesystemslib/_internal/__init__.py",
})
GENERATED_RUNTIME_SOURCES = frozenset({
    "generated:entrypoint",
    "generated:capabilities",
    "generated:runtimes",
    "generated:games",
    "generated:identity",
    "generated:component-catalog",
    "generated:runtime-dependencies",
})
STATIC_RUNTIME_SOURCE_PREFIXES = (
    "dist/installer/bin/",
    "dist/installer/assets/",
    "dist/mods/ktx/",
    "maintenance/trust/",
    "x86qw_runtime/",
)
RUNTIME_CONTRACT_SOURCES = (
    ("capabilities", "maintenance/inventory/capabilities.json"),
    ("runtimes", "maintenance/inventory/runtimes.json"),
    ("games", "maintenance/inventory/games.json"),
)
FIXED_TIME = (2020, 1, 1, 0, 0, 0)
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
ZIPAPP_ENTRYPOINT = (
    b"import sys\n"
    b"from python_runtime import require_supported_runtime, UnsupportedPythonError\n"
    b"try:\n"
    b"    require_supported_runtime()\n"
    b"except UnsupportedPythonError as error:\n"
    b"    print(f'[ERRO] {error}', file=sys.stderr)\n"
    b"    raise SystemExit(2)\n"
    b"from manager import main\n"
    b"raise SystemExit(main())\n"
)


def _portable_relative_path(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError(f"{label} inválido no manifesto do zipapp: {value!r}")
    raw_parts = value.split("/")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in raw_parts):
        raise ValueError(f"{label} inseguro no manifesto do zipapp: {value!r}")
    return path.as_posix()


def runtime_member_contracts() -> tuple[dict[str, str], ...]:
    """Load the sole declarative source for every installed zipapp member."""

    try:
        payload = read_regular_file(
            RUNTIME_MEMBER_MANIFEST, maximum_size=MAX_TEXT_INPUT_BYTES,
        )
        document = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("manifesto de membros do zipapp ausente ou inválido") from error
    if (
        not isinstance(document, dict)
        or set(document) != {"format", "project", "members"}
        or document.get("format") != 1
        or document.get("project") != "x86qw"
        or not isinstance(document.get("members"), list)
        or not document["members"]
    ):
        raise ValueError("identidade ou campos inválidos do manifesto de membros do zipapp")

    contracts: list[dict[str, str]] = []
    members: set[str] = set()
    sources: set[str] = set()
    for index, entry in enumerate(document["members"]):
        if not isinstance(entry, dict) or set(entry) != RUNTIME_MEMBER_FIELDS:
            raise ValueError(f"contrato inválido em members[{index}]")
        if not all(isinstance(entry[field], str) for field in RUNTIME_MEMBER_FIELDS):
            raise ValueError(f"campos inválidos em members[{index}]")
        member = _portable_relative_path(entry["member"], f"membro {index}")
        source = entry["source"]
        consumer = entry["consumer"].strip()
        contract = entry["contract"].strip()
        if not consumer:
            raise ValueError(f"consumidor ausente em members[{index}]")
        if not contract:
            raise ValueError(f"contrato ausente em members[{index}]")
        if source.startswith("generated:"):
            if source not in GENERATED_RUNTIME_SOURCES:
                raise ValueError(f"gerador desconhecido em members[{index}]: {source}")
        else:
            source = _portable_relative_path(source, f"origem {index}")
            if not source.startswith(STATIC_RUNTIME_SOURCE_PREFIXES):
                raise ValueError(f"origem fora do runtime em members[{index}]: {source}")
        if member in members:
            raise ValueError(f"membro duplicado no manifesto do zipapp: {member}")
        if source in sources:
            raise ValueError(f"origem duplicada no manifesto do zipapp: {source}")
        members.add(member)
        sources.add(source)
        contracts.append({
            "member": member,
            "source": source,
            "consumer": consumer,
            "contract": contract,
        })

    generated = {entry["source"] for entry in contracts if entry["source"].startswith("generated:")}
    if generated != GENERATED_RUNTIME_SOURCES:
        missing = sorted(GENERATED_RUNTIME_SOURCES - generated)
        extra = sorted(generated - GENERATED_RUNTIME_SOURCES)
        raise ValueError(
            f"projeções geradas divergentes no manifesto; ausentes={missing}, extras={extra}"
        )
    return tuple(contracts)


def runtime_member_files() -> tuple[tuple[str, str], ...]:
    """Return the static source projection derived from the canonical manifest."""

    return tuple(
        (entry["source"], entry["member"])
        for entry in runtime_member_contracts()
        if not entry["source"].startswith("generated:")
    )


def runtime_dependency_lock() -> dict[str, object]:
    try:
        document = json.loads(read_regular_file(
            RUNTIME_DEPENDENCY_MANIFEST, maximum_size=MAX_TEXT_INPUT_BYTES,
        ))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("manifesto de dependências runtime ausente ou inválido") from error
    if (
        not isinstance(document, dict)
        or set(document) != {"format", "project", "dependencies"}
        or document.get("format") != 1
        or document.get("project") != "x86qw"
        or not isinstance(document.get("dependencies"), list)
        or not document["dependencies"]
    ):
        raise ValueError("identidade ou campos inválidos das dependências runtime")
    names: set[str] = set()
    filenames: set[str] = set()
    for index, dependency in enumerate(document["dependencies"]):
        if not isinstance(dependency, dict) or set(dependency) != RUNTIME_DEPENDENCY_FIELDS:
            raise ValueError(f"dependência runtime inválida em dependencies[{index}]")
        if not all(isinstance(dependency[field], str) for field in RUNTIME_DEPENDENCY_FIELDS - {"package_prefixes"}):
            raise ValueError(f"campos inválidos em dependencies[{index}]")
        prefixes = dependency["package_prefixes"]
        if (
            not isinstance(prefixes, list)
            or not prefixes
            or not all(isinstance(prefix, str) and prefix.endswith("/") for prefix in prefixes)
        ):
            raise ValueError(f"prefixos inválidos em dependencies[{index}]")
        name = dependency["name"]
        filename = dependency["filename"]
        digest = dependency["sha256"]
        upstream_digest = dependency["upstream_sha256"]
        transformation = dependency["transformation"]
        if name in names or filename in filenames:
            raise ValueError("dependência runtime duplicada")
        if Path(filename).name != filename or not filename.endswith("-py3-none-any.whl"):
            raise ValueError(f"wheel runtime inválido: {filename}")
        if any(
            len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for value in (digest, upstream_digest)
        ):
            raise ValueError(f"SHA-256 runtime inválido: {name}")
        if transformation not in RUNTIME_DEPENDENCY_TRANSFORMATIONS:
            raise ValueError(f"transformação runtime inválida: {name}")
        if transformation == "none" and digest != upstream_digest:
            raise ValueError(f"wheel runtime sem transformação diverge da origem: {name}")
        if (
            transformation == "add-empty-package-marker:securesystemslib/_internal/__init__.py"
            and name != "securesystemslib"
        ):
            raise ValueError(f"transformação runtime incompatível: {name}")
        if not dependency["source"].startswith("https://pypi.org/project/"):
            raise ValueError(f"origem runtime inválida: {name}")
        names.add(name)
        filenames.add(filename)
    return document


def runtime_dependency_projection() -> dict[str, object]:
    """Expose only the dependency facts needed by the installed runtime.

    Repository URLs and package-prefix rules are build-time provenance, not
    runtime configuration.  Keeping them in the maintenance lock while
    projecting the minimal digest/license record prevents the installer from
    carrying an accidental network or source-policy surface.
    """

    document = runtime_dependency_lock()
    runtime_fields = (
        "name", "version", "filename", "sha256", "transformation", "license",
    )
    return {
        "format": document["format"],
        "project": document["project"],
        "dependencies": [
            {field: dependency[field] for field in runtime_fields}
            for dependency in document["dependencies"]
        ],
    }


def runtime_dependency_members() -> tuple[tuple[str, bytes], ...]:
    """Verify pinned pure-Python wheels and return their shipped package members."""

    document = runtime_dependency_lock()
    members: dict[str, bytes] = {}
    claimed_output_names: set[str] = set()
    for dependency in document["dependencies"]:
        assert isinstance(dependency, dict)
        wheel_path = RUNTIME_DEPENDENCY_WHEELS / str(dependency["filename"])
        wheel = read_regular_file(wheel_path, maximum_size=MAX_BUILD_INPUT_BYTES)
        if hashlib.sha256(wheel).hexdigest() != dependency["sha256"]:
            raise ValueError(f"SHA-256 do wheel diverge do lock: {dependency['name']}")
        try:
            plan = scan_archive(wheel)
        except ArchiveError as error:
            raise ValueError(f"wheel runtime inválido: {dependency['name']}") from error
        transformation = str(dependency["transformation"])
        if transformation != "none":
            marker = transformation.split(":", 1)[1]
            if not any(member.name == marker for member in plan.members):
                raise ValueError(
                    f"wheel runtime não contém marcador da transformação: {dependency['name']}"
                )
        license_prefix = f"{dependency['name']}-{dependency['version']}.dist-info/licenses/"
        selected_names: list[str] = []
        output_names: dict[str, str] = {}
        for member in plan.members:
            name = member.name
            package_member = any(
                name.startswith(prefix) for prefix in dependency["package_prefixes"]
            )
            license_member = name.startswith(license_prefix)
            if not package_member and not license_member:
                continue
            if member.is_dir or name.endswith(".pyc"):
                continue
            parts = PurePosixPath(name).parts
            if "test_data" in parts or PurePosixPath(name).name.startswith("test_"):
                continue
            if package_member and not (name.endswith(".py") or name.endswith("py.typed")):
                continue
            if member.size > MAX_TEXT_INPUT_BYTES:
                raise ValueError(f"membro inseguro no wheel runtime: {name}")
            _portable_relative_path(name, "membro do wheel")
            output_name = (
                name
                if package_member
                else f"_x86qw/licenses/dependencies/{dependency['name']}/{PurePosixPath(name).name}"
            )
            if output_name in claimed_output_names:
                raise ValueError(f"membro runtime duplicado: {output_name}")
            claimed_output_names.add(output_name)
            selected_names.append(name)
            output_names[name] = output_name
        if not selected_names:
            raise ValueError(f"wheel runtime não contém pacote consumível: {dependency['name']}")
        try:
            payloads = read_archive_members(plan, selected_names)
        except ArchiveError as error:
            raise ValueError(f"wheel runtime inválido: {dependency['name']}") from error
        for name in selected_names:
            members[output_names[name]] = payloads[name]
    return tuple(sorted(members.items()))


def bundle_files() -> tuple[str, ...]:
    return tuple(source for source, _, _ in (*BUNDLE_FILES, *LEGAL_FILES)) + tuple(
        source for source, _ in runtime_member_files()
    ) + tuple(source for _, source in RUNTIME_CONTRACT_SOURCES) + (
        RUNTIME_MEMBER_MANIFEST.relative_to(ROOT).as_posix(),
        RUNTIME_DEPENDENCY_MANIFEST.relative_to(ROOT).as_posix(),
        *(path.relative_to(ROOT).as_posix() for path in sorted(RUNTIME_DEPENDENCY_WHEELS.glob("*.whl"))),
    )


def includes_project_legal_files(version: str) -> bool:
    return parse_semver(version).major >= 1


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
    contracts = runtime_member_contracts()
    component_catalog = load_component_catalog(ROOT / "maintenance/inventory/components.json")
    runtime_inventory = load_runtime_inventory(
        ROOT / "maintenance/inventory", component_catalog=component_catalog,
    )
    generated_payloads = {
        "generated:entrypoint": ZIPAPP_ENTRYPOINT,
        "generated:capabilities": json_bytes(runtime_inventory["capabilities"]),
        "generated:runtimes": json_bytes(runtime_inventory["runtimes"]),
        "generated:games": json_bytes(runtime_inventory["games"]),
        "generated:identity": json_bytes(identity),
        "generated:component-catalog": runtime_catalog_bytes(),
        "generated:runtime-dependencies": json_bytes(runtime_dependency_projection()),
    }
    entries: list[tuple[str, str, int]] = [
        (entry["source"], entry["member"], 0o644) for entry in contracts
    ]
    if includes_project_legal_files(version):
        entries.extend(
            (source, f"_x86qw/{member}", mode)
            for source, member, mode in LEGAL_FILES
        )
    dependency_members = runtime_dependency_members()
    declared_names = {member_name for _source, member_name, _mode in entries}
    for name, _payload in dependency_members:
        if name in declared_names:
            raise ValueError(f"dependência sobrescreve membro do zipapp: {name}")
    with zipfile.ZipFile(output, "w", allowZip64=True) as application:
        for source_name, member_name, mode in entries:
            payload = (
                generated_payloads[source_name]
                if source_name.startswith("generated:")
                else read_regular_file(
                    ROOT.joinpath(*PurePosixPath(source_name).parts),
                    maximum_size=MAX_BUILD_INPUT_BYTES,
                )
            )
            if (
                source_name == TRUST_ROOT_SOURCE
                and hashlib.sha256(payload).hexdigest() != TRUST_ROOT_SHA256
            ):
                raise ValueError("root TUF diverge do pin SHA-256 aprovado")
            write_member(application, member_name, payload, mode)
        for name, payload in dependency_members:
            write_member(application, name, payload)
    payload = output.getvalue()
    required = tuple(member for _source, member, _mode in entries) + tuple(
        name for name, _payload in dependency_members
    )
    try:
        plan = scan_archive(payload, required_members=required)
    except ArchiveError as error:
        raise ValueError(f"installer zipapp failed archive validation: {error}") from error
    if set(plan.member_names) != set(required):
        raise ValueError("installer zipapp contains an unexpected member")
    return payload


def installer_ownership_document(
    version: str,
    filename: str,
    outer_payload: bytes,
    zipapp_payload: bytes,
) -> dict[str, object]:
    """Describe builder-known project bytes for a modern installer bundle."""

    if not includes_project_legal_files(version):
        raise ValueError("ownership facts só estão disponíveis para bundles >= 1.0.0")
    license_url = f"https://github.com/x86dx2/x86qw/blob/x86qw-installer-{version}/LICENSE"
    nested_plan = scan_archive(zipapp_payload)
    nested_payloads = read_archive_members(nested_plan)
    nested_entries: list[dict[str, object]] = []
    for contract in runtime_member_contracts():
        member = contract["member"]
        payload = nested_payloads[member]
        basis = (
            "generated-project-metadata"
            if contract["source"].startswith("generated:")
            else "project-source"
        )
        nested_entries.append({
            "path": member,
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "kind": "metadata" if member.endswith(".json") else "file",
            "ownership": "project",
            "ownership_basis": basis,
            "source": contract["source"],
            "license_concluded": "MIT",
            "license_url": license_url,
            "copyright_text": release_ownership.PROJECT_COPYRIGHT,
            "members": [],
        })
    for member in ("_x86qw/LICENSE", "_x86qw/NOTICE"):
        payload = nested_payloads[member]
        nested_entries.append({
            "path": member,
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "kind": "metadata",
            "ownership": "project",
            "ownership_basis": "generated-project-metadata",
            "source": member,
            "license_concluded": "MIT",
            "license_url": license_url,
            "copyright_text": release_ownership.PROJECT_COPYRIGHT,
            "members": [],
        })

    outer_plan = scan_archive(outer_payload)
    direct_payloads = read_archive_members(outer_plan)
    prefix = f"x86qw-installer-{version}/"
    direct_entries: list[dict[str, object]] = []
    for member, payload in sorted(direct_payloads.items()):
        if not member.startswith(prefix):
            continue
        relative = member[len(prefix):]
        if relative == "x86qw.pyz":
            members = nested_entries
            kind = "archive"
            source = "build:x86qw.pyz"
        else:
            members = []
            kind = "metadata" if relative.endswith((".json", ".md", "LICENSE", "NOTICE", "VERSION")) else "file"
            source = f"dist/installer/{relative}"
        direct_entries.append({
            "path": member,
            "size": len(payload),
            "sha256": __import__("hashlib").sha256(payload).hexdigest(),
            "kind": kind,
            "ownership": "project",
            "ownership_basis": "build-output",
            "source": source,
            "license_concluded": "MIT",
            "license_url": license_url,
            "copyright_text": release_ownership.PROJECT_COPYRIGHT,
            "members": members,
        })
    return release_ownership.validate_document({
        "format": 1,
        "project": "x86qw",
        "artifacts": [{
            "path": f"installer/{filename}",
            "size": len(outer_payload),
            "sha256": hashlib.sha256(outer_payload).hexdigest(),
            "kind": "archive",
            "ownership": "project",
            "ownership_basis": "build-output",
            "source": "build-installer-bundle",
            "license_concluded": "MIT",
            "license_url": license_url,
            "copyright_text": release_ownership.PROJECT_COPYRIGHT,
            "members": direct_entries,
        }],
    })


def package_results(package_root: Path) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    if not package_root.is_dir() or package_root.is_symlink():
        raise ValueError(f"installer package directory is missing or unsafe: {package_root}")
    for directory in package_root.iterdir():
        if directory.name == ".DS_Store":
            continue
        if directory.name == "latest" and directory.is_symlink():
            continue
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError(f"unexpected installer package entry: {directory}")
        version = directory.name
        parse_semver(version)
        filename = f"x86qw-installer-{version}.zip"
        archive = directory / filename
        if not archive.is_file() or archive.is_symlink():
            raise ValueError(f"installer package is missing or unsafe: {archive}")
        if any(entry.name not in {filename, ".DS_Store"} for entry in directory.iterdir()):
            raise ValueError(f"installer version directory contains an unexpected file: {directory}")
        try:
            plan = validate_installer_history_bundle(archive, version)
            read_archive_members(plan, ())
        except ArchiveError as error:
            raise ValueError(
                f"installer history bundle failed canonical archive validation: {archive}: {error}"
            ) from error
        try:
            distribution_path = archive.relative_to(ROOT / "dist").as_posix()
        except ValueError:
            # Release candidates are deliberately assembled outside the
            # checkout.  Keep the public path stable without pretending that
            # the temporary candidate is already part of ``dist``.
            distribution_path = PurePosixPath(
                "installer", "packages", version, filename,
            ).as_posix()
        results.append({
            "version": version,
            "filename": filename,
            "distribution_path": distribution_path,
            "size": plan.source_size,
            "sha256": plan.source_sha256,
        })
    return sorted(results, key=lambda item: parse_semver(str(item["version"])))


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
        atomic_io.sync_directory(path.parent)
        os.replace(staged.path, path)
        if read_regular_file(path, maximum_size=len(updated)) != updated:
            raise ValueError(f"public bootstrap changed during replacement: {path}")
        atomic_io.sync_directory(path.parent)


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
            elif entry.is_dir():
                try:
                    parse_semver(entry.name)
                except ValueError:
                    raise ValueError(
                        f"unexpected entry blocks installer history reset: {entry}"
                    ) from None
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


def build(
    output: Path,
    version: str = VERSION,
    *,
    ownership_output: Path | None = None,
) -> dict[str, object]:
    validate_public_launcher_contract(ROOT)
    validate_bootstrap_archive_source()
    filename = f"x86qw-installer-{version}.zip"
    zipapp_payload = zipapp_bytes(version)
    target = output / version / filename
    with staged_artifact(target, root=output, prefix=".installer-") as staged:
        with zipfile.ZipFile(staged.stream, "w", allowZip64=True) as bundle:
            prefix = f"x86qw-installer-{version}"
            write_member(bundle, f"{prefix}/x86qw.pyz", zipapp_payload)
            for source_name, member, mode in BUNDLE_FILES:
                source = ROOT / source_name
                payload = read_regular_file(source, maximum_size=MAX_BUILD_INPUT_BYTES)
                if source_name == "dist/installer/VERSION":
                    payload = f"{version}\n".encode("ascii")
                write_member(
                    bundle,
                    f"{prefix}/{member}",
                    payload,
                    mode,
                )
            if includes_project_legal_files(version):
                for source_name, member, mode in LEGAL_FILES:
                    write_member(
                        bundle,
                        f"{prefix}/{member}",
                        read_regular_file(
                            ROOT / source_name, maximum_size=MAX_BUILD_INPUT_BYTES,
                        ),
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
    try:
        distribution_path = target.relative_to(ROOT / "dist").as_posix()
    except ValueError:
        distribution_path = PurePosixPath(
            "installer", "packages", version, filename,
        ).as_posix()
    result = {
        "version": version,
        "filename": filename,
        "distribution_path": distribution_path,
        "size": accepted_plan.source_size,
        "sha256": accepted_plan.source_sha256,
    }
    if ownership_output is not None:
        ownership = installer_ownership_document(
            version,
            filename,
            target.read_bytes(),
            zipapp_payload,
        )
        release_ownership.write_document(Path(ownership_output), ownership)
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
        "license": (
            "MIT" if includes_project_legal_files(version)
            else "x86qw-project-terms"
        ),
        # x86QW-owned terms must resolve to the immutable installer tag rather
        # than a mutable repository root or branch for new bundles.  Historical
        # records retain their published metadata and are never rewritten by a
        # future registration.
        "license_url": (
            f"https://github.com/x86dx2/x86qw/blob/{release_tag}/LICENSE"
            if includes_project_legal_files(version)
            else "https://github.com/x86dx2/x86qw"
        ),
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
    parser.add_argument("--ownership-output", type=Path)
    parser.add_argument("--register", action="store_true")
    parser.add_argument(
        "--reset-history",
        action="store_true",
        help="descarta todas as versões locais antes de reconstruir a versão informada",
    )
    options = parser.parse_args()
    if options.reset_history:
        reset_history(options.output.resolve())
    result = build(
        options.output.resolve(),
        options.version,
        ownership_output=options.ownership_output,
    )
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
