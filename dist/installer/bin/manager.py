#!/usr/bin/env python3
"""Cross-platform ezQuake + x86QW component installer."""

from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import errno
import hashlib
import io
import importlib
import json
import os
import re
import shutil
import stat
import struct
import sys
import tarfile
import tempfile
import traceback
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any, Callable

sys.dont_write_bytecode = True

_argv0 = Path(sys.argv[0]).expanduser().resolve()
ZIPAPP_PATH = _argv0 if _argv0.suffix.casefold() == ".pyz" and _argv0.is_file() else None
PROJECT_ROOT = ZIPAPP_PATH.parent if ZIPAPP_PATH is not None else Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
INSTALLER_BIN = Path(__file__).resolve().parent
if str(INSTALLER_BIN) not in sys.path:
    sys.path.insert(0, str(INSTALLER_BIN))

python_runtime = importlib.import_module("x86qw_runtime.platform.python_runtime")
try:
    python_runtime.require_supported_runtime()
except python_runtime.UnsupportedPythonError as error:
    print(f"[ERRO] {error}", file=sys.stderr)
    raise SystemExit(2)

session_control = importlib.import_module("x86qw_runtime.session_control")
macos = importlib.import_module("x86qw_runtime.platform.macos")
host_adapter = importlib.import_module("x86qw_runtime.platform.host")
host_platform = host_adapter
supervisor_core = importlib.import_module("x86qw_runtime.supervisor.core")
from x86qw_runtime.ui import menu as navigation
from x86qw_runtime.ui.arguments import (
    FriendlyArgumentParser,
    public_bootstrap_command,
    public_launcher_name,
)
from x86qw_runtime.ui.console import (
    Console,
    UpdatePlanRow,
    format_bytes,
    format_bytes_compact,
)
from x86qw_runtime.ui.json_output import make_json_output, render_json_output

from x86qw_runtime.io.archive import (
    ArchiveError,
    extract_archive,
    read_archive_member,
    scan_archive,
    validate_installer_bundle,
)
from x86qw_runtime.io import private_fs, quarantine
from x86qw_runtime.io.atomic import (
    AtomicWriteError,
    atomic_copy_file,
    atomic_write_bytes,
)
from x86qw_runtime.io.managed_files import (
    file_sha256 as file_hash,
    persistent_descriptor_identity,
    persistent_path_identity,
    remove_persistent_identity_bound_path,
)
from x86qw_runtime.io.metadata import MetadataFileError, read_bounded_regular_file
from x86qw_runtime.io.paths import lexists, remove_path
from x86qw_runtime.errors import ExitCode, InstallerError, PersistenceError
from x86qw_runtime.contracts.schema import (
    ContractError,
    ContractVersions,
    SchemaKind,
    add_contract_versions,
    validate_document_versions,
)
from x86qw_runtime.migrations import (
    inspect_pending_migration,
    migrate_install_state,
    migrate_installation,
    recover_migration,
)
from x86qw_runtime.state import (
    INSTALLATION_PROFILES,
    StateError,
    parse_install_state,
    read_install_state,
    serialize_install_state,
)
from x86qw_runtime.transaction import (
    MutationApplyError,
    MutationPlan,
    MutationResult,
    MutationRollbackError,
    MutationStep,
    execute_mutation,
    finalize_mutation,
    prepare_mutation,
    rollback_mutation,
)
from x86qw_runtime.receipts import (
    ComponentReceipt,
    EzQuakeReceipt,
    EzQuakeReceiptContext,
    InventoryEntry,
    MAX_INVENTORY_BYTES,
    MAX_RECEIPT_BYTES,
    ReceiptError,
    parse_component_receipt,
    parse_ezquake_receipt,
    parse_inventory,
    parse_cli_receipt,
    parse_legacy_nquake_receipt,
    serialize_cli_receipt,
    serialize_component_receipt,
    serialize_ezquake_receipt,
    serialize_inventory,
)
from x86qw_runtime.versioning import (
    COMPONENT_VERSION,
    NIGHTLY_VERSION,
    SEMVER_VERSION,
    STABLE_VERSION,
    parse_semver,
)

from x86qw_runtime.catalogs import (
    components_by_id,
    load_development_component_catalog as load_component_catalog,
    load_component_catalog as load_runtime_catalog,
    profile_fingerprint,
    resolve_dependencies,
    validate_component_catalog as validate_runtime_catalog,
)
from x86qw_runtime.io.downloader import (
    DownloadError,
    DownloadHTTPError,
    MAX_ARTIFACT_BYTES,
    safe_url_for_log,
)
from x86qw_runtime.io.remote import (
    RemoteClient,
    https_url_filename,
    validate_https_url,
)
from x86qw_runtime.doctor import (
    DEFAULT_BUNDLE_NAME,
    OWNER_ONLY_FIRST_RUN,
    diagnose,
    render_doctor_report,
    resolve_bundle_destination,
    write_doctor_bundle,
)
from x86qw_runtime.profiles import (
    DEFAULT_PROFILE_BUNDLE,
    backup_user_profile,
    classify_install_data,
    is_user_profile_path,
    render_profile_report,
    restore_user_profile,
)
from x86qw_runtime.library import (
    add_favorite,
    discover_servers,
    load_library,
    record_recent,
    remove_favorite,
    render_library_report,
)
from x86qw_runtime.ui.local import write_local_ui
from x86qw_runtime.installation_changes import (
    InstallationChange,
    ManagedInstallationFile,
    inspect_installation_changes,
    render_installation_gitignore,
)
from x86qw_runtime.trust import (
    BoundedTufFetcher,
    TrustError,
    load_trusted_catalog,
)
from x86qw_runtime.catalogs import (
    load_capabilities,
    load_runtimes,
    runtimes_by_id,
)


ID1_PAK0_SHA256 = "eec9a020b6d8b6df73a5b911e19985f6e2539c1c6857b4a9f400553b9599677d"
ID1_PAK1_SHA256 = "94e355836ec42bc464e4cbe794cfb7b5163c6efa1bcc575622bb36475bf1cf30"
CATALOG_URL = "https://qw.x86.com.br/api/v1/catalog.json"
CATALOG_URLS = (
    CATALOG_URL,
    "https://raw.githubusercontent.com/x86dx2/x86qw/main/site/public/api/v1/catalog.json",
    "https://gitlab.com/x86dx2/x86qw/-/raw/main/site/public/api/v1/catalog.json",
)
TRUST_METADATA_URL = "https://qw.x86.com.br/api/v1/trust/metadata/"
TRUST_TARGET_URL = "https://qw.x86.com.br/api/v1/trust/targets/"
TRUST_ROOT_MEMBER = "_x86qw/trust/root.json"
TRUST_ROOT_MAX_BYTES = 512 * 1024
CATALOG_TIMEOUT = 10.0
CATALOG_MAX_BYTES = 2 * 1024 * 1024
HUB_MAX_BYTES = 1024 * 1024
PUBLIC_UNIX_BOOTSTRAP_COMMAND = (
    "curl -fsS https://qw.x86.com.br/install.sh | bash"
)
PUBLIC_POWERSHELL_BOOTSTRAP_COMMAND = (
    "& { Add-Type -AssemblyName System.Net.Http; $h = [System.Net.Http.HttpClientHandler]::new(); "
    "$h.AllowAutoRedirect = $false; $c = [System.Net.Http.HttpClient]::new($h); "
    "$c.Timeout = [TimeSpan]::FromSeconds(60); $c.MaxResponseContentBufferSize = 262144; "
    "$r = $null; try { $r = $c.GetAsync('https://qw.x86.com.br/install.ps1')."
    "GetAwaiter().GetResult(); if (-not $r.IsSuccessStatusCode) { throw \"x86QW: HTTP "
    "$([int]$r.StatusCode).\" }; if ($r.Content.Headers.ContentLength -gt 262144) { "
    "throw 'x86QW: bootstrap excedeu 262144 bytes.' }; $s = $r.Content.ReadAsStringAsync()."
    "GetAwaiter().GetResult(); & ([scriptblock]::Create($s)) @args } finally { if ($null -ne $r) "
    "{ $r.Dispose() }; $c.Dispose(); $h.Dispose() } }"
)
METADATA_DIR = ".x86qw"
COMPONENT_METADATA_DIR = ".x86qw/components"
EZQUAKE_METADATA_DIR = ".x86qw/clients/ezquake"
PERSONAL_BASELINE_DIR = ".x86qw/personal"
PERSONAL_BASELINE_COMPONENT = "personal"
# Legacy aggregate receipt names kept only for one-way migration and uninstall.
NQUAKE_RECEIPT = ".x86qw/nquake.receipt"
NQUAKE_INVENTORY = ".x86qw/nquake.inventory"
DEVELOPMENT_COMPONENT_CATALOG = "maintenance/inventory/components.json"
COMPONENT_RELEASES = "maintenance/inventory/component-releases.json"
RUNTIME_COMPONENT_CATALOG = "_x86qw/components.json"
DEVELOPMENT_CAPABILITY_CATALOG = Path("maintenance/inventory/capabilities.json")
DEVELOPMENT_RUNTIME_CATALOG = Path("maintenance/inventory/runtimes.json")
RUNTIME_CAPABILITY_CATALOG = "_x86qw/capabilities.json"
RUNTIME_RUNTIME_CATALOG = "_x86qw/runtimes.json"
CLI_ARCHIVE_NAME = "x86qw.pyz"
PUBLIC_CATALOG = Path("site/public/api/v1/catalog.json")
DEVELOPMENT_ID1_DIR = Path("dist/game-data/id1")
CORE_ID1_PACKAGE = "x86qw-core-id1"
CACHE_DIR_NAME = "x86qw"
CACHE_MARKER_NAME = ".x86qw-cache"
CACHE_MARKER_VALUE = "x86qw-cache-v1"
LEGACY_CACHE = ("x86-qw", ".x86-qw-cache", "x86-qw-cache-v1")
MACOS_PREFERENCES_DOMAIN = "com.ezquake.ezQuake"
MACOS_DIRECTORY_KEYS = ("basedir", "version", "NSOSPLastRootDirectory")
MACOS_SAFE_AREA_KEY = "NSPrefersDisplaySafeAreaCompatibilityMode"
DEFAULT_PRESET = 's_raw_volume "0.2"\n'
NQUAKE_TEXTURE_LIMIT = re.compile(
    rb'^([ \t]*gl_max_size[ \t]+)"?32768"?([ \t]*)(\r?)$', re.MULTILINE,
)
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
MACOS_STABLE_BINARY_IDENTITIES = {
    "2ccea8f214c91e5fc92b5cb195c81ac584f75a551d11a58de56e5e7951eec7ed": {
        "upstream": "14633b5d4201e9460250ad236fde2e4ad579a6ddbaf81301830099d8cf004f33",
        "x86qw-legacy": "e24524761d8ff10c57a8ecbb2fdc7ce29d1bd78641cfaecf49644d8881e2422a",
    },
}
HUB_SERVERS_API = "https://hubapi.quakeworld.nu/v2/servers/mvdsv?empty=exclude&limit=20"
MAPS_RECEIPT = ".x86qw/components/maps/receipt"
MAPS_INVENTORY = ".x86qw/components/maps/inventory"
PRESETS_RECEIPT = ".x86qw/components/presets/receipt"
PRESETS_INVENTORY = ".x86qw/components/presets/inventory"
PLAY_SUPPORT_RECEIPT = ".x86qw/components/play-support/receipt"
PLAY_SUPPORT_INVENTORY = ".x86qw/components/play-support/inventory"
PACKAGE_ORDER_RECEIPT = ".x86qw/components/package-order/receipt"
PACKAGE_ORDER_INVENTORY = ".x86qw/components/package-order/inventory"
CLI_RECEIPT = ".x86qw/cli/receipt"
LEGACY_CLI_RECEIPT = ".x86qw/cli.receipt"
INSTALL_STATE = ".x86qw/state.json"
INSTALLATION_CAPABILITIES: frozenset[str] = frozenset()
INSTALLER_BUNDLE_METADATA = "_x86qw/installer.json"
DEVELOPMENT_VERSION_FILE = Path("dist/installer/VERSION")
NATIVE_CANDIDATE_ROOT_ENV = "X86QW_NATIVE_CANDIDATE_ROOT"
NATIVE_CANDIDATE_ARTIFACT_KEY = "_native_candidate_artifact"
QW_PACKAGE_PRIORITY = (
    "ktx.pk3",
    "models.pk3",
    "scoreboard_flags.pk3",
    "nquake.pk3",
    "textures.pk3",
    "qrp_maps_textures_1.pk3",
    "qrp_maps_textures_2.pk3",
    "qrp_maps_textures_3.pk3",
    "qrp_maps_textures_4.pk3",
    "qrp_b-models.pk3",
)
MUTABLE_COMPONENT_DEFAULTS = {
    "clan-arena": ("prox/configs/config.cfg",),
}
LEGACY_COMPONENT_REPLACEMENTS = {
    "nquake-bootstrap": "x86qw-client-bootstrap",
    "nquake-ktx": "ktx",
}
LEGACY_COMPONENT_REMOVALS = {
    "nquake-sounds": "sons de Clan Arena incorporados ao KTX",
}


def trust_now() -> datetime:
    """Return the UTC clock used for installed-client trust checks."""

    return datetime.now(timezone.utc)
LEGACY_COMPONENTS = frozenset({
    "clan-arena", *LEGACY_COMPONENT_REPLACEMENTS, *LEGACY_COMPONENT_REMOVALS,
})
ReleaseRecord = tuple[str, tuple[str, ...], str]
INSTALLER_ROOT = PROJECT_ROOT


def read_zipapp_json(archive: Path, member: str, label: str) -> dict[str, object]:
    try:
        plan = scan_archive(archive, required_members=(member,))
        value = json.loads(read_archive_member(plan, member))
    except (ArchiveError, OSError, KeyError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InstallerError(f"{label} ausente ou inválido em {archive}") from error
    if not isinstance(value, dict):
        raise InstallerError(f"{label} inválido em {archive}")
    return value


def trusted_root_bytes() -> bytes:
    """Load only an embedded production root or an explicit development root."""

    if ZIPAPP_PATH is not None:
        try:
            plan = scan_archive(ZIPAPP_PATH, required_members=(TRUST_ROOT_MEMBER,))
            return read_archive_member(plan, TRUST_ROOT_MEMBER)
        except (ArchiveError, OSError, KeyError) as error:
            raise InstallerError("A root TUF de produção não está incorporada na CLI.") from error
    configured = os.environ.get("X86_QW_TRUST_ROOT")
    if not configured:
        raise InstallerError(
            "A root TUF de desenvolvimento não foi informada; catálogo remoto bloqueado."
        )
    try:
        return read_bounded_regular_file(
            Path(configured), maximum_size=TRUST_ROOT_MAX_BYTES,
        )
    except OSError as error:
        raise InstallerError("A root TUF de desenvolvimento é inválida.") from error


def application_version() -> str:
    if ZIPAPP_PATH is not None:
        identity = read_zipapp_json(
            ZIPAPP_PATH, INSTALLER_BUNDLE_METADATA, "Identidade da CLI pública",
        )
        version = identity.get("version")
        location = ZIPAPP_PATH
    else:
        location = PROJECT_ROOT / DEVELOPMENT_VERSION_FILE
        try:
            version = location.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise InstallerError(f"Versão da CLI x86QW ausente ou inválida: {location}") from error
    if not isinstance(version, str) or not SEMVER_VERSION.fullmatch(version):
        raise InstallerError(f"Versão da CLI x86QW ausente ou inválida: {location}")
    return version


def gameplay_composition_context(gameplay):
    return gameplay.GameplayContext(
        project_root=PROJECT_ROOT,
        installer_root=INSTALLER_ROOT,
        zipapp_path=ZIPAPP_PATH,
        installer_base=Installer,
        console=console,
        read_zipapp_json=read_zipapp_json,
        public_cli=ZIPAPP_PATH is not None,
    )


def load_gameplay_module():
    """Compose the gameplay facade with manager-owned dependencies explicitly."""

    gameplay = importlib.import_module("gameplay")
    gameplay.configure_context(gameplay_composition_context(gameplay))
    return gameplay


def load_services_module():
    """Compose service lifecycle dependencies without a manager service locator."""

    gameplay = load_gameplay_module()
    services = importlib.import_module("services")
    services.configure_context(service_composition_context(services, gameplay))
    return services


def service_composition_context(services, gameplay):
    return services.ServiceContext(
        project_root=PROJECT_ROOT,
        zipapp_path=ZIPAPP_PATH,
        installer_base=Installer,
        runtimes=RUNTIMES,
        capability_catalog=CAPABILITY_CATALOG,
        host_platforms=HOST_PLATFORMS,
        host_platform=host_platform,
        console=console,
        gameplay_module=gameplay,
        gameplay_context=gameplay_composition_context(gameplay),
    )




@dataclass(frozen=True)
class PlatformSpec:
    key: str
    label: str
    architecture: str
    stable_archive: str
    nightly_suffix: str
    archive_binary: str
    archive_executable: str | None
    stable_runtime: str
    nightly_runtime: str
    stable_receipt: str
    nightly_receipt: str

    def runtime(self, channel: str) -> str:
        return self.stable_runtime if channel == "stable" else self.nightly_runtime

    def receipt(self, channel: str) -> str:
        return self.stable_receipt if channel == "stable" else self.nightly_receipt

    def legacy_receipt(self, channel: str) -> str:
        return f".x86qw/ezquake-{self.key}-{channel}.receipt"


@lru_cache(maxsize=1)
def load_launcher_contracts() -> tuple[dict[str, object], dict[str, object]]:
    if ZIPAPP_PATH is not None:
        capabilities = read_zipapp_json(
            ZIPAPP_PATH, RUNTIME_CAPABILITY_CATALOG, "Catálogo de capacidades da CLI",
        )
        runtimes = read_zipapp_json(
            ZIPAPP_PATH, RUNTIME_RUNTIME_CATALOG, "Catálogo de runtimes da CLI",
        )
    else:
        capabilities = load_capabilities(PROJECT_ROOT / DEVELOPMENT_CAPABILITY_CATALOG)
        runtimes = load_runtimes(PROJECT_ROOT / DEVELOPMENT_RUNTIME_CATALOG)
    return capabilities, runtimes


class LazyMapping(Mapping[str, object]):
    """Compatibility mapping that defers its catalog read until first use."""

    def __init__(self, loader: Callable[[], Mapping[str, object]]) -> None:
        self._loader = loader

    def _mapping(self) -> Mapping[str, object]:
        return self._loader()

    def __getitem__(self, key: str) -> object:
        return self._mapping()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._mapping())

    def __len__(self) -> int:
        return len(self._mapping())

    def __repr__(self) -> str:
        return repr(self._mapping())


CAPABILITY_CATALOG = LazyMapping(lambda: load_launcher_contracts()[0])
RUNTIME_CATALOG = LazyMapping(lambda: load_launcher_contracts()[1])


def public_command_names() -> tuple[str, ...]:
    """Return the public command contract from the bundled catalog."""

    commands = load_launcher_contracts()[0].get("commands")
    if (
        not isinstance(commands, list)
        or not commands
        or any(not isinstance(command, str) or not command for command in commands)
        or len(commands) != len(set(commands))
    ):
        raise InstallerError("Catálogo de capacidades sem uma lista de comandos pública válida.")
    return tuple(commands)


@lru_cache(maxsize=1)
def launcher_runtimes() -> dict[str, dict[str, object]]:
    return runtimes_by_id(load_launcher_contracts()[1])


RUNTIMES = LazyMapping(launcher_runtimes)


@lru_cache(maxsize=1)
def client_platform_specs() -> dict[str, PlatformSpec]:
    stable = RUNTIMES["ezquake-stable"]
    nightly = RUNTIMES["ezquake-nightly"]
    stable_platforms = {
        str(entry["system"]): entry for entry in stable["platforms"]
        if isinstance(entry, dict)
    }
    nightly_platforms = {
        str(entry["system"]): entry for entry in nightly["platforms"]
        if isinstance(entry, dict)
    }
    labels = CAPABILITY_CATALOG.get("platform_labels")
    if not isinstance(labels, dict) or set(stable_platforms) != set(nightly_platforms):
        raise ValueError("catálogo declarativo de plataformas do ezQuake é inconsistente")
    result: dict[str, PlatformSpec] = {}
    for system, stable_platform in stable_platforms.items():
        nightly_platform = nightly_platforms[system]
        variant = str(stable_platform["variant"])
        result[system] = PlatformSpec(
            system,
            str(labels[variant]),
            str(stable_platform["architecture"]),
            str(stable_platform["archive"]),
            str(nightly_platform["filename_suffix"]),
            str(stable_platform["archive_binary"]),
            (
                (
                    f"{stable_platform['archive_binary']}/{stable_platform['executable']}"
                    if system == "macos"
                    else str(stable_platform["archive_binary"])
                )
                if stable_platform.get("permissions") == "executable"
                else None
            ),
            str(stable_platform["runtime_path"]),
            str(nightly_platform["runtime_path"]),
            str(stable_platform["receipt"]),
            str(nightly_platform["receipt"]),
        )
    return result


PLATFORMS = LazyMapping(client_platform_specs)


@lru_cache(maxsize=1)
def host_platforms() -> dict[str, str]:
    raw_host_platforms = CAPABILITY_CATALOG.get("host_systems")
    if not isinstance(raw_host_platforms, dict):
        raise ValueError("catálogo declarativo não informa sistemas hospedeiros")
    return {str(host): str(system) for host, system in raw_host_platforms.items()}


HOST_PLATFORMS = LazyMapping(host_platforms)


PRESETS = {
    "x86-qw-modern.cfg": """// x86-qw: visual moderno, carregamento manual com cfg_load x86-qw-modern
s_khz \"48\"
s_linearresample \"1\"
vid_renderer \"1\"
vid_framebuffer \"1\"
vid_framebuffer_hdr \"1\"
vid_framebuffer_hdr_tonemap \"1\"
vid_framebuffer_fxaa \"1\"
gl_anisotropy \"16\"
gl_part_explosions \"1\"
gl_part_trails \"1\"
gl_part_spikes \"1\"
gl_part_gunshots \"1\"
gl_part_blood \"1\"
gl_part_telesplash \"1\"
gl_part_blobs \"1\"
""",
    "x86-qw-competitive.cfg": """// x86-qw: máxima clareza, sem alterar rede, binds ou sensibilidade
s_khz \"48\"
s_linearresample \"1\"
vid_vsync \"0\"
r_drawviewmodel \"0\"
r_fastsky \"1\"
r_fastturb \"1\"
r_drawflame \"0\"
gl_part_explosions \"0\"
gl_part_trails \"0\"
gl_part_spikes \"0\"
gl_part_gunshots \"0\"
gl_part_blood \"0\"
""",
    "x86-qw-classic.cfg": """// x86-qw: aparência próxima ao Quake original
vid_framebuffer_hdr \"0\"
vid_framebuffer_hdr_tonemap \"0\"
vid_framebuffer_fxaa \"0\"
gl_texturemode \"GL_NEAREST_MIPMAP_NEAREST\"
r_drawviewmodel \"1\"
r_fastsky \"0\"
r_fastturb \"0\"
gl_part_explosions \"0\"
gl_part_trails \"0\"
gl_part_spikes \"0\"
gl_part_gunshots \"0\"
gl_part_blood \"0\"
""",
    "x86-qw-stream.cfg": """// x86-qw: imagem legível para transmissão e gravação
s_khz \"48\"
s_linearresample \"1\"
vid_renderer \"1\"
vid_framebuffer \"1\"
vid_framebuffer_hdr \"1\"
vid_framebuffer_hdr_tonemap \"1\"
vid_framebuffer_fxaa \"1\"
r_drawviewmodel \"1\"
gl_part_explosions \"1\"
gl_part_trails \"1\"
gl_part_gunshots \"1\"
""",
}

@dataclass(frozen=True)
class ClientRepairIssue:
    spec: PlatformSpec
    channel: str
    receipt_path: Path
    receipt: dict[str, str]
    reason: str
    mode: str
    release: ReleaseRecord | None = None
    category: str = "payload-required"


@dataclass(frozen=True)
class RepairAssessment:
    invalid_components: tuple[str, ...]
    support_invalid: bool
    permission_repairs: tuple[Path, ...]
    package_order_invalid: bool
    client_issues: tuple[ClientRepairIssue, ...]
    metadata_diagnostics: tuple[str, ...]
    recovered_state: dict[str, object] | None = None


def repair_diagnostic_category(diagnostic: str) -> str:
    if "runtime presente sem recibo; preservado" in diagnostic:
        return "advisory"
    return "fatal"


class LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value is not None:
                self.links.append(value)


console = Console(application_version)


def package_size(package: dict[str, object]) -> int | None:
    size = package.get("size")
    return size if isinstance(size, int) and size > 0 else None


def file_count(count: int) -> str:
    return f"{count} {'arquivo' if count == 1 else 'arquivos'}"


def validate_hex(value: str, pattern: re.Pattern[str], label: str) -> None:
    if not pattern.fullmatch(value):
        raise InstallerError(f"invalid {label}")


def ensure_no_symlink(path: Path, label: str) -> None:
    if path.is_symlink():
        raise InstallerError(f"{label} must not be a symlink: {path}")


def remove_empty_directories(root: Path) -> None:
    if not root.is_dir() or root.is_symlink():
        return
    for current, directories, _ in os.walk(root, topdown=False, followlinks=False):
        for name in directories:
            candidate = Path(current, name)
            if not candidate.is_symlink():
                try:
                    candidate.rmdir()
                except OSError:
                    pass
    try:
        root.rmdir()
    except OSError:
        pass


def reject_tree_symlinks(root: Path, label: str) -> None:
    if not root.is_dir():
        return
    for current, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            candidate = Path(current, name)
            if candidate.is_symlink():
                raise InstallerError(f"{label} contains a symlink: {candidate}")


def safe_extract_zip(
    archive: Path,
    destination: Path,
    *,
    executable_members: tuple[str, ...] = (),
) -> None:
    """Compatibility adapter over the single ZIP/PK3 extraction boundary."""
    try:
        plan = scan_archive(archive, executable_members=executable_members)
        extract_archive(plan, destination)
    except ArchiveError as error:
        raise InstallerError(f"Pacote ZIP inválido: {error}") from error


@dataclass
class ComponentPayloadRollback:
    backup_root: Path
    original_hashes: dict[str, str | None]
    new_hashes: dict[str, str]
    stale_hashes: dict[str, str]
    created_directories: dict[Path, tuple[int, int]]


@dataclass
class ComponentMetadataRollback:
    destination: Path
    installed_identity: tuple[int, int] | None
    previous: Path
    previous_identity: tuple[int, int] | None
    legacy_backups: list[tuple[Path, Path, tuple[int, int]]]
    created_directories: tuple[tuple[Path, tuple[int, int]], ...]


@dataclass
class ComponentRemovalNode:
    original: Path
    backup: Path
    identity: tuple[int, int]
    kind: str


@dataclass
class ComponentRemovalRollback:
    backup_root: Path
    moved: list[ComponentRemovalNode]


@dataclass
class RuntimePayloadRollback:
    destination: Path
    installed_identity: tuple[int, int] | None
    previous: Path
    previous_identity: tuple[int, int] | None


@dataclass
class RuntimeReceiptRollback:
    destination: Path
    installed_identity: tuple[int, int] | None
    previous: list[tuple[Path, Path, tuple[int, int]]]
    created_directories: tuple[tuple[Path, tuple[int, int]], ...]


@dataclass
class CorePakRollback:
    installed: list[tuple[Path, tuple[int, int]]]


@dataclass(frozen=True)
class InstallTopologyRollback:
    created_directories: tuple[tuple[Path, tuple[int, int]], ...]


@dataclass(frozen=True)
class MetadataTopologyRollback:
    directories: tuple[tuple[Path, tuple[int, int], int], ...]


@dataclass(frozen=True)
class RuntimePermissionRollback:
    path: Path
    identity: tuple[int, int]
    mode: int


@dataclass(frozen=True)
class CreatedDefaultRollback:
    destination: Path
    digest: str
    identity: tuple[int, int]
    created_directories: tuple[tuple[Path, tuple[int, int]], ...]


class RuntimeCommitPersistenceError(PersistenceError):
    """The runtime pair committed, but its final durability sync failed."""

    def __init__(self, message: str, *, result: MutationResult) -> None:
        super().__init__(message, committed=True)
        self.result = result


@dataclass(frozen=True)
class ComponentSourceProvider:
    """Repository-only component source operations injected by development tools."""

    load_context: Callable[[Path, Path, Path], object]
    resolve_payloads: Callable[[object, str], tuple[dict[str, object], str, object]]


_development_component_source_provider: ComponentSourceProvider | None = None


def configure_development_source_provider(
    provider: ComponentSourceProvider | None,
) -> None:
    global _development_component_source_provider
    if provider is not None and not isinstance(provider, ComponentSourceProvider):
        raise TypeError("provider de fontes de desenvolvimento inválido")
    _development_component_source_provider = provider


class Installer:
    def __init__(
        self,
        project_root: Path,
        target: Path,
        cache_root: Path | None = None,
        *,
        online_only: bool = False,
        component_source_provider: ComponentSourceProvider | None = None,
    ):
        self.project_root = project_root.resolve()
        self.target = target
        self.online_only = online_only
        self._cache_root = cache_root
        self.cache_root: Path | None = None
        self.cache_bin: Path | None = None
        self.stage: Path | None = None
        self._stage_identity: tuple[int, int] | None = None
        self._stage_created_roots: tuple[tuple[Path, tuple[int, int]], ...] = ()
        self._stage_lease: object | None = None
        self.spec: PlatformSpec | None = None
        self.channel = ""
        self.selected_version = ""
        self.app_url = ""
        self.app_urls: tuple[str, ...] = ()
        self.app_archive_name = ""
        self.app_expected_checksum = ""
        self.app_checksum_kind = ""
        self.app_archive_sha256 = ""
        self.app_expected_size = 0
        self.app_bundle_version = ""
        self.app_binary_sha256 = ""
        self.app_distribution_path = ""
        self.cache_prefix = ""
        self.update_ui = False
        self.remote = RemoteClient(console)
        self._public_catalog: dict[str, object] | None = None
        self._component_source_context: object | None = None
        self.component_source_provider = (
            component_source_provider or _development_component_source_provider
        )
        self.selected_component_profile = "none"
        self.requested_components: list[str] = []
        # Install-only automation keeps the existing interactive flow as the
        # default while allowing release smoke jobs to provide every choice
        # explicitly.  These values are scoped to one install transaction and
        # are never persisted as user preferences.
        self._non_interactive_install = False
        self._requested_channel: str | None = None
        self._requested_release: str | None = None
        self._requested_profile: str | None = None
        self._component_catalog: dict[str, object] | None = None
        self._components: dict[str, dict[str, object]] | None = None
        self._content_component_namespaces: set[str] | None = None
        self._runtime_launch_hashes: dict[Path, str] = {}
        self._native_candidate_manifest_data: dict[str, object] | None = None

    @property
    def component_catalog(self) -> dict[str, object]:
        if self._component_catalog is not None:
            return self._component_catalog
        runtime_catalog_path = INSTALLER_ROOT / RUNTIME_COMPONENT_CATALOG
        development_catalog_path = INSTALLER_ROOT / DEVELOPMENT_COMPONENT_CATALOG
        try:
            if ZIPAPP_PATH is not None:
                catalog = read_zipapp_json(
                    ZIPAPP_PATH, RUNTIME_COMPONENT_CATALOG, "Catálogo runtime da CLI",
                )
                validate_runtime_catalog(catalog)
            elif runtime_catalog_path.is_file() and not runtime_catalog_path.is_symlink():
                catalog = load_runtime_catalog(runtime_catalog_path)
            else:
                catalog = load_component_catalog(development_catalog_path)
        except (InstallerError, ValueError) as error:
            raise InstallerError(str(error)) from error
        self._component_catalog = catalog
        return catalog

    @property
    def components(self) -> dict[str, dict[str, object]]:
        if self._components is None:
            self._components = components_by_id(self.component_catalog)
        return self._components

    @property
    def content_component_namespaces(self) -> set[str]:
        if self._content_component_namespaces is None:
            self._content_component_namespaces = set(
                self.component_catalog["content_namespaces"],
            )
        return self._content_component_namespaces

    def is_native_macos_install(self) -> bool:
        return host_platform.system() == "Darwin" and self.spec is not None and self.spec.key == "macos"

    def ensure_macos_ezquake_closed(self) -> None:
        if not self.is_native_macos_install():
            return
        macos.ensure_process_absent("ezQuake")

    def reset_macos_game_directory(self) -> MutationResult | None:
        if not self.is_native_macos_install():
            return None
        return self.clear_macos_game_directory()

    def macos_game_directory_reset_required(self) -> bool:
        if not self.is_native_macos_install():
            return False
        assert self.spec is not None
        return not any(
            lexists(self.target / relative)
            for channel in ("stable", "nightly")
            for relative in (
                self.spec.runtime(channel),
                self.spec.receipt(channel),
                self.spec.legacy_receipt(channel),
            )
        )

    def clear_macos_game_directory(self) -> MutationResult:
        self.ensure_macos_ezquake_closed()
        snapshot = macos.snapshot_preference_keys(
            MACOS_PREFERENCES_DOMAIN, MACOS_DIRECTORY_KEYS,
        )
        plan = MutationPlan(
            identifier="macos-directory-preferences",
            summary="Limpar seleção antiga do diretório do ezQuake",
            steps=(MutationStep(
                key="directory-preferences",
                description="Remover preferências antigas do diretório do jogo",
                observe=lambda: macos.snapshot_preference_keys(
                    MACOS_PREFERENCES_DOMAIN, MACOS_DIRECTORY_KEYS,
                ),
                apply=lambda: macos.clear_preference_keys(snapshot),
                rollback=macos.restore_preference_keys,
            ),),
        )
        result = execute_mutation(prepare_mutation(plan))
        console.success("Seleção antiga do diretório do ezQuake removida do macOS.")
        return result

    def macos_app_is_sandboxed(self, app: Path) -> bool:
        if host_platform.system() != "Darwin":
            return False
        return macos.app_is_sandboxed(app)

    def macos_app_uses_full_display(self, app: Path) -> bool:
        return macos.app_uses_full_display(app)

    def prepare_macos_nightly_app(self, app: Path) -> bool:
        if self.channel != "nightly":
            raise InstallerError(
                "A preparação local do bundle macOS é permitida somente no canal nightly."
            )
        if host_platform.system() != "Darwin":
            return False
        sandboxed, display_enabled = macos.prepare_nightly_bundle(app)
        if sandboxed:
            console.info("Ajustando o bundle macOS para acessar diretamente o diretório x86QW...")
        if display_enabled:
            console.info("Preparando o fullscreen do ezQuake para utilizar toda a tela no macOS...")
        console.success("Bundle macOS preparado para acesso direto e fullscreen integral.")
        return True

    def macos_app_needs_preparation(self, app: Path) -> bool:
        return (
            host_platform.system() == "Darwin"
            and (self.macos_app_is_sandboxed(app) or not self.macos_app_uses_full_display(app))
        )

    def macos_stable_has_legacy_x86qw_rewrite(
        self, app: Path, artifact_sha256: str, binary_sha256: str,
    ) -> bool:
        identities = MACOS_STABLE_BINARY_IDENTITIES.get(artifact_sha256)
        if (
            identities is None
            or binary_sha256 != identities["x86qw-legacy"]
            or not self.macos_app_uses_full_display(app)
        ):
            return False
        if host_platform.system() != "Darwin":
            return True
        return not self.macos_app_is_sandboxed(app)

    def macos_runtime_action(
        self, app: Path, channel: str, artifact_sha256: str, binary_sha256: str,
    ) -> str | None:
        if channel == "stable":
            return (
                "restore-upstream"
                if self.macos_stable_has_legacy_x86qw_rewrite(
                    app, artifact_sha256, binary_sha256,
                )
                else None
            )
        return "prepare-nightly" if self.macos_app_needs_preparation(app) else None

    def validate_target(self, action: str, *, purge: bool = False) -> None:
        target_exists = lexists(self.target)
        if target_exists and self.target.is_symlink():
            raise InstallerError(f"O diretório de destino não pode ser um link simbólico: {self.target}")
        if target_exists and not self.target.is_dir():
            raise InstallerError(f"O destino não é um diretório: {self.target}")
        if not target_exists and action != "install" and not (action == "uninstall" and purge):
            raise InstallerError(f"O diretório de destino não existe: {self.target}")
        self.target = self.target.resolve(strict=False)
        if self.target == Path(self.target.anchor):
            raise InstallerError("A raiz do sistema de arquivos não pode ser usada como destino.")
        if self.target == self.project_root:
            raise InstallerError("A raiz do projeto não pode ser usada como destino; use quake-world.")

    def reject_target_symlinks(self) -> None:
        managed_roots = (
            "id1", "ezquake", "qw", "arena", "prox", "fortress", "td2",
            "mvdsv", "mvdsv.exe", "qtv", "qwfwd", "docs", METADATA_DIR,
        )
        for name in managed_roots:
            candidate = self.target / name
            ensure_no_symlink(candidate, "managed path")
            reject_tree_symlinks(candidate, "managed tree")
        for spec in PLATFORMS.values():
            for relative in (spec.stable_runtime, spec.nightly_runtime, spec.stable_receipt, spec.nightly_receipt):
                ensure_no_symlink(self.target / relative, "managed path")
        for relative in (
            "LICENSE", "readme.txt", NQUAKE_RECEIPT, NQUAKE_INVENTORY,
            MAPS_RECEIPT, MAPS_INVENTORY, PRESETS_RECEIPT, PRESETS_INVENTORY,
        ):
            ensure_no_symlink(self.target / relative, "managed path")
        for component in self.metadata_component_ids():
            for relative in self.component_metadata(component):
                ensure_no_symlink(self.target / relative, "managed path")

    def managed_installation_identity(self) -> tuple[str, ...]:
        """Validate persisted evidence before a destructive target operation."""

        if not lexists(self.target):
            return ()
        metadata = self.target / METADATA_DIR
        if lexists(metadata):
            ensure_no_symlink(metadata, "metadados da instalação")
            if not metadata.is_dir():
                raise InstallerError(
                    f"Metadados da instalação inválidos: {metadata}"
                )

        evidence: list[str] = []
        state = self.target / INSTALL_STATE
        if lexists(state):
            if not state.is_file() or state.is_symlink():
                raise InstallerError(f"Estado da instalação inválido: {state}")
            self.read_install_state_document(state)
            evidence.append("state")

        if self.cli_receipt_path() is not None:
            evidence.append("cli")
        if self.validate_nquake_pair()[0]:
            evidence.append("nquake")
        for component in self.metadata_component_ids():
            if self.validate_component_pair(component)[0]:
                evidence.append(f"component:{component}")
        for spec in PLATFORMS.values():
            for channel in ("stable", "nightly"):
                if self.ezquake_receipt_path(spec, channel) is not None:
                    evidence.append(f"client:{spec.key}:{channel}")
        return tuple(evidence)

    def require_managed_installation_identity(self, action: str) -> tuple[str, ...]:
        evidence = self.managed_installation_identity()
        if not evidence:
            raise InstallerError(
                f"A operação {action} recusou um destino sem identidade gerenciada "
                f"x86QW validada: {self.target}"
            )
        return evidence

    def resolve_cache_root(self) -> Path:
        if self._cache_root is not None:
            root = self._cache_root.absolute()
        else:
            system = host_platform.system()
            root = host_adapter.user_cache_directory(CACHE_DIR_NAME, system=system)
        if not root.is_absolute() or root == Path(root.anchor):
            raise InstallerError(f"unsafe cache path: {root}")
        parent = root.parent.resolve(strict=False)
        root = parent / root.name
        try:
            root.relative_to(self.project_root)
        except ValueError:
            pass
        else:
            raise InstallerError("cache must be outside the project")
        self.cache_root = root
        self.cache_bin = root / "bin"
        return root

    def _cache_marker_at(self, root: Path, marker_name: str, marker_value: str) -> Path:
        marker = root / marker_name
        if not marker.is_file() or marker.is_symlink():
            raise InstallerError(f"O diretório de cache não pertence a este instalador e foi preservado: {root}")
        first_line = marker.read_text(encoding="utf-8").splitlines()[:1]
        if first_line != [marker_value]:
            raise InstallerError(f"O marcador de propriedade do cache é inválido: {marker}")
        return marker

    def validate_cache_marker_at(self, root: Path, marker_name: str, marker_value: str) -> None:
        marker = self._cache_marker_at(root, marker_name, marker_value)
        expected = marker.lstat()
        try:
            private_fs.validate_private_file(marker)
        except OSError as error:
            raise InstallerError(
                f"O marcador de propriedade do cache não pôde ser protegido: {marker}"
            ) from error
        current = marker.lstat()
        if (
            stat.S_ISLNK(current.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or (int(current.st_dev), int(current.st_ino))
            != (int(expected.st_dev), int(expected.st_ino))
        ):
            raise InstallerError(
                f"O marcador de propriedade do cache mudou: {marker}"
            )
        self._cache_marker_at(root, marker_name, marker_value)

    def _prepare_cache_marker_at(
        self, root: Path, marker_name: str, marker_value: str,
    ) -> None:
        marker = self._cache_marker_at(root, marker_name, marker_value)
        expected = marker.lstat()
        try:
            private_fs.protect_private_file(
                marker,
                expected_identity=(int(expected.st_dev), int(expected.st_ino)),
            )
        except OSError as error:
            raise InstallerError(
                f"O marcador de propriedade do cache não pôde ser protegido: {marker}"
            ) from error
        self.validate_cache_marker_at(root, marker_name, marker_value)

    def validate_cache_marker(self) -> None:
        assert self.cache_root is not None
        self.validate_cache_marker_at(self.cache_root, CACHE_MARKER_NAME, CACHE_MARKER_VALUE)

    def _owned_cache_targets(
        self, *, include_legacy: bool,
    ) -> list[tuple[Path, tuple[object, ...]]]:
        current = self.resolve_cache_root()
        candidates = [(current, CACHE_MARKER_NAME, CACHE_MARKER_VALUE)]
        if include_legacy and self._cache_root is None:
            legacy_name, legacy_marker, legacy_value = LEGACY_CACHE
            candidates.append((current.parent / legacy_name, legacy_marker, legacy_value))
        owned: list[tuple[Path, tuple[object, ...]]] = []
        for root, marker_name, marker_value in candidates:
            if not lexists(root):
                continue
            ensure_no_symlink(root, "cache root")
            if not root.is_dir():
                raise InstallerError(f"O caminho reservado ao cache não é um diretório: {root}")
            before = quarantine.observe_quarantine_target(root)
            self.validate_cache_marker_at(root, marker_name, marker_value)
            after = quarantine.observe_quarantine_target(root)
            if before != after:
                raise InstallerError(
                    f"O diretório de cache mudou durante a validação: {root}"
                )
            owned.append((root, after))
        return owned

    def owned_cache_roots(self, *, include_legacy: bool) -> list[Path]:
        return [
            root for root, _ in self._owned_cache_targets(
                include_legacy=include_legacy,
            )
        ]

    def prepare_cache(self) -> None:
        root = self.resolve_cache_root()
        console.detail(f"Cache: {root}")
        try:
            self.target.relative_to(root)
        except ValueError:
            pass
        else:
            raise InstallerError("O destino da instalação não pode ficar dentro do cache do instalador.")
        try:
            root.relative_to(self.target)
        except ValueError:
            pass
        else:
            raise InstallerError("O cache do instalador não pode ficar dentro do destino da instalação.")
        ensure_no_symlink(root, "cache root")
        if not lexists(root):
            root.parent.mkdir(parents=True, exist_ok=True)
            try:
                private_fs.create_private_directory(root)
            except FileExistsError:
                pass
            except OSError as error:
                raise InstallerError(f"Não foi possível preparar o cache privado: {root}") from error
        ensure_no_symlink(root, "cache root")
        if not root.is_dir():
            raise InstallerError(f"O caminho reservado ao cache não é um diretório: {root}")
        marker = root / CACHE_MARKER_NAME
        if lexists(marker):
            self._prepare_cache_marker_at(root, CACHE_MARKER_NAME, CACHE_MARKER_VALUE)
        else:
            if any(root.iterdir()):
                raise InstallerError(f"O diretório de cache contém arquivos que não pertencem ao instalador e foi preservado: {root}")
            try:
                descriptor = private_fs.create_private_file(marker)
            except FileExistsError:
                self._prepare_cache_marker_at(root, CACHE_MARKER_NAME, CACHE_MARKER_VALUE)
            except OSError as error:
                raise InstallerError(f"Não foi possível criar o marcador privado do cache: {marker}") from error
            else:
                try:
                    reservation_identity = persistent_descriptor_identity(
                        descriptor, directory=False,
                    )
                except OSError as error:
                    raise InstallerError(
                        f"Não foi possível confirmar o marcador privado do cache: {marker}"
                    ) from error
                finally:
                    os.close(descriptor)
                try:
                    private_fs.protect_private_directory(root)
                    current = marker.lstat()
                    if (
                        stat.S_ISLNK(current.st_mode)
                        or not stat.S_ISREG(current.st_mode)
                        or persistent_path_identity(marker, directory=False)
                        != reservation_identity
                    ):
                        raise InstallerError(
                            f"O marcador reservado do cache mudou de identidade: {marker}"
                        )
                    atomic_write_bytes(
                        marker,
                        (CACHE_MARKER_VALUE + "\n").encode("utf-8"),
                        mode=0o600,
                    )
                    published_identity = persistent_path_identity(
                        marker, directory=False,
                    )
                    foreign = tuple(entry for entry in root.iterdir() if entry != marker)
                    if foreign:
                        private_fs.unlink_private_file(
                            marker,
                            expected_identity=published_identity,
                        )
                        raise InstallerError(
                            "O diretório de cache contém arquivos que não pertencem ao "
                            f"instalador e foi preservado: {root}"
                        )
                    private_fs.validate_private_file(marker)
                except InstallerError:
                    raise
                except OSError as error:
                    try:
                        private_fs.unlink_private_file(
                            marker,
                            expected_identity=reservation_identity,
                        )
                    except OSError:
                        pass
                    raise InstallerError(
                        f"Não foi possível finalizar o marcador privado do cache: {marker}"
                    ) from error
        assert self.cache_bin is not None
        ensure_no_symlink(self.cache_bin, "cache directory")

    def publish_cache_artifact(
        self,
        source: Path,
        destination: Path,
        *,
        expected_size: int,
        expected_sha256: str,
        label: str,
    ) -> Path:
        """Copy verified bytes through a private sibling before cache publication."""

        try:
            source_info = source.lstat()
        except OSError as error:
            raise InstallerError(f"Não foi possível inspecionar {label}: {source}") from error
        if (
            stat.S_ISLNK(source_info.st_mode)
            or not stat.S_ISREG(source_info.st_mode)
            or source_info.st_size != expected_size
        ):
            raise InstallerError(f"O tamanho de {label} é inválido: {source}")
        try:
            result = atomic_copy_file(
                source,
                destination,
                expected_sha256=expected_sha256,
            )
        except (AtomicWriteError, OSError) as error:
            raise InstallerError(f"Não foi possível publicar {label} no cache.") from error
        if (
            result.bytes_written != expected_size
            or destination.stat().st_size != expected_size
            or file_hash(destination, maximum_size=MAX_ARTIFACT_BYTES) != expected_sha256
        ):
            raise InstallerError(f"{label.capitalize()} falhou na verificação após a cópia.")
        return destination

    def cache_is_present(self) -> bool:
        root = self.resolve_cache_root()
        if not lexists(root):
            return False
        ensure_no_symlink(root, "cache root")
        if not root.is_dir():
            raise InstallerError(f"O caminho reservado ao cache não é um diretório: {root}")
        self.validate_cache_marker()
        return True

    def cleanup_cache(self) -> None:
        targets = self._owned_cache_targets(include_legacy=True)
        roots = [root for root, _ in targets]
        if not roots:
            console.info(f"Nenhum cache do instalador foi encontrado em {self.cache_root}.")
            return
        result = self._quarantine_paths_transaction(
            roots,
            identifier="cleanup-native-cache",
            summary="Recolher caches nativos gerenciados",
            expected_observations=dict(targets),
        )
        if result is not None:
            finalize_mutation(result)
        for root in roots:
            console.success(f"Cache removido: {root}")

    def managed_runtime_paths(self) -> set[str]:
        metadata = self.target / METADATA_DIR
        if not metadata.is_dir() or metadata.is_symlink():
            return set()
        managed: set[str] = set()
        inventories = {
            *metadata.glob("*.inventory"),
            *(metadata / "components").glob("*/inventory"),
        }
        for inventory in sorted(inventories):
            managed.update(name for name, _ in self.validate_inventory(inventory))
        return managed

    @staticmethod
    def _minimal_removal_paths(paths: Iterable[Path]) -> tuple[Path, ...]:
        """Drop descendants when an already selected ancestor removes the same data."""

        selected: list[Path] = []
        for path in sorted(set(paths), key=lambda item: (len(item.parts), str(item))):
            if any(parent == path or parent in path.parents for parent in selected):
                continue
            selected.append(path)
        return tuple(selected)

    def _remove_paths_transaction(
        self,
        paths: Iterable[Path],
        *,
        identifier: str,
        summary: str,
    ) -> MutationResult | None:
        selected = self._minimal_removal_paths(paths)
        if not selected:
            return None
        if self.stage is None:
            self._create_stage(f".{identifier}.")
        steps = tuple(
            MutationStep(
                key=f"remove-{index}",
                description=f"Recolher {path.relative_to(self.target)}",
                observe=lambda path=path: self._mutation_path_observation(path),
                apply=lambda path=path: self._apply_managed_path_removal(
                    path, label=f"caminho selecionado {path}",
                ),
                rollback=self._rollback_runtime_payload,
            )
            for index, path in enumerate(selected, 1)
        )
        plan = MutationPlan(identifier=identifier, summary=summary, steps=steps)
        try:
            return execute_mutation(prepare_mutation(plan))
        except MutationRollbackError:
            raise
        except MutationApplyError as error:
            if isinstance(error.operation_error, InstallerError):
                raise error.operation_error
            raise InstallerError(
                f"{summary} falhou; a instalação anterior foi restaurada."
            ) from error

    def _quarantine_paths_transaction(
        self,
        paths: Iterable[Path],
        *,
        identifier: str,
        summary: str,
        expected_observations: Mapping[Path, tuple[object, ...]] | None = None,
    ) -> MutationResult | None:
        selected = self._minimal_removal_paths(paths)
        if not selected:
            return None
        observations = {
            path: (
                expected_observations[path]
                if expected_observations is not None
                and path in expected_observations
                else quarantine.observe_quarantine_target(path)
            )
            for path in selected
        }
        plan = MutationPlan(
            identifier=identifier,
            summary=summary,
            steps=tuple(
                MutationStep(
                    key=f"domain-{index}",
                    description=f"Recolher {path}",
                    observe=lambda path=path: (
                        quarantine.observe_quarantine_target(path)
                    ),
                    apply=lambda path=path, expected=observations[path]: (
                        quarantine.apply_quarantine_removal(
                            path, expected_observation=expected,
                            allow_non_regular=True,
                        )
                    ),
                    rollback=quarantine.rollback_quarantine,
                    finalize=quarantine.finalize_quarantine,
                )
                for index, path in enumerate(selected, 1)
            ),
        )
        try:
            return execute_mutation(prepare_mutation(plan))
        except MutationRollbackError:
            raise
        except MutationApplyError as error:
            if isinstance(error.operation_error, InstallerError):
                raise error.operation_error
            raise InstallerError(
                f"{summary} falhou; os domínios anteriores foram restaurados."
            ) from error

    def _runtime_cleanup_selection(
        self, *, downloads: bool, personal_data: bool,
    ) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
        if not self.target.is_dir() or self.target.is_symlink():
            return (), ()
        cache_paths: list[Path] = []
        personal_paths: list[Path] = []

        for relative in ("ezquake/sb/cache", "ezquake/temp"):
            path = self.target / relative
            if lexists(path):
                cache_paths.append(path)

        fortress = self.target / "fortress"
        if fortress.is_dir() and not fortress.is_symlink():
            for temporary in sorted(fortress.rglob("*.tmp")):
                if temporary.is_file() and not temporary.is_symlink():
                    cache_paths.append(temporary)

        demos = self.target / "td2/demos"
        if demos.is_dir() and not demos.is_symlink():
            for artifact in sorted(demos.iterdir()):
                if artifact.is_file() and not artifact.is_symlink() and artifact.stat().st_size == 0:
                    cache_paths.append(artifact)

        if downloads and fortress.is_dir() and not fortress.is_symlink():
            managed = self.managed_runtime_paths()
            for root_name in ("fortress/progs", "fortress/sound"):
                root = self.target / root_name
                if not root.is_dir() or root.is_symlink():
                    continue
                for artifact in sorted(root.rglob("*")):
                    if not artifact.is_file() or artifact.is_symlink():
                        continue
                    relative = artifact.relative_to(self.target).as_posix()
                    if relative not in managed:
                        cache_paths.append(artifact)

        if personal_data:
            for relative in (
                "ezquake/.ezquake_history",
                "qw/qconsole.log",
                "logs",
                "td2/demos",
            ):
                path = self.target / relative
                if lexists(path):
                    personal_paths.append(path)

        selected_personal = self._minimal_removal_paths(personal_paths)
        selected_cache = tuple(
            path
            for path in self._minimal_removal_paths(cache_paths)
            if not any(
                parent == path or parent in path.parents
                for parent in selected_personal
            )
        )
        return selected_cache, selected_personal

    def cleanup_data(
        self, *, downloads: bool, personal_data: bool,
    ) -> tuple[int, int]:
        self.require_managed_installation_identity("cleanup")
        cache_paths, personal_paths = self._runtime_cleanup_selection(
            downloads=downloads, personal_data=personal_data,
        )
        native_targets = self._owned_cache_targets(include_legacy=True)
        native_caches = [root for root, _ in native_targets]
        result = self._quarantine_paths_transaction(
            (*native_caches, *cache_paths, *personal_paths),
            identifier="cleanup-all-data",
            summary="Recolher caches e dados locais selecionados",
            expected_observations=dict(native_targets),
        )
        if result is not None:
            finalize_mutation(result)
        for root in native_caches:
            console.success(f"Cache removido: {root}")
        if not native_caches:
            console.info(f"Nenhum cache do instalador foi encontrado em {self.cache_root}.")
        return len(cache_paths), len(personal_paths)

    def cleanup_runtime_data(self, *, downloads: bool, personal_data: bool) -> tuple[int, int]:
        self.require_managed_installation_identity("cleanup")
        if not self.target.is_dir() or self.target.is_symlink():
            console.info(f"Nenhuma instalação local foi encontrada em {self.target}.")
            return 0, 0
        selected_cache, selected_personal = self._runtime_cleanup_selection(
            downloads=downloads, personal_data=personal_data,
        )
        selected = (*selected_cache, *selected_personal)
        if selected:
            owned_stage = self.stage is None
            cleanup_stage = True
            try:
                self._remove_paths_transaction(
                    selected,
                    identifier="cleanup-runtime-data",
                    summary="Remover dados selecionados pela limpeza",
                )
            except MutationRollbackError:
                cleanup_stage = False
                raise
            finally:
                if owned_stage and cleanup_stage:
                    self.cleanup_stage()
                    self.stage = None
        return len(selected_cache), len(selected_personal)

    def validate_pak_file(self, pak: Path, expected: str, label: str = "PAK") -> None:
        if not pak.is_file() or pak.is_symlink():
            raise InstallerError(f"{label} não encontrado: {pak}")
        with pak.open("rb") as source:
            if source.read(4) != b"PACK":
                raise InstallerError(f"O arquivo não possui um cabeçalho PAK válido: {pak}")
        if file_hash(pak) != expected:
            raise InstallerError(f"O PAK não corresponde à versão registrada original: {pak}")

    def check_pak(self, relative: str, expected: str) -> None:
        self.validate_pak_file(self.target / relative, expected, "PAK obrigatório")

    def check_paks(self) -> None:
        self.check_pak("id1/pak0.pak", ID1_PAK0_SHA256)
        self.check_pak("id1/pak1.pak", ID1_PAK1_SHA256)
        console.detail("PAKs registrados validados por SHA-256.")

    def _missing_install_directories(self) -> tuple[Path, ...]:
        missing_target: list[Path] = []
        current = self.target
        while not lexists(current):
            missing_target.append(current)
            parent = current.parent
            if parent == current:
                raise InstallerError(f"Não foi possível localizar o pai do destino: {self.target}")
            current = parent
        ensure_no_symlink(current, "installation target parent")
        if not current.is_dir():
            raise InstallerError(f"O pai do destino não é um diretório: {current}")

        if lexists(self.target):
            ensure_no_symlink(self.target, "installation target")
            if not self.target.is_dir():
                raise InstallerError(f"O destino não é um diretório: {self.target}")

        id1 = self.target / "id1"
        if lexists(id1):
            ensure_no_symlink(id1, "id1 directory")
            if not id1.is_dir():
                raise InstallerError(f"O caminho id1 não é um diretório: {id1}")
            missing_id1: tuple[Path, ...] = ()
        else:
            missing_id1 = (id1,)
        return (*reversed(missing_target), *missing_id1)

    def _rollback_install_topology(self, token: InstallTopologyRollback) -> None:
        errors: list[str] = []
        for directory, expected_identity in reversed(token.created_directories):
            if not lexists(directory):
                continue
            try:
                if self._directory_identity(directory) != expected_identity:
                    raise InstallerError(
                        f"Diretório criado mudou e foi preservado: {directory}"
                    )
                directory.rmdir()
            except BaseException as error:
                errors.append(str(error))
        if errors:
            raise InstallerError(
                "Rollback da topologia inicial ficou incompleto: " + "; ".join(errors)
            )

    def _apply_install_topology(
        self, directories: tuple[Path, ...],
    ) -> InstallTopologyRollback:
        created: list[tuple[Path, tuple[int, int]]] = []
        token = InstallTopologyRollback(())
        try:
            for directory in directories:
                if lexists(directory):
                    raise InstallerError(
                        f"Diretório apareceu durante a preparação: {directory}"
                    )
                parent = directory.parent
                ensure_no_symlink(parent, "installation directory parent")
                if not parent.is_dir():
                    raise InstallerError(
                        f"O pai da instalação deixou de ser um diretório: {parent}"
                    )
                directory.mkdir()
                created.append((directory, self._directory_identity(directory)))
            return InstallTopologyRollback(tuple(created))
        except BaseException as error:
            token = InstallTopologyRollback(tuple(created))
            try:
                self._rollback_install_topology(token)
            except BaseException as rollback_error:
                raise InstallerError(
                    "A preparação do destino falhou e o rollback ficou incompleto: "
                    f"{rollback_error}"
                ) from error
            raise

    def prepare_install_target(self) -> MutationResult | None:
        directories = self._missing_install_directories()
        if not directories:
            return None
        observed_paths = tuple(dict.fromkeys(
            (*(directory.parent for directory in directories), *directories)
        ))
        plan = MutationPlan(
            identifier="install-topology",
            summary="Preparar a topologia inicial da instalação",
            steps=(MutationStep(
                key="directories",
                description="Criar somente os diretórios ausentes do destino",
                observe=lambda: tuple(
                    (path, self._mutation_path_observation(path))
                    for path in observed_paths
                ),
                apply=lambda: self._apply_install_topology(directories),
                rollback=self._rollback_install_topology,
            ),),
        )
        try:
            return execute_mutation(prepare_mutation(plan))
        except MutationApplyError as error:
            if isinstance(error.operation_error, InstallerError):
                raise error.operation_error
            raise InstallerError("Não foi possível preparar o destino da instalação.") from error

    def _rollback_core_paks(self, token: CorePakRollback) -> None:
        errors: list[str] = []
        for destination, expected_identity in reversed(token.installed):
            try:
                if not lexists(destination):
                    continue
                if self._regular_identity(destination) != expected_identity:
                    raise InstallerError(
                        f"PAK instalado mudou e foi preservado: {destination}"
                    )
                destination.unlink()
            except BaseException as error:
                errors.append(str(error))
        if errors:
            raise InstallerError(
                "Rollback dos PAKs registrados ficou incompleto: " + "; ".join(errors)
            )

    def _apply_core_paks(
        self, prepared: tuple[tuple[Path, Path], ...],
    ) -> CorePakRollback:
        token = CorePakRollback([])
        try:
            for source, destination in prepared:
                if lexists(destination):
                    raise InstallerError(
                        f"O destino do PAK passou a existir durante a instalação: {destination}"
                    )
                os.replace(source, destination)
                token.installed.append((destination, self._regular_identity(destination)))
            return token
        except BaseException as error:
            try:
                self._rollback_core_paks(token)
            except BaseException as rollback_error:
                raise InstallerError(
                    f"A instalação dos PAKs falhou e o rollback ficou incompleto: {rollback_error}"
                ) from error
            raise

    def provision_install_target(self) -> MutationResult | None:
        ensure_no_symlink(self.target, "installation target")
        id1 = self.target / "id1"
        ensure_no_symlink(id1, "id1 directory")
        if not self.target.is_dir() or not id1.is_dir():
            raise InstallerError("A topologia inicial da instalação não foi preparada.")
        requirements = (
            ("pak0.pak", ID1_PAK0_SHA256),
            ("pak1.pak", ID1_PAK1_SHA256),
        )
        missing: list[str] = []
        for name, expected in requirements:
            destination = self.target / "id1" / name
            if lexists(destination):
                self.validate_pak_file(destination, expected, "PAK existente")
            else:
                missing.append(name)
        if not missing:
            return None

        local_id1 = self.project_root / DEVELOPMENT_ID1_DIR
        local_sources = not self.online_only and local_id1.is_dir() and not local_id1.is_symlink()
        if local_sources:
            sources = {name: local_id1 / name for name, _ in requirements}
            for name, expected in requirements:
                self.validate_pak_file(sources[name], expected, "PAK permanente da distribuição")
        else:
            if self.stage is None:
                raise InstallerError("A preparação dos dados base exige uma área temporária ativa.")
            package = self.core_id1_package_record()
            artifact = self.download_component_package(package)
            managed, defaults = self.prepare_component_package(package, artifact)
            if defaults:
                raise InstallerError("O pacote de dados base contém defaults inesperados.")
            sources = {name: managed / "id1" / name for name, _ in requirements}
            actual = {
                path.relative_to(managed).as_posix()
                for path in managed.rglob("*") if path.is_file()
            }
            if actual != {f"id1/{name}" for name, _ in requirements}:
                raise InstallerError("O pacote de dados base contém arquivos inesperados.")
            for name, expected in requirements:
                self.validate_pak_file(sources[name], expected, "PAK do pacote de dados base")

        parent_managed = self.stage is not None
        with self.runtime_mutation_stage(
            ".x86qw-core-paks.", parent_managed=parent_managed,
        ):
            assert self.stage is not None
            prepared: list[tuple[Path, Path]] = []
            for name, expected in requirements:
                if name not in missing:
                    continue
                staged = self.stage / name
                shutil.copyfile(sources[name], staged)
                host_adapter.apply_mode(staged, 0o644)
                self.validate_pak_file(staged, expected, "Cópia temporária do PAK")
                prepared.append((staged, self.target / "id1" / name))
            frozen_prepared = tuple(prepared)
            plan = MutationPlan(
                identifier="core-paks",
                summary="Publicar os PAKs registrados da distribuição",
                steps=(MutationStep(
                    key="pak-files",
                    description="Publicar todos os PAKs registrados como uma unidade",
                    observe=lambda: tuple(
                        (
                            self._mutation_path_observation(source),
                            self._mutation_path_observation(destination),
                        )
                        for source, destination in frozen_prepared
                    ),
                    apply=lambda: self._apply_core_paks(frozen_prepared),
                    rollback=self._rollback_core_paks,
                ),),
            )
            try:
                result = execute_mutation(prepare_mutation(plan))
            except MutationApplyError as error:
                if isinstance(error.operation_error, InstallerError):
                    raise error.operation_error
                raise InstallerError(
                    "Os PAKs registrados não puderam ser publicados como uma unidade."
                ) from error
        console.success(
            f"Dados base preparados em {self.target / 'id1'} ({file_count(len(prepared))} copiados)."
        )
        return result

    def ensure_metadata_directory(self) -> None:
        metadata = self.target / METADATA_DIR
        ensure_no_symlink(metadata, "metadata directory")
        if lexists(metadata) and not metadata.is_dir():
            raise InstallerError(f"metadata path is not a directory: {metadata}")
        try:
            private_fs.ensure_private_directory(metadata)
        except OSError as error:
            raise InstallerError(f"metadata directory is not private: {metadata}") from error

    def select_platform(self, requested: str | None = None) -> PlatformSpec:
        host = host_platform.system() or "desconhecido"
        machine = host_platform.machine() or "arquitetura desconhecida"
        console.detail(f"Host detectado: {host} {machine}; Python {host_platform.python_version()}")
        detected = HOST_PLATFORMS.get(host)
        if requested is None and detected is None:
            raise InstallerError(
                f"O sistema {host} não é reconhecido automaticamente. "
                "Use --platform macos, --platform linux ou --platform windows."
            )
        key = requested or detected
        assert key is not None
        self.spec = PLATFORMS[key]
        if requested is None:
            console.success(f"Sistema detectado automaticamente: {self.spec.label}.")
        elif requested == detected:
            console.success(f"Cliente solicitado por --platform: {self.spec.label}.")
        else:
            detected_label = PLATFORMS[detected].label if detected is not None else host
            console.info(
                f"Cliente solicitado por --platform: {self.spec.label}; host detectado: {detected_label}."
            )
        return self.spec

    def component_platform_variant(self, identifier: str) -> str:
        runtime = next(
            (entry for entry in RUNTIMES.values() if entry.get("component") == identifier),
            None,
        )
        if runtime is None:
            raise InstallerError(f"O componente {identifier} não possui runtime declarativo.")
        detected = HOST_PLATFORMS.get(host_platform.system())
        system = self.spec.key if self.spec is not None else detected
        if system is None:
            raise InstallerError(
                f"Não foi possível selecionar a plataforma do componente {identifier}."
            )
        candidates = [
            entry for entry in runtime["platforms"]
            if isinstance(entry, dict) and entry.get("system") == system
        ]
        if len(candidates) != 1:
            raise InstallerError(
                f"O componente {identifier} não está disponível para {system}."
            )
        candidate = candidates[0]
        if detected == system:
            aliases = CAPABILITY_CATALOG.get("architecture_aliases", {})
            accepted = aliases.get(candidate["architecture"], []) if isinstance(aliases, dict) else []
            machine = str(host_platform.machine() or "").casefold()
            if machine not in {str(value).casefold() for value in accepted}:
                label = CAPABILITY_CATALOG.get("platform_labels", {}).get(
                    candidate["variant"], candidate["variant"],
                )
                raise InstallerError(
                    f"O componente {identifier} requer {label}; o host informou {machine or 'arquitetura desconhecida'}."
                )
        return str(candidate["variant"])

    def normalize_component_platform_payload(self, identifier: str, managed: Path) -> None:
        component = self.components[identifier]
        platform_files = component.get("platform_files")
        if platform_files is None:
            platform_files = [
                {
                    "platform": entry["platform"],
                    "package_path": entry["destination"],
                    "install_path": entry["install_destination"],
                }
                for entry in component.get("project_sources", [])
                if isinstance(entry, dict) and "platform" in entry
            ]
        if not platform_files:
            return
        if not isinstance(platform_files, list):
            raise InstallerError(f"Mapeamento de plataforma inválido para {identifier}.")
        selected = self.component_platform_variant(identifier)
        runtime = next(
            entry for entry in RUNTIMES.values()
            if entry.get("component") == identifier
        )
        runtime_platform = next(
            entry for entry in runtime["platforms"]
            if isinstance(entry, dict) and entry.get("variant") == selected
        )
        matches = [
            entry for entry in platform_files
            if isinstance(entry, dict) and entry.get("platform") == selected
        ]
        if len(matches) != 1:
            raise InstallerError(
                f"O pacote {identifier} não contém exatamente um executável para {selected}."
            )
        for entry in platform_files:
            if not isinstance(entry, dict):
                raise InstallerError(f"Mapeamento de plataforma inválido para {identifier}.")
            package_path = str(entry.get("package_path", ""))
            source = managed.joinpath(*PurePosixPath(package_path).parts)
            if not source.is_file() or source.is_symlink():
                raise InstallerError(
                    f"Executável de plataforma ausente no pacote {identifier}: {package_path}"
                )
            if entry.get("platform") != selected:
                source.unlink()
                continue
            install_path = str(entry.get("install_path", ""))
            destination = managed.joinpath(*PurePosixPath(install_path).parts)
            if lexists(destination):
                raise InstallerError(
                    f"O pacote {identifier} possui destino operacional duplicado: {install_path}"
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.replace(destination)
            if runtime_platform.get("permissions") == "executable":
                host_adapter.apply_mode(destination, 0o755)
        remove_empty_directories(managed / "platforms")

    def prompt_catalog(self, label: str, catalog: list[ReleaseRecord]) -> ReleaseRecord:
        if not navigation.supports_navigation():
            preview_size = 12
            visible = catalog[:preview_size]
            print(f"\nVersões {label} disponíveis (mais recente primeiro):")
            for index, record in enumerate(visible, 1):
                print(f"  {index:3d}) {record[0]}")
            if hidden := len(catalog) - len(visible):
                print(f"       ... mais {hidden} versões. Digite t para mostrar todas.")
            expanded = not hidden
            while True:
                try:
                    prompt = "Escolha o número ou a versão exata"
                    if not expanded:
                        prompt += ", ou t para listar todas"
                    answer = input(prompt + ": ").strip()
                except EOFError as error:
                    raise InstallerError("Nenhuma versão foi selecionada.") from error
                if not expanded and answer.casefold() in {"t", "todas", "all"}:
                    for index, record in enumerate(catalog[len(visible):], len(visible) + 1):
                        print(f"  {index:3d}) {record[0]}")
                    expanded = True
                    continue
                if answer.isdigit() and 1 <= int(answer) <= len(catalog):
                    return catalog[int(answer) - 1]
                exact = [record for record in catalog if record[0] == answer]
                if len(exact) == 1:
                    return exact[0]
                console.warning("Versão não encontrada. Use um número da lista ou informe o identificador completo.")
        selected = navigation.select_one(
            f"Versões {label} disponíveis",
            (
                navigation.MenuOption(
                    record[0], record[0],
                    "mais recente" if index == 0 else "",
                    aliases=(str(index + 1),),
                )
                for index, record in enumerate(catalog)
            ),
            breadcrumb="x86QW › Instalação › Versão",
            subtitle="Mais recente primeiro. Use a busca para localizar uma versão.",
            searchable=True,
        )
        if selected is None:
            raise InstallerError("Nenhuma versão foi selecionada.")
        return next(record for record in catalog if record[0] == selected)

    def choose_channel(self, requested: str | None = None) -> str:
        if requested is not None:
            if requested not in {"stable", "nightly"}:
                raise InstallerError(
                    f"Canal de instalação inválido: {requested}. Use stable ou nightly."
                )
            self.channel = requested
            console.success(f"Canal selecionado: {requested}")
            return requested
        channel = navigation.select_one(
            "Qual canal deseja instalar?",
            (
                navigation.MenuOption(
                    "stable", "Stable", "releases oficiais", aliases=("s",),
                ),
                navigation.MenuOption(
                    "nightly", "Nightly", "snapshots de desenvolvimento", aliases=("n",),
                ),
            ),
            breadcrumb="x86QW › Instalação › Canal",
            invalid_message="Opção inválida. Digite 1 para stable ou 2 para nightly.",
        )
        if channel is None:
            raise InstallerError("Nenhum canal foi selecionado.")
        self.channel = channel
        console.success(f"Canal selecionado: {channel}")
        return channel

    def confirm_components(self) -> bool:
        if self._non_interactive_install:
            # The profile is mandatory in non-interactive mode, so accepting
            # this phase is deterministic and never falls back to a prompt.
            if self._requested_profile not in {"essential", "recommended", "complete"}:
                raise InstallerError(
                    "Instalação não interativa exige --profile essential, recommended ou complete."
                )
            return True
        return navigation.confirm(
            "Instalar também os componentes x86QW?",
            breadcrumb="x86QW › Instalação › Conteúdo",
            description="KTX, mapas, recursos visuais e componentes do perfil escolhido.",
            default=True,
            invalid_message="Resposta inválida. Digite s para sim ou n para não.",
        )

    def confirm_client_only_install(self) -> bool:
        return bool(navigation.confirm(
            "Instalar somente o cliente ezQuake?",
            breadcrumb="x86QW › Instalação › Avançado › Somente cliente",
            description=(
                "Esta instalação não será jogável. Jogar recusará até você "
                "adicionar ao menos KTX."
            ),
            default=False,
            invalid_message="Resposta inválida. Digite s para sim ou n para não.",
        ))

    def choose_install_content(self) -> list[str] | None:
        if self._non_interactive_install:
            return self.choose_components()
        path = navigation.select_one(
            "Qual conteúdo deseja instalar?",
            (
                navigation.MenuOption(
                    "recommended",
                    "Recomendado",
                    "KTX, mapas e configuração para jogar agora",
                ),
                navigation.MenuOption(
                    "advanced",
                    "Avançado",
                    "essencial, completo, personalizado ou somente cliente",
                ),
            ),
            breadcrumb="x86QW › Instalação › Conteúdo",
            default=0,
            invalid_message="Opção inválida. Digite 1 para recomendado ou 2 para avançado.",
        )
        if path is None:
            raise InstallerError("Nenhum conteúdo foi selecionado.")
        if path == "recommended":
            return self.select_components_profile("recommended")
        while True:
            choice = navigation.select_one(
                "Opções avançadas de conteúdo",
                (
                    navigation.MenuOption(
                        "essential",
                        "Essencial",
                        "configuração, interface principal e KTX",
                    ),
                    navigation.MenuOption(
                        "complete",
                        "Completo",
                        f"todos os {len(self.components)} componentes atuais",
                    ),
                    navigation.MenuOption(
                        "custom",
                        "Personalizado",
                        "escolha componentes individualmente",
                    ),
                    navigation.MenuOption(
                        "client-only",
                        "Somente cliente",
                        "ezQuake sem mods; Jogar recusa até adicionar KTX",
                    ),
                ),
                breadcrumb="x86QW › Instalação › Avançado",
                invalid_message="Opção inválida. Digite 1 a 4.",
            )
            if choice is None:
                raise InstallerError("Nenhum conteúdo foi selecionado.")
            if choice == "client-only":
                if self.confirm_client_only_install():
                    console.warning(
                        "Somente cliente: Jogar recusará até instalar ao menos KTX."
                    )
                    self.selected_component_profile = "none"
                    self.requested_components = []
                    return None
                continue
            return self._resolve_component_selection(choice)

    def catalog_records(
        self,
        component: str,
        channel: str,
        version_pattern: re.Pattern[str],
        expected_filename: Callable[[str], str],
        architecture: str,
    ) -> list[ReleaseRecord]:
        assert self.spec is not None
        native_root = self._native_candidate_root()
        if native_root is not None:
            if component != "ezquake":
                raise InstallerError(f"O candidato nativo não declara o runtime {component}.")
            prefix = f"runtime/clients/ezquake/{channel}/"
            records: list[ReleaseRecord] = []
            artifacts = self._native_candidate_manifest()["artifacts"]
            assert isinstance(artifacts, dict)
            for raw_name in artifacts:
                if not isinstance(raw_name, str) or not raw_name.startswith(prefix):
                    continue
                parts = PurePosixPath(raw_name).parts
                if len(parts) != 7 or parts[5] != "macos-universal":
                    continue
                version, filename = parts[4], parts[6]
                if not version_pattern.fullmatch(version) or filename != expected_filename(version):
                    continue
                _, _, digest = self._native_candidate_artifact(raw_name)
                records.append((version, (f"https://candidate.invalid/{filename}",), digest))
            if not records:
                raise InstallerError(
                    f"Nenhuma versão {channel} de {component} foi encontrada no candidato nativo."
                )
            if len(records) != len({record[0] for record in records}):
                raise InstallerError(f"O candidato nativo contém versões duplicadas de {component}.")
            if channel == "nightly":
                records.sort(key=lambda record: record[0], reverse=True)
            else:
                records.sort(
                    key=lambda record: tuple(
                        int(part) for part in record[0].removeprefix("v").split(".")
                    ),
                    reverse=True,
                )
            return records
        catalog = self.public_catalog("Consultando o catálogo oficial x86QW...")
        packages = catalog["packages"]

        records: list[ReleaseRecord] = []
        for index, package in enumerate(packages):
            if not isinstance(package, dict):
                raise InstallerError(f"Entrada inválida no catálogo x86QW: packages[{index}].")
            if (
                package.get("component") != component
                or package.get("channel") != channel
                or package.get("platform") != self.spec.key
                or package.get("architecture") != architecture
            ):
                continue
            version = package.get("version")
            if not isinstance(version, str) or not version_pattern.fullmatch(version):
                raise InstallerError(f"Versão inválida no catálogo x86QW: packages[{index}].")
            filename = package.get("filename")
            if filename != expected_filename(version):
                raise InstallerError(f"Nome de artefato inválido no catálogo x86QW: packages[{index}].")
            if not isinstance(package.get("size"), int) or package["size"] <= 0:
                raise InstallerError(f"Tamanho inválido no catálogo x86QW: packages[{index}].")
            digest = package.get("sha256")
            if not isinstance(digest, str):
                raise InstallerError(f"SHA-256 ausente no catálogo x86QW: packages[{index}].")
            validate_hex(digest, HEX64, f"SHA-256 de packages[{index}]")
            if not isinstance(package.get("license"), str) or not package["license"].strip():
                raise InstallerError(f"Licença ausente no catálogo x86QW: packages[{index}].")
            validate_https_url(package.get("license_url"), f"licença de packages[{index}]")
            source_urls = package.get("source_urls")
            if (
                not isinstance(source_urls, list)
                or not source_urls
                or not all(isinstance(url, str) for url in source_urls)
                or len(source_urls) != len(set(source_urls))
            ):
                raise InstallerError(f"Fontes inválidas no catálogo x86QW: packages[{index}].")
            for source_url in source_urls:
                validate_https_url(source_url, f"fonte de packages[{index}]")
            if package.get("redistribution_reviewed") is not True:
                raise InstallerError(f"Redistribuição não revisada no catálogo x86QW: packages[{index}].")
            https_url_filename(package.get("origin_url"), f"origem de packages[{index}]")
            urls = package.get("urls")
            if (
                not isinstance(urls, list)
                or not urls
                or not all(isinstance(url, str) for url in urls)
                or len(urls) != len(set(urls))
            ):
                raise InstallerError(f"Mirrors inválidos no catálogo x86QW: packages[{index}].")
            for url in urls:
                if https_url_filename(url, f"mirror de packages[{index}]") != filename:
                    raise InstallerError(f"Mirror com nome inesperado no catálogo x86QW: packages[{index}].")
            distribution_path = package.get("distribution_path", "")
            if distribution_path:
                self.validate_distribution_path(distribution_path, filename)
            records.append((version, tuple(urls), digest))

        if not records:
            raise InstallerError(f"Nenhuma versão {channel} de {component} está disponível para {self.spec.label}.")
        if len(records) != len({record[0] for record in records}):
            raise InstallerError(f"O catálogo x86QW contém versões duplicadas de {component} para {self.spec.label}.")
        if channel == "nightly":
            records.sort(key=lambda record: record[0], reverse=True)
        else:
            records.sort(
                key=lambda record: tuple(int(part) for part in record[0].removeprefix("v").split(".")),
                reverse=True,
            )
        return records

    def stable_catalog(self) -> list[ReleaseRecord]:
        assert self.spec is not None
        return self.catalog_records(
            "ezquake", "stable", STABLE_VERSION,
            lambda version: self.spec.stable_archive,
            self.spec.architecture,
        )

    def nightly_catalog(self) -> list[ReleaseRecord]:
        assert self.spec is not None
        return self.catalog_records(
            "ezquake", "nightly", NIGHTLY_VERSION,
            lambda version: version + self.spec.nightly_suffix,
            self.spec.architecture,
        )

    def choose_release(self, requested: str | None = None) -> None:
        assert self.spec is not None
        catalog = self.stable_catalog() if self.channel == "stable" else self.nightly_catalog()
        if requested is None:
            selected = self.prompt_catalog(self.channel, catalog)
        elif requested == "latest":
            selected = catalog[0]
        else:
            matches = [record for record in catalog if record[0] == requested]
            if len(matches) != 1:
                raise InstallerError(
                    f"A versão {requested} não está disponível no canal {self.channel}."
                )
            selected = matches[0]
        self.configure_release(selected)
        console.success(f"Versão selecionada: {self.selected_version}")

    def configure_release(self, selected: ReleaseRecord) -> None:
        assert self.spec is not None
        self.selected_version, self.app_urls, self.app_expected_checksum = selected
        self.app_url = self.app_urls[0]
        self.app_archive_name = https_url_filename(self.app_url, "mirror selecionado")
        if self._public_catalog is not None:
            selected_packages = [
                package for package in self._public_catalog["packages"]
                if isinstance(package, dict)
                and package.get("component") == "ezquake"
                and package.get("version") == self.selected_version
                and package.get("channel") == self.channel
                and package.get("platform") == self.spec.key
                and package.get("architecture") == self.spec.architecture
            ]
            if len(selected_packages) != 1:
                raise InstallerError("O artefato selecionado não possui identidade única na distribuição.")
            self.app_distribution_path = str(selected_packages[0].get("distribution_path", ""))
            self.app_expected_size = int(selected_packages[0]["size"])
        elif self._native_candidate_root() is not None:
            _, _, size, _ = self._native_candidate_matching(
                lambda name: name == (
                    f"runtime/clients/ezquake/{self.channel}/{self.selected_version}/"
                    f"macos-universal/{self.app_archive_name}"
                ),
                "runtime ezQuake nativo",
            )
            self.app_expected_size = size
        console.detail(f"Artefato: {safe_url_for_log(self.app_url)}")
        if self.channel == "stable":
            if self.app_archive_name != self.spec.stable_archive:
                raise InstallerError("invalid stable archive name")
            validate_hex(self.app_expected_checksum, HEX64, f"stable archive SHA-256 for {self.selected_version}")
            self.app_checksum_kind = "sha256"
        else:
            if not NIGHTLY_VERSION.fullmatch(self.selected_version):
                raise InstallerError("invalid nightly selection")
            expected_name = self.selected_version + self.spec.nightly_suffix
            if self.app_archive_name != expected_name:
                raise InstallerError("invalid nightly archive name")
            validate_hex(self.app_expected_checksum, HEX64, f"nightly archive SHA-256 for {self.selected_version}")
            self.app_checksum_kind = "sha256"
        console.detail(f"Checksum publicado ({self.app_checksum_kind}): {self.app_expected_checksum}")

    def latest_release(self) -> ReleaseRecord:
        catalog = self.stable_catalog() if self.channel == "stable" else self.nightly_catalog()
        return catalog[0]

    def ensure_archive(self) -> Path:
        assert self.cache_bin is not None and self.stage is not None and self.spec is not None
        self.cache_bin.mkdir(parents=True, exist_ok=True)
        cache_name = self.app_archive_name
        if self.channel == "stable":
            cache_name = f"stable-{self.selected_version}-{cache_name}"
        elif self.cache_prefix:
            cache_name = f"{self.cache_prefix}-{cache_name}"
        archive = self.cache_bin / cache_name
        ensure_no_symlink(archive, "cached archive")
        if archive.is_file():
            if file_hash(archive, maximum_size=MAX_ARTIFACT_BYTES) != self.app_expected_checksum:
                raise InstallerError(f"O arquivo em cache falhou na verificação: {archive}. Execute cleanup e tente novamente.")
            if self.update_ui:
                console.download_result(
                    f"ezQuake {self.selected_version}", size=archive.stat().st_size, status="Cached",
                )
            else:
                console.info(f"Usando arquivo já disponível no cache: {self.app_archive_name}")
                console.success("Arquivo do cache validado.")
        else:
            if self._native_candidate_root() is not None:
                _, source, expected_size, expected_sha256 = self._native_candidate_matching(
                    lambda name: name == (
                        f"runtime/clients/ezquake/{self.channel}/{self.selected_version}/"
                        f"macos-universal/{self.app_archive_name}"
                    ),
                    "runtime ezQuake nativo",
                )
                self.publish_cache_artifact(
                    source,
                    archive,
                    expected_size=expected_size,
                    expected_sha256=expected_sha256,
                    label="o artefato ezQuake do candidato",
                )
                self.app_archive_sha256 = file_hash(archive)
                console.success(f"Artefato candidato validado: {self.app_archive_name}")
                return archive
            local = self.distribution_artifact(
                self.app_distribution_path, self.app_archive_name,
                expected_size=self.app_expected_size or None, expected_sha256=self.app_expected_checksum,
            ) if self.app_distribution_path else None
            if local is not None:
                self.publish_cache_artifact(
                    local,
                    archive,
                    expected_size=self.app_expected_size,
                    expected_sha256=self.app_expected_checksum,
                    label="o artefato ezQuake",
                )
                if self.update_ui:
                    console.download_result(
                        f"ezQuake {self.selected_version}", size=archive.stat().st_size, status="Loaded",
                    )
                else:
                    console.success(f"Artefato carregado da distribuição local: {self.app_distribution_path}")
                self.app_archive_sha256 = file_hash(
                    archive, maximum_size=MAX_ARTIFACT_BYTES,
                )
                return archive
            download = self.stage / f"{self.app_archive_name}.download"
            if not self.update_ui:
                console.info(f"Baixando {self.app_archive_name}...")
            else:
                console.download_start(
                    f"ezQuake {self.selected_version}",
                    size=self.app_expected_size or None,
                )
            _, self.app_url = self.remote.get_mirrors(
                self.app_urls or (self.app_url,),
                download,
                expected_size=self.app_expected_size,
                expected_sha256=self.app_expected_checksum,
                maximum_size=MAX_ARTIFACT_BYTES,
            )
            if file_hash(download, maximum_size=MAX_ARTIFACT_BYTES) != self.app_expected_checksum:
                raise InstallerError(
                    "O arquivo baixado falhou na verificação: "
                    f"{safe_url_for_log(self.app_url)}"
                )
            self.publish_cache_artifact(
                download,
                archive,
                expected_size=self.app_expected_size,
                expected_sha256=self.app_expected_checksum,
                label="o download do ezQuake",
            )
            if self.update_ui:
                console.download_result(
                    f"ezQuake {self.selected_version}", size=archive.stat().st_size,
                )
            else:
                console.success(f"Download concluído e validado ({format_bytes(archive.stat().st_size)}).")
        self.app_archive_sha256 = file_hash(
            archive, maximum_size=MAX_ARTIFACT_BYTES,
        )
        console.detail(f"SHA-256 local: {self.app_archive_sha256}")
        return archive

    def inspect_macos_app(self, app: Path) -> tuple[str, str]:
        verify = False
        if host_platform.system() == "Darwin":
            verify = macos.verify_app_signature
        return macos.inspect_ezquake_bundle(app, verify_signature=verify)

    def inspect_portable_binary(self, spec: PlatformSpec, binary: Path) -> str:
        return host_adapter.inspect_portable_binary(
            binary,
            platform_id=spec.key,
        )

    def prepare_runtime(self, archive: Path) -> Path:
        assert self.stage is not None and self.spec is not None
        prepared = self.stage / "prepared-runtime"
        if self.spec.key == "macos":
            extract = self.stage / "app"
            executable_members = (
                (self.spec.archive_executable,)
                if self.spec.archive_executable is not None else ()
            )
            safe_extract_zip(
                archive, extract, executable_members=executable_members,
            )
            source = extract / self.spec.archive_binary
            version, binary_hash = self.inspect_macos_app(source)
            if self.channel == "stable" and version != self.selected_version:
                raise InstallerError(f"stable bundle version is {version}, expected {self.selected_version}")
            if self.channel == "nightly" and f"-g{self.selected_version.rsplit('_', 1)[-1]}" not in version:
                raise InstallerError(f"nightly bundle {version} does not match {self.selected_version}")
            if self.channel == "nightly" and self.prepare_macos_nightly_app(source):
                version, binary_hash = self.inspect_macos_app(source)
            source.replace(prepared)
            self.app_bundle_version = version
            self.app_binary_sha256 = binary_hash
        else:
            if self.channel == "stable":
                extract = self.stage / "runtime"
                executable_members = (
                    (self.spec.archive_executable,)
                    if self.spec.archive_executable is not None else ()
                )
                safe_extract_zip(
                    archive, extract, executable_members=executable_members,
                )
                source = extract / self.spec.archive_binary
            else:
                source = archive
            if not source.is_file() or source.is_symlink():
                raise InstallerError(f"artifact is missing {self.spec.archive_binary}")
            shutil.copy2(source, prepared)
            if self.spec.key == "linux":
                host_adapter.apply_mode(prepared, 0o755)
            self.app_binary_sha256 = self.inspect_portable_binary(self.spec, prepared)
            self.app_bundle_version = self.selected_version
        return prepared

    def validate_ezquake_receipt(self, path: Path, spec: PlatformSpec, channel: str) -> dict[str, str]:
        try:
            return parse_ezquake_receipt(
                read_bounded_regular_file(path, maximum_size=MAX_RECEIPT_BYTES),
                context=EzQuakeReceiptContext(
                    platform=spec.key,
                    architecture=spec.architecture,
                    channel=channel,
                    install_name=spec.runtime(channel),
                    stable_archive=spec.stable_archive,
                    nightly_suffix=spec.nightly_suffix,
                ),
            ).to_legacy_dict()
        except MetadataFileError as error:
            raise InstallerError(f"invalid ezQuake receipt: {path}") from error
        except ReceiptError as error:
            if error.code == "ezquake_platform":
                message = f"invalid platform metadata in ezQuake receipt: {path}"
            elif error.code == "ezquake_target":
                message = f"invalid target metadata in ezQuake receipt: {path}"
            elif error.code == "ezquake_hash":
                message = f"invalid {error.field_name}"
            elif error.code == "ezquake_stable_selection":
                message = f"invalid stable selection in ezQuake receipt: {error.value}"
            elif error.code == "ezquake_nightly_selection":
                message = f"invalid nightly selection in ezQuake receipt: {error.value}"
            elif error.code == "ezquake_macos_bundle":
                message = "nightly bundle version differs from ezQuake selection"
            elif error.code == "ezquake_nightly_bundle":
                message = "nightly version differs from ezQuake selection"
            elif error.code == "ezquake_artifact_url":
                message = str(error)
            elif error.code == "ezquake_artifact":
                message = f"unexpected artifact in ezQuake receipt: {path}"
            else:
                message = f"invalid ezQuake receipt: {path}"
            raise InstallerError(message) from error

    def write_ezquake_receipt(self, path: Path) -> None:
        assert self.spec is not None
        self.write_ezquake_receipt_record(path, {
            "format": "1", "platform": self.spec.key, "architecture": self.spec.architecture,
            "channel": self.channel, "selection": self.selected_version,
            "install_name": self.spec.runtime(self.channel), "bundle_version": self.app_bundle_version,
            "artifact_name": self.app_archive_name, "artifact_url": self.app_url,
            "artifact_sha256": self.app_archive_sha256, "binary_sha256": self.app_binary_sha256,
        })
        self.validate_ezquake_receipt(path, self.spec, self.channel)

    def write_ezquake_receipt_record(self, path: Path, receipt: dict[str, str]) -> None:
        try:
            atomic_write_bytes(
                path,
                serialize_ezquake_receipt(EzQuakeReceipt(**receipt)),
            )
        except (AtomicWriteError, TypeError) as error:
            raise InstallerError(f"Não foi possível gravar o recibo ezQuake: {path}") from error

    def repair_installed_macos_runtime(
        self,
        spec: PlatformSpec,
        channel: str,
        receipt_path: Path,
        receipt: dict[str, str],
        *,
        mutation_results: list[MutationResult] | None = None,
    ) -> dict[str, str]:
        runtime = self.target / spec.runtime(channel)
        if channel == "stable":
            raise InstallerError(
                "O ezQuake stable deve ser restaurado do artefato upstream integral pelo bootstrap."
            )
        if spec.key != "macos":
            return receipt
        reject_tree_symlinks(runtime, "bundle macOS gerenciado")
        if not self.macos_app_needs_preparation(runtime):
            return receipt
        self.spec = spec
        self.channel = channel
        self.ensure_macos_ezquake_closed()
        parent_managed = mutation_results is not None
        with self.runtime_mutation_stage(
            ".x86qw-macos-repair.", parent_managed=parent_managed,
        ):
            assert self.stage is not None
            prepared = self.stage / "prepared-runtime"
            shutil.copytree(runtime, prepared)
            self.prepare_macos_nightly_app(prepared)
            _, binary_hash = self.inspect_macos_app(prepared)
            updated = dict(receipt)
            updated["binary_sha256"] = binary_hash
            staged_receipt = self.stage / "ezquake-receipt"
            self.write_ezquake_receipt_record(staged_receipt, updated)
            self.validate_ezquake_receipt(staged_receipt, spec, channel)
            try:
                result = self.commit_runtime(prepared, staged_receipt)
            except RuntimeCommitPersistenceError as error:
                if mutation_results is not None:
                    mutation_results.append(error.result)
                raise
            if mutation_results is not None:
                mutation_results.append(result)
        console.success(f"ezQuake {channel} reparado para o macOS atual.")
        return updated

    def check_runtime(self, spec: PlatformSpec, channel: str, receipt: dict[str, str]) -> None:
        runtime = self.target / spec.runtime(channel)
        if spec.key == "macos":
            version, binary_hash = self.inspect_macos_app(runtime)
            if version != receipt["bundle_version"]:
                raise InstallerError(f"unexpected version in {runtime}: {version}")
        else:
            binary_hash = self.inspect_portable_binary(spec, runtime)
        if binary_hash != receipt["binary_sha256"]:
            raise InstallerError(f"unexpected ezQuake executable hash: {runtime}")

    def ezquake_receipt_path(self, spec: PlatformSpec, channel: str) -> Path | None:
        canonical = self.target / spec.receipt(channel)
        legacy = self.target / spec.legacy_receipt(channel)
        canonical_exists, legacy_exists = lexists(canonical), lexists(legacy)
        if canonical_exists:
            canonical_receipt = self.validate_ezquake_receipt(canonical, spec, channel)
            if legacy_exists:
                legacy_receipt = self.validate_ezquake_receipt(legacy, spec, channel)
                if canonical_receipt != legacy_receipt:
                    raise InstallerError(
                        f"Os recibos novo e legado do ezQuake {spec.key} {channel} divergem."
                    )
            return canonical
        if legacy_exists:
            self.validate_ezquake_receipt(legacy, spec, channel)
            return legacy
        return None

    def check_runtime_destination_ownership(self) -> None:
        assert self.spec is not None
        runtime = self.target / self.spec.runtime(self.channel)
        receipt_path = self.ezquake_receipt_path(self.spec, self.channel)
        if lexists(runtime):
            expected_type = runtime.is_dir() if self.spec.key == "macos" else runtime.is_file()
            if not expected_type:
                raise InstallerError(f"invalid managed runtime path: {runtime}")
            if receipt_path is None:
                raise InstallerError(f"refusing to replace an unmanaged {self.spec.label} runtime: {runtime}")
            receipt = self.validate_ezquake_receipt(receipt_path, self.spec, self.channel)
            self.check_runtime(self.spec, self.channel, receipt)
        elif receipt_path is not None:
            self.validate_ezquake_receipt(receipt_path, self.spec, self.channel)

    @staticmethod
    def _runtime_path_identity(path: Path) -> tuple[int, int]:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise InstallerError(f"Caminho de runtime inválido: {path}")
        return int(metadata.st_dev), int(metadata.st_ino)

    def _rollback_runtime_payload(self, token: RuntimePayloadRollback) -> None:
        if token.installed_identity is not None and lexists(token.destination):
            if self._runtime_path_identity(token.destination) != token.installed_identity:
                raise InstallerError(
                    f"Runtime alterado foi preservado: {token.destination}"
                )
            remove_path(token.destination)
        if token.previous_identity is not None:
            if lexists(token.destination):
                raise InstallerError(
                    f"Destino do runtime ocupado foi preservado: {token.destination}"
                )
            if self._runtime_path_identity(token.previous) != token.previous_identity:
                raise InstallerError(
                    f"Backup do runtime alterado foi preservado: {token.previous}"
                )
            token.previous.replace(token.destination)

    def _apply_runtime_payload(
        self, prepared: Path, destination: Path,
    ) -> RuntimePayloadRollback:
        assert self.stage is not None
        backup_root = Path(tempfile.mkdtemp(prefix=".runtime-old.", dir=self.stage))
        previous = backup_root / "runtime"
        token = RuntimePayloadRollback(destination, None, previous, None)
        try:
            if lexists(destination):
                destination.replace(previous)
                token.previous_identity = self._runtime_path_identity(previous)
            prepared.replace(destination)
            token.installed_identity = self._runtime_path_identity(destination)
            return token
        except BaseException as error:
            try:
                self._rollback_runtime_payload(token)
            except BaseException as rollback_error:
                raise InstallerError(
                    f"Rollback do runtime falhou; recuperação mantida em {self.stage}: "
                    f"{rollback_error}"
                ) from error
            raise

    def _apply_managed_path_removal(
        self, destination: Path, *, label: str,
    ) -> RuntimePayloadRollback:
        """Move one managed node into the live transaction stage."""

        assert self.stage is not None
        backup_root = Path(tempfile.mkdtemp(prefix=".managed-old.", dir=self.stage))
        previous = backup_root / "previous"
        token = RuntimePayloadRollback(destination, None, previous, None)
        if not lexists(destination):
            return token
        try:
            token.previous_identity = self._runtime_path_identity(destination)
            destination.replace(previous)
            if self._runtime_path_identity(previous) != token.previous_identity:
                raise InstallerError(f"{label} mudou durante a remoção: {destination}")
            return token
        except BaseException as error:
            try:
                self._rollback_runtime_payload(token)
            except BaseException as rollback_error:
                raise InstallerError(
                    f"Rollback de {label} falhou; recuperação mantida em {self.stage}: "
                    f"{rollback_error}"
                ) from error
            raise

    def _rollback_runtime_receipt(self, token: RuntimeReceiptRollback) -> None:
        errors: list[str] = []
        if token.installed_identity is not None and lexists(token.destination):
            try:
                if self._runtime_path_identity(token.destination) != token.installed_identity:
                    raise InstallerError(
                        f"Recibo alterado foi preservado: {token.destination}"
                    )
                remove_path(token.destination)
            except BaseException as error:
                errors.append(str(error))
        for original, backup, identity in reversed(token.previous):
            try:
                if lexists(original):
                    raise InstallerError(
                        f"Destino do recibo ocupado foi preservado: {original}"
                    )
                if self._runtime_path_identity(backup) != identity:
                    raise InstallerError(
                        f"Backup do recibo alterado foi preservado: {backup}"
                    )
                original.parent.mkdir(parents=True, exist_ok=True)
                backup.replace(original)
            except BaseException as error:
                errors.append(str(error))
        try:
            self._rollback_created_directory_chain(
                token.created_directories, label="recibo ezQuake",
            )
        except BaseException as error:
            errors.append(str(error))
        if errors:
            raise InstallerError(
                "Rollback do recibo ezQuake ficou incompleto: " + "; ".join(errors)
            )

    def _apply_runtime_receipt(
        self,
        staged_receipt: Path,
        destination: Path,
        previous_paths: tuple[Path, ...],
        durability_errors: list[AtomicWriteError],
    ) -> RuntimeReceiptRollback:
        assert self.spec is not None and self.stage is not None
        backup_root = Path(tempfile.mkdtemp(prefix=".runtime-receipts-old.", dir=self.stage))
        created_directories = self._create_private_directory_chain(
            destination.parent,
            root=self.target / METADATA_DIR,
            label="recibo ezQuake",
        )
        token = RuntimeReceiptRollback(
            destination, None, [], created_directories,
        )
        try:
            for index, original in enumerate(previous_paths, 1):
                if not lexists(original):
                    continue
                identity = self._runtime_path_identity(original)
                backup = backup_root / f"receipt-{index}"
                original.replace(backup)
                token.previous.append((original, backup, identity))
            payload = read_bounded_regular_file(
                staged_receipt, maximum_size=MAX_RECEIPT_BYTES,
            )
            try:
                atomic_write_bytes(destination, payload)
            except AtomicWriteError as error:
                if lexists(destination):
                    token.installed_identity = self._runtime_path_identity(destination)
                if not error.committed:
                    self._rollback_runtime_receipt(token)
                    raise PersistenceError(
                        f"Recibo ezQuake não pôde ser promovido: {destination}",
                        committed=False,
                    ) from error
                self.validate_ezquake_receipt(destination, self.spec, self.channel)
                durability_errors.append(error)
                return token
            token.installed_identity = self._runtime_path_identity(destination)
            self.validate_ezquake_receipt(destination, self.spec, self.channel)
            return token
        except PersistenceError:
            raise
        except BaseException as error:
            try:
                self._rollback_runtime_receipt(token)
            except BaseException as rollback_error:
                raise InstallerError(
                    f"Rollback do recibo ezQuake falhou; recuperação mantida em "
                    f"{self.stage}: {rollback_error}"
                ) from error
            raise

    def commit_runtime(
        self, prepared: Path, staged_receipt: Path,
    ) -> MutationResult:
        assert self.spec is not None and self.stage is not None
        runtime = self.target / self.spec.runtime(self.channel)
        receipt = self.target / self.spec.receipt(self.channel)
        legacy_receipt = self.target / self.spec.legacy_receipt(self.channel)
        previous_receipts = tuple(dict.fromkeys((receipt, legacy_receipt)))
        self.ezquake_receipt_path(self.spec, self.channel)
        durability_errors: list[AtomicWriteError] = []
        plan = MutationPlan(
            identifier=f"runtime:{self.spec.key}:{self.channel}",
            summary=f"Instalar ezQuake {self.spec.label} {self.channel}",
            steps=(
                MutationStep(
                    key="runtime",
                    description="Publicar o runtime ezQuake",
                    observe=lambda: (
                        self._mutation_path_observation(prepared),
                        self._mutation_path_observation(runtime),
                    ),
                    apply=lambda: self._apply_runtime_payload(prepared, runtime),
                    rollback=self._rollback_runtime_payload,
                ),
                MutationStep(
                    key="receipt",
                    description="Publicar o recibo ezQuake",
                    observe=lambda: (
                        self._mutation_path_observation(staged_receipt),
                        *(self._mutation_path_observation(path) for path in previous_receipts),
                    ),
                    apply=lambda: self._apply_runtime_receipt(
                        staged_receipt, receipt, previous_receipts, durability_errors,
                    ),
                    rollback=self._rollback_runtime_receipt,
                ),
            ),
        )
        try:
            result = execute_mutation(prepare_mutation(plan))
        except MutationApplyError as error:
            if isinstance(error.operation_error, PersistenceError):
                raise error.operation_error
            if isinstance(error.operation_error, InstallerError):
                raise error.operation_error
            raise InstallerError(
                f"Não foi possível publicar {runtime} e seu recibo."
            ) from error
        if durability_errors:
            raise RuntimeCommitPersistenceError(
                f"Runtime e recibo foram promovidos, mas a durabilidade final falhou: {receipt}",
                result=result,
            ) from durability_errors[0]
        return result

    @staticmethod
    def validate_safe_inventory_path(value: str) -> PurePosixPath:
        if not value or "\\" in value or ":" in value:
            raise InstallerError(f"unsafe path in managed inventory: {value}")
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
            raise InstallerError(f"unsafe path in managed inventory: {value}")
        return path

    def validate_personal_baseline_path(self, value: str) -> None:
        self.validate_safe_inventory_path(value)
        if not is_user_profile_path(value):
            raise InstallerError(f"unexpected path in personal baseline: {value}")

    def validate_managed_path(self, value: str) -> None:
        path = self.validate_safe_inventory_path(value)
        if value in ("ezquake/configs/config.cfg", "ezquake/configs/preset.cfg"):
            raise InstallerError(f"personal configuration must not be managed: {value}")
        if path.parts[0] == "id1":
            if (
                len(path.parts) == 4
                and path.parts[1:3] == ("maps", "ctf")
                and path.suffix.casefold() == ".ent"
            ):
                return
            raise InstallerError(f"unexpected path in managed inventory: {value}")
        if value in {"mvdsv", "mvdsv.exe"}:
            return
        if len(path.parts) >= 3 and path.parts[:2] == ("docs", "licenses"):
            return
        if len(path.parts) >= 2 and path.parts[0] in {"qtv", "qwfwd"}:
            return
        if value not in ("LICENSE", "readme.txt", "README-X86QW.txt") and path.parts[0] not in (
            "ezquake", "qw", "arena", "prox", "fortress", "td2",
        ):
            raise InstallerError(f"unexpected path in managed inventory: {value}")

    def validate_inventory(self, path: Path) -> list[tuple[str, str]]:
        _, entries = self._read_inventory(path)
        return entries

    def _read_inventory(
        self,
        path: Path,
        *,
        path_validator: Callable[[str], None] | None = None,
    ) -> tuple[bytes, list[tuple[str, str]]]:
        try:
            payload = read_bounded_regular_file(
                path, maximum_size=MAX_INVENTORY_BYTES,
            )
            parsed = parse_inventory(payload)
        except MetadataFileError as error:
            if not path.is_file() or path.is_symlink():
                raise InstallerError(f"missing managed inventory: {path}") from error
            raise InstallerError(f"Inventário gerenciado inválido: {path}") from error
        except ReceiptError as error:
            if error.code == "inventory_entry":
                message = f"invalid managed inventory entry: {error.value}"
            elif error.code == "inventory_hash":
                message = f"invalid hash in managed inventory: {error.field_name}"
            else:
                message = f"Inventário gerenciado inválido: {path}"
            raise InstallerError(message) from error
        entries: list[tuple[str, str]] = []
        validate_path = path_validator or self.validate_managed_path
        for entry in parsed:
            validate_path(entry.path)
            entries.append((entry.path, entry.sha256))
        return payload, entries

    def create_inventory(self, managed: Path, destination: Path) -> list[tuple[str, str]]:
        entries: list[tuple[str, str]] = []
        for path in sorted((path for path in managed.rglob("*") if path.is_file()), key=lambda item: item.relative_to(managed).as_posix()):
            if path.is_symlink():
                raise InstallerError(f"distribution contains an unsupported symlink: {path}")
            relative = path.relative_to(managed).as_posix()
            self.validate_managed_path(relative)
            entries.append((relative, file_hash(path)))
        self.write_inventory_record(destination, entries)
        return entries

    def write_inventory_record(
        self,
        destination: Path,
        entries: Iterable[tuple[str, str]],
        *,
        path_validator: Callable[[str], None] | None = None,
    ) -> None:
        try:
            atomic_write_bytes(
                destination,
                serialize_inventory(
                    InventoryEntry(name, digest) for name, digest in entries
                ),
            )
        except AtomicWriteError as error:
            raise InstallerError(f"Inventário não pôde ser gravado: {destination}") from error
        self._read_inventory(destination, path_validator=path_validator)

    def component_metadata(self, component: str) -> tuple[str, str]:
        if not COMPONENT_VERSION.fullmatch(component):
            raise InstallerError(f"Componente desconhecido: {component}")
        return (
            f"{COMPONENT_METADATA_DIR}/{component}/receipt",
            f"{COMPONENT_METADATA_DIR}/{component}/inventory",
        )

    def legacy_component_metadata(self, component: str) -> tuple[str, str]:
        self.component_metadata(component)
        return f".x86qw/{component}.receipt", f".x86qw/{component}.inventory"

    @staticmethod
    def metadata_path(metadata: Path, relative: str) -> Path:
        parts = PurePosixPath(relative).parts
        if not parts or parts[0] != METADATA_DIR:
            raise InstallerError(f"Caminho de metadados inválido: {relative}")
        return metadata.joinpath(*parts[1:])

    def component_pair_paths(
        self, component: str, metadata: Path, *, legacy: bool = False,
    ) -> tuple[Path, Path]:
        relative = (
            self.legacy_component_metadata(component)
            if legacy else self.component_metadata(component)
        )
        return tuple(self.metadata_path(metadata, path) for path in relative)  # type: ignore[return-value]

    def validate_component_paths(
        self,
        component: str,
        receipt_path: Path,
        inventory_path: Path,
        *,
        path_validator: Callable[[str], None] | None = None,
    ) -> tuple[list[tuple[str, str]], dict[str, str]]:
        try:
            receipt = parse_component_receipt(
                read_bounded_regular_file(
                    receipt_path, maximum_size=MAX_RECEIPT_BYTES,
                ),
                component=component,
            ).to_legacy_dict()
        except MetadataFileError as error:
            raise InstallerError(f"Recibo inválido do componente {component}.") from error
        except ReceiptError as error:
            if error.code == "component_selection":
                message = f"Seleção inválida no recibo do componente {component}."
            elif error.code == "component_source":
                message = f"Origem inválida no recibo do componente {component}."
            elif error.code == "component_inventory_hash":
                message = f"invalid SHA-256 do inventário {component}"
            elif error.code == "component_identity":
                message = f"Recibo inválido do componente {component}."
            else:
                message = f"invalid recibo do componente {component}: {receipt_path}"
            raise InstallerError(message) from error
        inventory_payload, entries = self._read_inventory(
            inventory_path, path_validator=path_validator,
        )
        if hashlib.sha256(inventory_payload).hexdigest() != receipt["inventory_sha256"]:
            raise InstallerError(f"O inventário do componente {component} diverge do recibo.")
        return entries, receipt

    def validate_component_pair(self, component: str, metadata: Path | None = None) -> tuple[bool, list[tuple[str, str]], dict[str, str] | None]:
        metadata = metadata or self.target / METADATA_DIR
        canonical = self.component_pair_paths(component, metadata)
        legacy = self.component_pair_paths(component, metadata, legacy=True)
        canonical_exists = tuple(lexists(path) for path in canonical)
        legacy_exists = tuple(lexists(path) for path in legacy)
        if not any(canonical_exists) and not any(legacy_exists):
            return False, [], None
        if any(canonical_exists) and not all(canonical_exists):
            raise InstallerError(f"Metadados incompletos do componente {component}.")
        if any(legacy_exists) and not all(legacy_exists):
            raise InstallerError(f"Metadados legados incompletos do componente {component}.")
        if all(canonical_exists):
            entries, receipt = self.validate_component_paths(component, *canonical)
            if all(legacy_exists):
                legacy_entries, legacy_receipt = self.validate_component_paths(component, *legacy)
                if entries != legacy_entries or receipt != legacy_receipt:
                    raise InstallerError(
                        f"Os metadados novo e legado do componente {component} divergem."
                    )
            return True, entries, receipt
        entries, receipt = self.validate_component_paths(component, *legacy)
        return True, entries, receipt

    def filter_component_conflicts(self, component: str, managed: Path) -> None:
        present, old_entries, _ = self.validate_component_pair(component)
        old = dict(old_entries) if present else {}
        for source in sorted((path for path in managed.rglob("*") if path.is_file())):
            relative = source.relative_to(managed).as_posix()
            self.validate_managed_path(relative)
            destination = self.target.joinpath(*PurePosixPath(relative).parts)
            if not lexists(destination):
                continue
            if not destination.is_file() or destination.is_symlink():
                raise InstallerError(f"Caminho existente não é um arquivo regular: {destination}")
            expected = old.get(relative)
            if expected is None or file_hash(destination) != expected:
                console.warning(f"Arquivo pessoal ou modificado preservado: {destination}")
                source.unlink()
        remove_empty_directories(managed)

    def write_component_receipt(self, component: str, selection: str, source: str, inventory: Path, destination: Path) -> None:
        receipt = ComponentReceipt(
            "1", component, selection, source, file_hash(inventory),
        )
        try:
            atomic_write_bytes(destination, serialize_component_receipt(receipt))
        except AtomicWriteError as error:
            raise InstallerError(
                f"Recibo do componente {component} não pôde ser gravado."
            ) from error

    @staticmethod
    def _directory_identity(path: Path) -> tuple[int, int]:
        metadata = path.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise InstallerError(f"Diretório gerenciado inválido: {path}")
        return int(metadata.st_dev), int(metadata.st_ino)

    @staticmethod
    def _regular_identity(path: Path) -> tuple[int, int]:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise InstallerError(f"Arquivo gerenciado inválido: {path}")
        return int(metadata.st_dev), int(metadata.st_ino)

    def _rollback_created_directory_chain(
        self,
        created: tuple[tuple[Path, tuple[int, int]], ...],
        *,
        label: str,
    ) -> None:
        errors: list[str] = []
        for directory, expected_identity in reversed(created):
            if not lexists(directory):
                continue
            try:
                if self._directory_identity(directory) != expected_identity:
                    raise InstallerError(
                        f"Diretório mudou e foi preservado: {directory}"
                    )
                directory.rmdir()
            except BaseException as error:
                errors.append(str(error))
        if errors:
            raise InstallerError(
                f"Rollback da topologia de {label} ficou incompleto: "
                + "; ".join(errors)
            )

    def _create_private_directory_chain(
        self, destination: Path, *, root: Path, label: str,
    ) -> tuple[tuple[Path, tuple[int, int]], ...]:
        try:
            destination.relative_to(root)
        except ValueError as error:
            raise InstallerError(
                f"Diretório de {label} escapa da raiz privada: {destination}"
            ) from error
        if not root.is_dir() or root.is_symlink():
            raise InstallerError(f"Raiz privada de {label} inválida: {root}")
        missing: list[Path] = []
        current = destination
        while current != root and not lexists(current):
            missing.append(current)
            current = current.parent
        if current != root:
            if not current.is_dir() or current.is_symlink():
                raise InstallerError(f"Diretório de {label} inválido: {current}")
        created: list[tuple[Path, tuple[int, int]]] = []
        try:
            for directory in reversed(missing):
                private_fs.ensure_private_directory(directory)
                created.append((directory, self._directory_identity(directory)))
            return tuple(created)
        except BaseException as error:
            try:
                self._rollback_created_directory_chain(
                    tuple(created), label=label,
                )
            except BaseException as rollback_error:
                raise InstallerError(
                    f"A criação da topologia de {label} falhou e o rollback "
                    f"ficou incompleto: {rollback_error}"
                ) from error
            raise

    def _rollback_component_metadata(self, token: ComponentMetadataRollback) -> None:
        errors: list[str] = []
        if token.installed_identity is not None and lexists(token.destination):
            try:
                if self._directory_identity(token.destination) != token.installed_identity:
                    raise InstallerError(
                        f"Metadados alterados foram preservados: {token.destination}"
                    )
                remove_path(token.destination)
            except BaseException as error:
                errors.append(str(error))
        if token.previous_identity is not None:
            try:
                if lexists(token.destination):
                    raise InstallerError(
                        f"Destino de metadados ocupado foi preservado: {token.destination}"
                    )
                if self._directory_identity(token.previous) != token.previous_identity:
                    raise InstallerError(
                        f"Backup de metadados alterado foi preservado: {token.previous}"
                    )
                token.previous.replace(token.destination)
            except BaseException as error:
                errors.append(str(error))
        for original, backup, identity in reversed(token.legacy_backups):
            try:
                if lexists(original):
                    raise InstallerError(
                        f"Metadado legado reapareceu e foi preservado: {original}"
                    )
                if self._regular_identity(backup) != identity:
                    raise InstallerError(
                        f"Backup legado alterado foi preservado: {backup}"
                    )
                original.parent.mkdir(parents=True, exist_ok=True)
                backup.replace(original)
            except BaseException as error:
                errors.append(str(error))
        try:
            self._rollback_created_directory_chain(
                token.created_directories, label="metadados de componente",
            )
        except BaseException as error:
            errors.append(str(error))
        if errors:
            raise InstallerError(
                "Rollback dos metadados ficou incompleto: " + "; ".join(errors)
            )

    def commit_component_metadata(
        self, component: str, inventory: Path, receipt: Path,
    ) -> ComponentMetadataRollback:
        metadata = self.target / METADATA_DIR
        destination = self.metadata_path(
            metadata, self.component_metadata(component)[0],
        ).parent
        return self._commit_metadata_pair(
            component,
            inventory,
            receipt,
            destination,
            legacy_paths=self.component_pair_paths(
                component, metadata, legacy=True,
            ),
        )

    def _commit_metadata_pair(
        self,
        component: str,
        inventory: Path,
        receipt: Path,
        destination: Path,
        *,
        legacy_paths: tuple[Path, ...] = (),
        path_validator: Callable[[str], None] | None = None,
    ) -> ComponentMetadataRollback:
        assert self.stage is not None
        self.ensure_metadata_directory()
        metadata = self.target / METADATA_DIR
        if lexists(destination) and (not destination.is_dir() or destination.is_symlink()):
            raise InstallerError(f"Diretório de metadados inválido para {component}: {destination}")
        prepared = Path(tempfile.mkdtemp(
            prefix=f".{component}-metadata-next.", dir=self.stage,
        ))
        previous = Path(tempfile.mkdtemp(
            prefix=f".{component}-metadata-old.", dir=self.stage,
        ))
        previous.rmdir()
        shutil.copy2(receipt, prepared / "receipt")
        shutil.copy2(inventory, prepared / "inventory")
        self.validate_component_paths(
            component,
            prepared / "receipt",
            prepared / "inventory",
            path_validator=path_validator,
        )
        created_directories = self._create_private_directory_chain(
            destination.parent,
            root=metadata,
            label="metadados de componente",
        )
        token = ComponentMetadataRollback(
            destination=destination,
            installed_identity=None,
            previous=previous,
            previous_identity=None,
            legacy_backups=[],
            created_directories=created_directories,
        )
        try:
            if lexists(destination):
                destination.replace(previous)
                token.previous_identity = self._directory_identity(previous)
            prepared.replace(destination)
            token.installed_identity = self._directory_identity(destination)
            for index, legacy in enumerate(legacy_paths, 1):
                if lexists(legacy):
                    backup_root = Path(tempfile.mkdtemp(
                        prefix=f".{component}-legacy-{index}.", dir=self.stage,
                    ))
                    backup = backup_root / "metadata"
                    identity = self._regular_identity(legacy)
                    legacy.replace(backup)
                    token.legacy_backups.append((legacy, backup, identity))
                    if self._regular_identity(backup) != identity:
                        raise InstallerError(
                            f"Metadado legado mudou durante a migração: {legacy}"
                        )
            return token
        except BaseException as error:
            try:
                self._rollback_component_metadata(token)
            except BaseException as rollback_error:
                raise InstallerError(f"Rollback dos metadados falhou; recuperação mantida em {self.stage}: {rollback_error}") from error
            raise InstallerError(f"Não foi possível registrar o componente {component}.") from error

    def personal_baseline_paths(
        self, metadata: Path | None = None,
    ) -> tuple[Path, Path]:
        root = metadata or self.target / METADATA_DIR
        directory = self.metadata_path(root, PERSONAL_BASELINE_DIR)
        return directory / "receipt", directory / "inventory"

    def validate_personal_baseline(
        self, metadata: Path | None = None,
    ) -> tuple[bool, list[tuple[str, str]]]:
        receipt, inventory = self.personal_baseline_paths(metadata)
        present = (lexists(receipt), lexists(inventory))
        if not any(present):
            return False, []
        if not all(present):
            raise InstallerError("Baseline pessoal incompleto.")
        entries, _ = self.validate_component_paths(
            PERSONAL_BASELINE_COMPONENT,
            receipt,
            inventory,
            path_validator=self.validate_personal_baseline_path,
        )
        return True, entries

    def _personal_baseline_observation(
        self,
    ) -> tuple[tuple[str, tuple[object, ...]], ...]:
        return tuple(
            (str(path), self._mutation_path_observation(path))
            for path in self.personal_baseline_paths()
        )

    def _stage_personal_baseline_entry(
        self, destination: Path, digest: str,
    ) -> tuple[Path, Path]:
        assert self.stage is not None
        relative = destination.relative_to(self.target).as_posix()
        self.validate_personal_baseline_path(relative)
        present, existing = self.validate_personal_baseline()
        entries = dict(existing) if present else {}
        entries[relative] = digest
        staged = private_fs.private_mkdtemp(
            directory=self.stage, prefix=".personal-baseline-next.",
        )
        inventory = staged / "inventory"
        receipt = staged / "receipt"
        self.write_inventory_record(
            inventory,
            sorted(entries.items()),
            path_validator=self.validate_personal_baseline_path,
        )
        self.write_component_receipt(
            PERSONAL_BASELINE_COMPONENT,
            "1",
            "x86QW personal defaults",
            inventory,
            receipt,
        )
        return receipt, inventory

    def _commit_personal_baseline(
        self, receipt: Path, inventory: Path,
    ) -> ComponentMetadataRollback:
        destination = self.personal_baseline_paths()[0].parent
        return self._commit_metadata_pair(
            PERSONAL_BASELINE_COMPONENT,
            inventory,
            receipt,
            destination,
            path_validator=self.validate_personal_baseline_path,
        )

    @staticmethod
    def _mutation_path_observation(path: Path) -> tuple[object, ...]:
        if not lexists(path):
            return ("absent",)
        try:
            metadata = path.lstat()
        except OSError as error:
            raise InstallerError(f"Caminho mudou durante o planejamento: {path}") from error
        identity = (
            int(metadata.st_dev),
            int(metadata.st_ino),
            stat.S_IFMT(metadata.st_mode),
            int(metadata.st_size),
            int(getattr(metadata, "st_mtime_ns", metadata.st_mtime * 1_000_000_000)),
        )
        if stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
            return ("file", *identity, file_hash(path))
        return ("node", *identity)

    def _component_payload_observation(
        self,
        managed: Path,
        names: Iterable[str],
    ) -> tuple[tuple[str, tuple[object, ...], tuple[object, ...]], ...]:
        result = []
        for name in sorted(set(names)):
            relative = PurePosixPath(name)
            result.append((
                name,
                self._mutation_path_observation(managed.joinpath(*relative.parts)),
                self._mutation_path_observation(self.target.joinpath(*relative.parts)),
            ))
        return tuple(result)

    def _component_metadata_observation(self, component: str) -> tuple[tuple[str, tuple[object, ...]], ...]:
        metadata = self.target / METADATA_DIR
        paths = [
            self.metadata_path(metadata, relative)
            for relative in (
                *self.component_metadata(component),
                *self.legacy_component_metadata(component),
            )
        ]
        return tuple(
            (str(path), self._mutation_path_observation(path)) for path in paths
        )

    def _rollback_component_payload(self, token: ComponentPayloadRollback) -> None:
        errors: list[str] = []
        for name, new_digest in reversed(tuple(token.new_hashes.items())):
            relative = PurePosixPath(name)
            destination = self.target.joinpath(*relative.parts)
            previous_digest = token.original_hashes[name]
            if not lexists(destination):
                current_digest = None
            elif destination.is_file() and not destination.is_symlink():
                current_digest = file_hash(destination)
            else:
                errors.append(f"tipo inesperado em {destination}")
                continue
            if previous_digest is None:
                if current_digest is None:
                    continue
                if current_digest != new_digest:
                    errors.append(f"arquivo alterado preservado em {destination}")
                    continue
                remove_path(destination)
                continue
            if current_digest == previous_digest:
                continue
            if current_digest not in {None, new_digest}:
                errors.append(f"arquivo alterado preservado em {destination}")
                continue
            backup = token.backup_root.joinpath(*relative.parts)
            if not backup.is_file() or backup.is_symlink() or file_hash(backup) != previous_digest:
                errors.append(f"backup inválido de {destination}")
                continue
            if current_digest is not None:
                remove_path(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, destination)

        for name, previous_digest in reversed(tuple(token.stale_hashes.items())):
            relative = PurePosixPath(name)
            destination = self.target.joinpath(*relative.parts)
            if lexists(destination):
                if (
                    destination.is_file()
                    and not destination.is_symlink()
                    and file_hash(destination) == previous_digest
                ):
                    continue
                errors.append(f"caminho ocupado preservado em {destination}")
                continue
            backup = token.backup_root.joinpath(*relative.parts)
            if not backup.is_file() or backup.is_symlink() or file_hash(backup) != previous_digest:
                errors.append(f"backup inválido de {destination}")
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, destination)

        for directory, identity in reversed(tuple(token.created_directories.items())):
            try:
                metadata = directory.lstat()
            except FileNotFoundError:
                continue
            except OSError as error:
                errors.append(f"diretório não pôde ser inspecionado: {directory}: {error}")
                continue
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or (int(metadata.st_dev), int(metadata.st_ino)) != identity
            ):
                errors.append(f"diretório alterado preservado em {directory}")
                continue
            try:
                directory.rmdir()
            except OSError:
                pass
        if errors:
            raise InstallerError("Rollback do payload ficou incompleto: " + "; ".join(errors))

    def _apply_component_payload(
        self,
        component: str,
        managed: Path,
        new_entries: list[tuple[str, str]],
        original_hashes: dict[str, str | None],
        stale_hashes: dict[str, str],
    ) -> ComponentPayloadRollback:
        assert self.stage is not None
        backup_root = Path(tempfile.mkdtemp(
            prefix=f".{component}-payload-old.", dir=self.stage,
        ))
        token = ComponentPayloadRollback(
            backup_root=backup_root,
            original_hashes=dict(original_hashes),
            new_hashes=dict(new_entries),
            stale_hashes=dict(stale_hashes),
            created_directories={},
        )
        try:
            for name, digest in {**{
                name: value for name, value in original_hashes.items() if value is not None
            }, **stale_hashes}.items():
                relative = PurePosixPath(name)
                source = self.target.joinpath(*relative.parts)
                backup = backup_root.joinpath(*relative.parts)
                if not source.is_file() or source.is_symlink() or file_hash(source) != digest:
                    raise InstallerError(f"Arquivo gerenciado mudou antes do backup: {source}")
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, backup)
            for name, digest in new_entries:
                relative = PurePosixPath(name)
                source = managed.joinpath(*relative.parts)
                destination = self.target.joinpath(*relative.parts)
                missing_parents: list[Path] = []
                parent = destination.parent
                while parent != self.target and not lexists(parent):
                    missing_parents.append(parent)
                    parent = parent.parent
                destination.parent.mkdir(parents=True, exist_ok=True)
                for created in reversed(missing_parents):
                    metadata = created.lstat()
                    token.created_directories.setdefault(
                        created, (int(metadata.st_dev), int(metadata.st_ino)),
                    )
                source_mode = source.stat().st_mode
                atomic_copy_file(
                    source,
                    destination,
                    expected_sha256=digest,
                    mode=0o755 if source_mode & 0o111 else 0o644,
                )
                if file_hash(destination) != digest:
                    raise InstallerError(f"Arquivo copiado diverge do plano: {destination}")
            for name in stale_hashes:
                relative = PurePosixPath(name)
                stale = self.target.joinpath(*relative.parts)
                if not stale.is_file() or stale.is_symlink():
                    raise InstallerError(f"Caminho gerenciado inválido: {stale}")
                remove_path(stale)
            return token
        except BaseException as error:
            try:
                self._rollback_component_payload(token)
            except BaseException as rollback_error:
                raise InstallerError(
                    f"Falha ao instalar {component}; rollback incompleto: {rollback_error}"
                ) from error
            raise

    def install_component_overlay_transaction(
        self, component: str, managed: Path, selection: str, source: str,
    ) -> tuple[int, MutationResult]:
        assert self.stage is not None
        present, old_entries, _ = self.validate_component_pair(component)
        self.filter_component_conflicts(component, managed)
        inventory = self.stage / f"{component}.inventory"
        entries = self.create_inventory(managed, inventory)
        if not entries:
            raise InstallerError(f"Nenhum arquivo novo do componente {component} pôde ser instalado.")
        receipt = self.stage / f"{component}.receipt"
        self.write_component_receipt(component, selection, source, inventory, receipt)
        old = dict(old_entries) if present else {}
        original_hashes: dict[str, str | None] = {}
        for name, _ in entries:
            destination = self.target.joinpath(*PurePosixPath(name).parts)
            original_hashes[name] = file_hash(destination) if lexists(destination) else None
        new_names = {name for name, _ in entries}
        stale_hashes: dict[str, str] = {}
        for name, digest in old.items():
            if name in new_names:
                continue
            stale = self.target.joinpath(*PurePosixPath(name).parts)
            if not lexists(stale):
                continue
            if not stale.is_file() or stale.is_symlink():
                raise InstallerError(f"Caminho gerenciado inválido: {stale}")
            if file_hash(stale) == digest:
                stale_hashes[name] = digest
            else:
                console.warning(f"Arquivo modificado preservado: {stale}")
        observed_names = (*new_names, *stale_hashes)
        plan = MutationPlan(
            identifier=f"component:{component}",
            summary=f"Instalar componente {component}",
            steps=(
                MutationStep(
                    key="payload",
                    description=f"Publicar payload de {component}",
                    observe=lambda: self._component_payload_observation(
                        managed, observed_names,
                    ),
                    apply=lambda: self._apply_component_payload(
                        component, managed, entries, original_hashes, stale_hashes,
                    ),
                    rollback=self._rollback_component_payload,
                ),
                MutationStep(
                    key="metadata",
                    description=f"Publicar recibo e inventário de {component}",
                    observe=lambda: self._component_metadata_observation(component),
                    apply=lambda: self.commit_component_metadata(
                        component, inventory, receipt,
                    ),
                    rollback=self._rollback_component_metadata,
                ),
            ),
        )
        try:
            result = execute_mutation(prepare_mutation(plan))
        except MutationRollbackError:
            raise
        except MutationApplyError as error:
            if isinstance(error.operation_error, InstallerError):
                raise error.operation_error
            raise
        return len(entries), result

    def install_component_overlay(
        self, component: str, managed: Path, selection: str, source: str,
    ) -> int:
        count, _ = self.install_component_overlay_transaction(
            component, managed, selection, source,
        )
        return count

    def expected_qw_package_order(self) -> list[str]:
        directory = self.target / "qw"
        if not directory.is_dir() or directory.is_symlink():
            return []
        available = {
            path.name for path in directory.iterdir()
            if path.is_file() and not path.is_symlink() and path.suffix.casefold() == ".pk3"
        }
        known = [name for name in QW_PACKAGE_PRIORITY if name in available]
        custom = sorted(available - set(QW_PACKAGE_PRIORITY), key=str.casefold)
        return [*known, *custom]

    def _qw_package_order_is_current(self, packages: list[str]) -> bool:
        present, entries, receipt = self.validate_component_pair("package-order")
        if not present or receipt is None:
            return False
        payload = "".join(f"{name}\n" for name in packages).encode("utf-8")
        order = self.target / "qw/pak.lst"
        return (
            dict(entries) == {"qw/pak.lst": hashlib.sha256(payload).hexdigest()}
            and order.is_file()
            and not order.is_symlink()
            and order.read_bytes() == payload
            and receipt["selection"] == "1"
            and receipt["source"] == "x86QW deterministic PK3 order"
        )

    def refresh_qw_package_order(
        self, *, mutation_results: list[MutationResult] | None = None,
    ) -> tuple[MutationResult, ...]:
        packages = self.expected_qw_package_order()
        payload = "".join(f"{name}\n" for name in packages).encode("utf-8")
        created: list[MutationResult] = []
        if not packages:
            present, _, _ = self.validate_component_pair("package-order")
            if present:
                owned_stage = self.stage is None
                if owned_stage:
                    self._create_stage(".quake-order-remove.")
                cleanup = True
                try:
                    _, result = self.remove_component_transaction("package-order")
                    created.append(result)
                    if mutation_results is not None:
                        mutation_results.append(result)
                except MutationRollbackError:
                    cleanup = False
                    raise
                finally:
                    if owned_stage and mutation_results is None and cleanup:
                        self.cleanup_stage()
                        self.stage = None
            return tuple(created) if mutation_results is not None else ()
        if self._qw_package_order_is_current(packages):
            return ()
        previous_stage = self.stage
        previous_stage_identity = self._stage_identity
        previous_stage_created_roots = self._stage_created_roots
        owned_stage = previous_stage is None
        if owned_stage:
            self._create_stage(".quake-order.")
        assert self.stage is not None
        cleanup = True
        try:
            managed = self.stage / "package-order-managed"
            if lexists(managed):
                remove_path(managed, self.target.stat().st_dev)
            target = managed / "qw/pak.lst"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            count, result = self.install_component_overlay_transaction(
                "package-order", managed, "1", "x86QW deterministic PK3 order",
            )
            created.append(result)
            if mutation_results is not None:
                mutation_results.append(result)
            console.detail(
                f"Ordem determinística registrada para {len(packages)} PK3 em qw/pak.lst "
                f"({file_count(count)})."
            )
        except MutationRollbackError:
            cleanup = False
            raise
        finally:
            if owned_stage and mutation_results is None and cleanup:
                self.cleanup_stage()
                self.stage = previous_stage
                self._stage_identity = previous_stage_identity
                self._stage_created_roots = previous_stage_created_roots
        return tuple(created) if mutation_results is not None else ()

    def verify_qw_package_order(self, *, report_details: bool = True) -> None:
        packages = self.expected_qw_package_order()
        present, _, _ = self.validate_component_pair("package-order")
        if not packages:
            if present:
                raise InstallerError("pak.lst gerenciado existe sem pacotes PK3 em qw.")
            return
        if not present:
            raise InstallerError(
                "Ordem de PK3 não registrada. Reexecute o bootstrap x86QW no mesmo "
                "destino para gerar qw/pak.lst."
            )
        self.verify_component("package-order", report_details=report_details)
        path = self.target / "qw/pak.lst"
        expected = "".join(f"{name}\n" for name in packages)
        if path.read_text(encoding="utf-8") != expected:
            raise InstallerError(
                "qw/pak.lst não representa os PK3 instalados. Reexecute o bootstrap "
                "x86QW no mesmo destino."
            )

    def _component_removal_payload_observation(
        self, names: Iterable[str],
    ) -> tuple[tuple[str, tuple[object, ...]], ...]:
        return tuple(
            (
                name,
                self._mutation_path_observation(
                    self.target.joinpath(*PurePosixPath(name).parts),
                ),
            )
            for name in sorted(set(names))
        )

    def _component_removal_node_identity(self, path: Path, kind: str) -> tuple[int, int]:
        if kind == "file":
            return self._regular_identity(path)
        if kind == "directory":
            return self._directory_identity(path)
        raise InstallerError(f"Tipo de backup gerenciado inválido: {kind}")

    def _move_component_removal_node(
        self,
        original: Path,
        backup: Path,
        *,
        token: ComponentRemovalRollback,
        kind: str,
        expected_identity: tuple[int, int],
    ) -> None:
        if self._component_removal_node_identity(original, kind) != expected_identity:
            raise InstallerError(f"Caminho gerenciado mudou antes da remoção: {original}")
        backup.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        original.replace(backup)
        node = ComponentRemovalNode(original, backup, expected_identity, kind)
        token.moved.append(node)
        if self._component_removal_node_identity(backup, kind) != expected_identity:
            raise InstallerError(f"Backup gerenciado mudou durante a remoção: {backup}")

    def _rollback_component_removal(self, token: ComponentRemovalRollback) -> None:
        errors: list[str] = []
        for node in reversed(token.moved):
            try:
                if lexists(node.original):
                    raise InstallerError(
                        f"Destino reapareceu e foi preservado: {node.original}"
                    )
                if (
                    self._component_removal_node_identity(node.backup, node.kind)
                    != node.identity
                ):
                    raise InstallerError(
                        f"Backup alterado foi preservado: {node.backup}"
                    )
                node.original.parent.mkdir(parents=True, exist_ok=True)
                node.backup.replace(node.original)
                if (
                    self._component_removal_node_identity(node.original, node.kind)
                    != node.identity
                ):
                    raise InstallerError(
                        f"Caminho restaurado diverge do backup: {node.original}"
                    )
            except BaseException as error:
                errors.append(str(error))
        if errors:
            raise InstallerError(
                "Rollback da remoção ficou incompleto: " + "; ".join(errors)
            )

    def _component_removal_apply_error(
        self,
        *,
        component: str,
        step_key: str,
        operation_error: BaseException,
        rollback_error: BaseException,
    ) -> MutationRollbackError:
        return MutationRollbackError(
            f"A remoção de {component} falhou e o rollback ficou incompleto.",
            plan_identifier=f"component-remove:{component}",
            step_key=step_key,
            operation_error=operation_error,
            rollback_errors=((step_key, rollback_error),),
        )

    def _apply_component_removal_payload(
        self,
        component: str,
        entries: Iterable[tuple[str, str, tuple[int, int]]],
    ) -> ComponentRemovalRollback:
        assert self.stage is not None
        backup_root = private_fs.private_mkdtemp(
            directory=self.stage, prefix=f".{component}-remove-payload.",
        )
        token = ComponentRemovalRollback(backup_root, [])
        try:
            for name, digest, identity in entries:
                original = self.target.joinpath(*PurePosixPath(name).parts)
                if file_hash(original) != digest:
                    raise InstallerError(
                        f"Arquivo gerenciado mudou antes da remoção: {original}"
                    )
                backup = backup_root.joinpath(*PurePosixPath(name).parts)
                self._move_component_removal_node(
                    original,
                    backup,
                    token=token,
                    kind="file",
                    expected_identity=identity,
                )
            return token
        except BaseException as error:
            try:
                self._rollback_component_removal(token)
            except BaseException as rollback_error:
                raise self._component_removal_apply_error(
                    component=component,
                    step_key="payload",
                    operation_error=error,
                    rollback_error=rollback_error,
                ) from error
            raise

    def _component_removal_metadata_nodes(
        self, component: str,
    ) -> tuple[tuple[Path, str, str, tuple[int, int]], ...]:
        metadata = self.target / METADATA_DIR
        canonical = self.component_pair_paths(component, metadata)
        nodes: list[tuple[Path, str, str, tuple[int, int]]] = []
        if lexists(canonical[0].parent):
            nodes.append((
                canonical[0].parent,
                "canonical",
                "directory",
                self._directory_identity(canonical[0].parent),
            ))
        for index, legacy in enumerate(
            self.component_pair_paths(component, metadata, legacy=True), 1,
        ):
            if lexists(legacy):
                nodes.append((
                    legacy,
                    f"legacy-{index}",
                    "file",
                    self._regular_identity(legacy),
                ))
        return tuple(nodes)

    def _apply_component_removal_metadata(
        self,
        component: str,
        expected_observation: tuple[tuple[str, tuple[object, ...]], ...],
        expected_nodes: tuple[tuple[Path, str, str, tuple[int, int]], ...],
    ) -> ComponentRemovalRollback:
        assert self.stage is not None
        backup_root = private_fs.private_mkdtemp(
            directory=self.stage, prefix=f".{component}-remove-metadata.",
        )
        token = ComponentRemovalRollback(backup_root, [])
        try:
            if self._component_metadata_observation(component) != expected_observation:
                raise InstallerError(
                    f"Os metadados de {component} mudaram antes da remoção."
                )
            for original, backup_name, kind, identity in expected_nodes:
                self._move_component_removal_node(
                    original,
                    backup_root / backup_name,
                    token=token,
                    kind=kind,
                    expected_identity=identity,
                )
            return token
        except BaseException as error:
            try:
                self._rollback_component_removal(token)
            except BaseException as rollback_error:
                raise self._component_removal_apply_error(
                    component=component,
                    step_key="metadata",
                    operation_error=error,
                    rollback_error=rollback_error,
                ) from error
            raise

    def remove_component_transaction(
        self, component: str,
    ) -> tuple[int, MutationResult]:
        present, entries, _ = self.validate_component_pair(component)
        if not present:
            plan = MutationPlan(
                identifier=f"component-remove:{component}",
                summary=f"Remover componente ausente {component}",
                steps=(MutationStep(
                    key="absent",
                    description=f"Confirmar ausência de {component}",
                    observe=lambda: self._component_metadata_observation(component),
                    apply=lambda: None,
                    rollback=lambda _token: None,
                ),),
            )
            return 0, execute_mutation(prepare_mutation(plan))
        unchanged: list[tuple[str, str, tuple[int, int]]] = []
        for name, digest in entries:
            managed = self.target.joinpath(*PurePosixPath(name).parts)
            if not lexists(managed):
                continue
            if not managed.is_file() or managed.is_symlink():
                raise InstallerError(f"Caminho gerenciado inválido: {managed}")
            if file_hash(managed) == digest:
                unchanged.append((name, digest, self._regular_identity(managed)))
            else:
                console.warning(f"Arquivo modificado preservado: {managed}")
        metadata = self.target / METADATA_DIR
        metadata_paths = [
            self.metadata_path(metadata, relative)
            for relative in (
                *self.component_metadata(component),
                *self.legacy_component_metadata(component),
            )
        ]
        expected_metadata_observation = tuple(
            (str(path), self._mutation_path_observation(path))
            for path in metadata_paths
        )
        expected_metadata_nodes = self._component_removal_metadata_nodes(component)
        plan = MutationPlan(
            identifier=f"component-remove:{component}",
            summary=f"Remover componente {component}",
            steps=(
                MutationStep(
                    key="payload",
                    description=f"Retirar payload gerenciado de {component}",
                    observe=lambda: self._component_removal_payload_observation(
                        name for name, _digest, _identity in unchanged
                    ),
                    apply=lambda: self._apply_component_removal_payload(
                        component, unchanged,
                    ),
                    rollback=self._rollback_component_removal,
                ),
                MutationStep(
                    key="metadata",
                    description=f"Retirar recibo e inventário de {component}",
                    observe=lambda: self._component_metadata_observation(component),
                    apply=lambda: self._apply_component_removal_metadata(
                        component,
                        expected_metadata_observation,
                        expected_metadata_nodes,
                    ),
                    rollback=self._rollback_component_removal,
                ),
            ),
        )
        try:
            result = execute_mutation(prepare_mutation(plan))
        except MutationRollbackError:
            raise
        except MutationApplyError as error:
            if isinstance(error.operation_error, MutationRollbackError):
                raise error.operation_error
            if isinstance(error.operation_error, InstallerError):
                raise error.operation_error
            raise
        return len(unchanged), result

    def remove_component(self, component: str) -> int:
        present, _, _ = self.validate_component_pair(component)
        if not present:
            return 0
        owned_stage = self.stage is None
        if owned_stage:
            self._create_stage(f".{component}-remove.")
        cleanup = True
        try:
            removed, _ = self.remove_component_transaction(component)
            return removed
        except MutationRollbackError:
            cleanup = False
            raise
        finally:
            if owned_stage and cleanup:
                self.cleanup_stage()
                self.stage = None

    def verify_component(self, component: str, *, report_details: bool = True) -> int:
        present, entries, receipt = self.validate_component_pair(component)
        if not present:
            return 0
        for name, expected in entries:
            managed = self.target.joinpath(*PurePosixPath(name).parts)
            if not managed.is_file() or managed.is_symlink():
                raise InstallerError(f"Arquivo gerenciado ausente do componente {component}: {managed}")
            suffix = managed.suffix.casefold()
            if suffix == ".pk3":
                try:
                    archive_plan = scan_archive(managed)
                except ArchiveError as error:
                    raise InstallerError(f"PK3 gerenciado inválido: {managed}") from error
                actual_hash = archive_plan.source_sha256
            else:
                actual_hash = file_hash(managed)
            if actual_hash != expected:
                raise InstallerError(f"Arquivo gerenciado foi alterado no componente {component}: {managed}")
            if suffix == ".bsp":
                with managed.open("rb") as source:
                    header = source.read(4)
                if len(header) != 4 or struct.unpack("<I", header)[0] != 29:
                    raise InstallerError(f"BSP gerenciado inválido: {managed}")
            if suffix == ".pak":
                with managed.open("rb") as source:
                    if source.read(4) != b"PACK":
                        raise InstallerError(f"PAK gerenciado inválido: {managed}")
        assert receipt is not None
        if report_details:
            console.success(
                f"Componente {component} íntegro "
                f"({file_count(len(entries))}; seleção {receipt['selection']})."
            )
        return len(entries)

    def manage_presets(
        self, *, mutation_results: list[MutationResult] | None = None,
    ) -> None:
        action = navigation.select_one(
            "O que deseja fazer com os presets?",
            (
                navigation.MenuOption("install", "Instalar ou atualizar", "preserva configurações pessoais"),
                navigation.MenuOption("remove", "Remover presets gerenciados", "não remove sua configuração"),
            ),
            breadcrumb="x86QW › Gerenciar instalação › Presets",
        )
        if action is None:
            raise InstallerError("Nenhuma operação de presets foi selecionada.")
        with self.component_state_transaction(mutation_results) as results:
            if self.stage is None:
                self._create_stage(
                    ".quake-presets-remove." if action == "remove" else ".quake-install."
                )
            if action == "remove":
                removed, result = self.remove_component_transaction("presets")
                results.append(result)
            else:
                self.check_paks()
                assert self.stage is not None
                managed = self.stage / "presets-managed"
                configs = managed / "ezquake/configs"
                configs.mkdir(parents=True)
                for name, contents in PRESETS.items():
                    (configs / name).write_text(contents, encoding="utf-8")
                count, result = self.install_component_overlay_transaction(
                    "presets", managed, "v1", "x86-qw built-in presets",
                )
                results.append(result)
        if action == "remove":
            console.success(
                f"Presets gerenciados removidos ({file_count(removed)}); "
                "configurações pessoais preservadas."
            )
        else:
            console.success(
                f"Presets instalados ({file_count(count)}). "
                "Carregue um deles com cfg_load x86-qw-modern."
            )

    def validate_nquake_receipt(self, path: Path) -> dict[str, str]:
        try:
            return parse_legacy_nquake_receipt(
                read_bounded_regular_file(path, maximum_size=MAX_RECEIPT_BYTES)
            ).to_legacy_dict()
        except MetadataFileError as error:
            raise InstallerError(f"Recibo histórico da instalação inválido: {path}") from error
        except ReceiptError as error:
            if error.code == "legacy_nquake_format":
                message = f"unsupported receipt format: {error.value}"
            elif error.code == "legacy_nquake_revision":
                message = "invalid distfiles commit in receipt"
            elif error.code == "legacy_nquake_inventory_hash":
                message = "invalid inventory SHA-256 in receipt"
            else:
                message = f"invalid installation receipt: {path}"
            raise InstallerError(message) from error

    def validate_nquake_pair(self, metadata: Path | None = None) -> tuple[bool, list[tuple[str, str]], dict[str, str] | None]:
        metadata = metadata or self.target / METADATA_DIR
        inventory = metadata / Path(NQUAKE_INVENTORY).name
        receipt_path = metadata / Path(NQUAKE_RECEIPT).name
        inventory_exists, receipt_exists = lexists(inventory), lexists(receipt_path)
        if not inventory_exists and not receipt_exists:
            return False, [], None
        if not inventory_exists or not receipt_exists:
            raise InstallerError("incomplete nQuake installation metadata")
        entries = self.validate_inventory(inventory)
        receipt = self.validate_nquake_receipt(receipt_path)
        if file_hash(inventory) != receipt["inventory_sha256"]:
            raise InstallerError("managed inventory differs from installation receipt")
        return True, entries, receipt

    def installed_components(self) -> list[str]:
        installed = []
        for identifier in self.components:
            present, _, _ = self.validate_component_pair(identifier)
            if present:
                installed.append(identifier)
        return installed

    def installed_legacy_component_replacements(self) -> dict[str, str]:
        installed: dict[str, str] = {}
        for legacy, replacement in LEGACY_COMPONENT_REPLACEMENTS.items():
            present, _, _ = self.validate_component_pair(legacy)
            if present:
                installed[legacy] = replacement
        return installed

    def installed_legacy_component_removals(self) -> list[str]:
        return [
            identifier
            for identifier in LEGACY_COMPONENT_REMOVALS
            if self.validate_component_pair(identifier)[0]
        ]

    @staticmethod
    def replace_legacy_component_ids(values: list[str]) -> list[str]:
        replaced: list[str] = []
        for identifier in values:
            if identifier in LEGACY_COMPONENT_REMOVALS:
                continue
            current = LEGACY_COMPONENT_REPLACEMENTS.get(identifier, identifier)
            if current not in replaced:
                replaced.append(current)
        return replaced

    def current_install_state(self, state: dict[str, object]) -> dict[str, object]:
        parsed = self._parse_install_state(state)
        return migrate_install_state(
            parsed,
            replacements=LEGACY_COMPONENT_REPLACEMENTS,
            removals=LEGACY_COMPONENT_REMOVALS,
            allowed_profiles=self._install_state_profiles(),
            allowed_capabilities=INSTALLATION_CAPABILITIES,
        ).to_document()

    def _install_state_profiles(self) -> frozenset[str]:
        return INSTALLATION_PROFILES

    @staticmethod
    def _install_state_error(error: StateError, path: Path) -> InstallerError:
        if error.code == "component_field" and error.field_name is not None:
            return InstallerError(
                f"Campo {error.field_name} inválido no estado da instalação: {path}"
            )
        if error.code == "custom_requested":
            return InstallerError(
                f"Somente o perfil custom pode registrar escolhas explícitas: {path}"
            )
        if error.code == "capabilities":
            return InstallerError(
                f"Capacidades ou fingerprint inválidos no estado da instalação: {path}"
            )
        return InstallerError(f"Estado da instalação inválido: {path}")

    def _parse_install_state(self, state: object):
        path = self.target / INSTALL_STATE
        try:
            if isinstance(state, Mapping):
                validate_document_versions(
                    state,
                    kind=SchemaKind.STATE,
                    current_cli_version=application_version(),
                    allow_legacy=True,
                )
            return parse_install_state(
                state,
                allowed_profiles=self._install_state_profiles(),
                allowed_capabilities=INSTALLATION_CAPABILITIES,
            )
        except ContractError as error:
            raise InstallerError(
                f"Estado da instalação incompatível com a CLI atual: {path}",
                exit_code=ExitCode.USAGE,
            ) from error
        except StateError as error:
            raise self._install_state_error(error, path) from error

    def validate_install_state(self, state: object) -> dict[str, object]:
        return self._parse_install_state(state).to_document()

    def write_install_state(
        self,
        profile: str,
        requested: list[str],
        *,
        known: list[str] | None = None,
        capabilities: list[str] | None = None,
        mutation_results: list[MutationResult] | None = None,
    ) -> dict[str, object]:
        self.ensure_metadata_directory()
        recorded = self.installed_components()
        state_document = add_contract_versions({
            "format": 2,
            "project": "x86qw",
            "profile": profile,
            "requested_components": list(requested),
            "recorded_components": recorded,
            "known_components": list(self.components) if known is None else list(known),
            "capabilities": [] if capabilities is None else list(capabilities),
            "component_fingerprint": profile_fingerprint(recorded),
        }, ContractVersions(), kind=SchemaKind.STATE)
        state = self.validate_install_state(state_document)
        destination = self.target / INSTALL_STATE
        payload = serialize_install_state(self._parse_install_state(state))
        if mutation_results is not None:
            if lexists(destination) and (
                not destination.is_file() or destination.is_symlink()
            ):
                raise InstallerError(f"Estado da instalação inválido: {destination}")
            if self.stage is None:
                self._create_stage(".x86qw-state.")
            assert self.stage is not None
            staged = self.stage / f"state-{len(mutation_results)}.json"
            try:
                atomic_write_bytes(staged, payload)
            except AtomicWriteError as error:
                raise PersistenceError(
                    f"Estado preparado não pôde ser gravado: {staged}",
                    committed=False,
                ) from error
            plan = MutationPlan(
                identifier=f"install-state:{profile}",
                summary="Publicar o estado coerente da instalação",
                steps=(MutationStep(
                    key="state",
                    description="Publicar state.json",
                    observe=lambda: (
                        self._mutation_path_observation(staged),
                        self._mutation_path_observation(destination),
                    ),
                    apply=lambda: self._apply_runtime_payload(staged, destination),
                    rollback=self._rollback_runtime_payload,
                ),),
            )
            try:
                result = execute_mutation(prepare_mutation(plan))
                if self.read_install_state_document(destination) != state:
                    raise InstallerError(
                        "O estado publicado diverge do plano da instalação."
                    )
            except MutationApplyError as error:
                if isinstance(error.operation_error, InstallerError):
                    raise error.operation_error
                raise PersistenceError(
                    f"Estado da instalação não pôde ser publicado: {destination}",
                    committed=False,
                ) from error
            except BaseException as error:
                if "result" in locals():
                    try:
                        rollback_mutation(result)
                    except BaseException as rollback_error:
                        raise InstallerError(
                            "A validação do estado falhou e o rollback ficou incompleto: "
                            f"{rollback_error}"
                        ) from error
                raise
            mutation_results.append(result)
            return state
        try:
            atomic_write_bytes(destination, payload)
        except AtomicWriteError as error:
            raise PersistenceError(
                f"Estado da instalação não pôde ser gravado de forma atômica: {destination}",
                committed=error.committed,
            ) from error
        return state

    def infer_install_state(self) -> dict[str, object]:
        installed = self.installed_components()
        for replacement in self.installed_legacy_component_replacements().values():
            if replacement not in installed:
                installed.append(replacement)
        profile = "custom"
        requested = list(installed)
        if not installed:
            profile = "none"
            requested = []
        else:
            fingerprint = profile_fingerprint(installed)
            for candidate in ("essential", "recommended", "complete"):
                if fingerprint in self.component_catalog["profile_history"][candidate]:
                    profile = candidate
                    requested = []
                    break
        return self.validate_install_state({
            "format": 2,
            "project": "x86qw",
            "profile": profile,
            "requested_components": requested,
            "recorded_components": installed,
            "known_components": list(self.components),
            "capabilities": [],
            "component_fingerprint": profile_fingerprint(installed),
        })

    def migrate_stale_custom_profile(self, state: dict[str, object]) -> dict[str, object]:
        if state["profile"] != "custom":
            return state
        recorded = list(state["recorded_components"])
        installed = self.installed_components()
        if set(installed) != set(recorded):
            return state
        if set(self.desired_components(state)) == set(recorded):
            return state
        fingerprint = profile_fingerprint(recorded)
        for candidate in ("essential", "recommended", "complete"):
            if fingerprint in self.component_catalog["profile_history"][candidate]:
                migrated = dict(state)
                migrated["profile"] = candidate
                migrated["requested_components"] = []
                return self.validate_install_state(migrated)
        return state

    def load_install_state(self) -> dict[str, object]:
        path = self.target / INSTALL_STATE
        if path.is_file() and not path.is_symlink():
            state = self.read_install_state_document(path)
            original = state
            state = self.current_install_state(state)
            migrated = self.migrate_stale_custom_profile(state)
            if migrated != original:
                profile = str(migrated["profile"])
                console.info(
                    "Estado histórico interpretado no formato 2; a próxima operação "
                    f"mutável persistirá o perfil {profile}."
                )
            return migrated
        if lexists(path):
            raise InstallerError(f"Estado da instalação inválido: {path}")
        state = self.infer_install_state()
        console.info(f"Perfil inferido sem alterar a instalação: {state['profile']}.")
        return state

    def read_install_state_document(self, path: Path) -> dict[str, object]:
        try:
            document = read_install_state(
                path,
                allowed_profiles=self._install_state_profiles(),
                allowed_capabilities=INSTALLATION_CAPABILITIES,
            ).to_document()
            validate_document_versions(
                document,
                kind=SchemaKind.STATE,
                current_cli_version=application_version(),
                allow_legacy=True,
            )
            return document
        except ContractError as error:
            raise InstallerError(
                f"Estado da instalação incompatível com a CLI atual: {path}",
                exit_code=ExitCode.USAGE,
            ) from error
        except StateError as error:
            raise self._install_state_error(error, path) from error

    def desired_components(self, state: dict[str, object]) -> list[str]:
        profile = str(state["profile"])
        if profile == "none":
            return []
        requested = (
            list(state["requested_components"])
            if profile == "custom"
            else list(self.component_catalog["profiles"][profile])
        )
        unknown = [identifier for identifier in requested if identifier not in self.components]
        if unknown:
            raise InstallerError(
                "O perfil da instalação referencia componentes indisponíveis: " + ", ".join(unknown)
            )
        try:
            return resolve_dependencies(self.component_catalog, requested)
        except ValueError as error:
            raise InstallerError(str(error)) from error

    def show_components(self) -> None:
        installed = set(self.installed_components())
        print("\nComponentes x86QW disponíveis:")
        for index, component in enumerate(self.components.values(), 1):
            identifier = str(component["id"])
            package = self.component_package_record(identifier)
            current = str(package["version"])
            status = ""
            if identifier in installed:
                _, _, receipt = self.validate_component_pair(identifier)
                assert receipt is not None
                status = " · instalado" if receipt["selection"] == current else f" · atualizar {receipt['selection']} → {current}"
            print(f"  {index:2d}) {component['label']} · {current}{status}")
            console.detail(f"{component['id']}: {component['description']}")
            if package.get("release_url"):
                console.detail(f"Novidades: {safe_url_for_log(package['release_url'])}")

    def choose_components(self) -> list[str]:
        if self._non_interactive_install:
            profile = self._requested_profile
            if profile not in {"essential", "recommended", "complete"}:
                raise InstallerError(
                    "Instalação não interativa exige --profile essential, recommended ou complete."
                )
        else:
            profile = navigation.select_one(
                "Qual conjunto de componentes deseja preparar?",
                (
                    navigation.MenuOption(
                        "recommended", "Recomendado", "experiência nQuake sem addons grandes",
                    ),
                    navigation.MenuOption(
                        "essential", "Essencial", "configuração, interface principal e KTX",
                    ),
                    navigation.MenuOption(
                        "complete", "Completo", f"todos os {len(self.components)} componentes atuais",
                    ),
                    navigation.MenuOption(
                        "custom", "Personalizado", "escolha componentes individualmente",
                    ),
                ),
                breadcrumb="x86QW › Instalação › Perfil",
            )
        if profile is None:
            raise InstallerError("Nenhum perfil foi selecionado.")
        return self._resolve_component_selection(profile)

    def _resolve_component_selection(self, profile: str) -> list[str]:
        if profile != "custom":
            selected = list(self.component_catalog["profiles"][profile])
            requested: list[str] = []
        else:
            try:
                chosen = navigation.select_many(
                    "Quais componentes deseja instalar?",
                    (
                        navigation.MenuOption(
                            identifier,
                            str(component["label"]),
                            str(self.component_package_record(identifier)["version"]),
                            str(component["description"]),
                        )
                        for identifier, component in self.components.items()
                    ),
                    breadcrumb="x86QW › Instalação › Perfil personalizado",
                    subtitle="Marque com Espaço e pressione Enter para concluir.",
                    searchable=True,
                )
            except (EOFError, navigation.MenuCancelled, ValueError) as error:
                raise InstallerError("Nenhum componente x86QW foi selecionado.") from error
            selected = list(chosen or ())
            if not selected:
                raise InstallerError("Nenhum componente x86QW foi selecionado.")
            requested = list(selected)
        try:
            resolved = resolve_dependencies(self.component_catalog, selected)
        except ValueError as error:
            raise InstallerError(str(error)) from error
        if profile == "custom":
            added = [identifier for identifier in resolved if identifier not in selected]
            if added:
                console.info("Dependências adicionadas automaticamente: " + ", ".join(added))
        selected = resolved
        self.selected_component_profile = profile
        self.requested_components = requested
        noun = "componente selecionado" if len(selected) == 1 else "componentes selecionados"
        console.detail(f"{len(selected)} {noun}.")
        console.detail("Versões que serão instaladas ou atualizadas:")
        for identifier in selected:
            package = self.component_package_record(identifier)
            console.detail(
                f"- {self.components[identifier]['label']}: {package['version']}"
            )
            if package.get("release_url"):
                console.detail(
                    f"  novidades: {safe_url_for_log(package['release_url'])}"
                )
        return selected

    def select_components_profile(self, profile: str) -> list[str]:
        if profile not in {"recommended", "essential", "complete"}:
            raise InstallerError(f"Perfil nativo inválido: {profile}")
        try:
            selected = resolve_dependencies(
                self.component_catalog,
                list(self.component_catalog["profiles"][profile]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise InstallerError(f"Perfil nativo inválido: {profile}") from error
        self.selected_component_profile = profile
        self.requested_components = []
        noun = "componente selecionado" if len(selected) == 1 else "componentes selecionados"
        console.detail(f"{len(selected)} {noun}.")
        console.detail("Versões que serão instaladas ou atualizadas:")
        for identifier in selected:
            package = self.component_package_record(identifier)
            console.detail(
                f"- {self.components[identifier]['label']}: {package['version']}"
            )
            if package.get("release_url"):
                console.detail(
                    f"  novidades: {safe_url_for_log(package['release_url'])}"
                )
        return selected

    def _native_candidate_root(self) -> Path | None:
        value = os.environ.get(NATIVE_CANDIDATE_ROOT_ENV)
        if value is None:
            return None
        root = Path(value).absolute()
        if root.is_symlink() or not root.is_dir():
            raise InstallerError("O candidato nativo é ausente ou inseguro.")
        return root

    def _native_candidate_manifest(self) -> dict[str, object]:
        if self._native_candidate_manifest_data is not None:
            return self._native_candidate_manifest_data
        root = self._native_candidate_root()
        if root is None:
            raise InstallerError("O candidato nativo não foi informado.")
        path = root / "candidate.json"
        try:
            document = json.loads(
                read_bounded_regular_file(path, maximum_size=CATALOG_MAX_BYTES),
            )
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as error:
            raise InstallerError("O manifest do candidato nativo é inválido.") from error
        if (
            not isinstance(document, dict)
            or document.get("project") != "x86qw"
            or not isinstance(document.get("artifacts"), dict)
        ):
            raise InstallerError("O manifest do candidato nativo é inválido.")
        self._native_candidate_manifest_data = document
        return document

    def _native_candidate_artifact(self, relative: str) -> tuple[Path, int, str]:
        root = self._native_candidate_root()
        if root is None:
            raise InstallerError("O candidato nativo não foi informado.")
        relative_path = PurePosixPath(relative)
        if (
            relative_path.is_absolute()
            or "\\" in relative
            or any(part in {"", ".", ".."} for part in relative_path.parts)
        ):
            raise InstallerError("O caminho do artifact nativo é inseguro.")
        artifacts = self._native_candidate_manifest()["artifacts"]
        identity = artifacts.get(relative) if isinstance(artifacts, dict) else None
        if (
            not isinstance(identity, dict)
            or type(identity.get("size")) is not int
            or identity["size"] <= 0
            or not isinstance(identity.get("sha256"), str)
            or HEX64.fullmatch(identity["sha256"]) is None
        ):
            raise InstallerError(f"Identidade ausente do artifact nativo: {relative}")
        path = root.joinpath(*relative_path.parts)
        current = path
        while current != root:
            if current.is_symlink():
                raise InstallerError(f"Artifact nativo usa symlink: {relative}")
            current = current.parent
        if not path.is_file() or path.is_symlink():
            raise InstallerError(f"Artifact nativo ausente: {relative}")
        expected_size = int(identity["size"])
        expected_sha256 = str(identity["sha256"])
        if (
            path.stat().st_size != expected_size
            or file_hash(
                path,
                expected_size=expected_size,
                maximum_size=MAX_ARTIFACT_BYTES,
            ) != expected_sha256
        ):
            raise InstallerError(f"Bytes do artifact nativo divergem: {relative}")
        return path, expected_size, expected_sha256

    def _native_candidate_matching(
        self, predicate: Callable[[str], bool], label: str,
    ) -> tuple[str, Path, int, str]:
        artifacts = self._native_candidate_manifest()["artifacts"]
        assert isinstance(artifacts, dict)
        matches = [str(name) for name in artifacts if isinstance(name, str) and predicate(name)]
        if len(matches) != 1:
            raise InstallerError(
                f"Artifact nativo ausente ou ambíguo ({label}): {len(matches)} encontrados."
            )
        path, size, digest = self._native_candidate_artifact(matches[0])
        return matches[0], path, size, digest

    def _native_component_package(self, identifier: str) -> dict[str, object]:
        manifest_name, manifest_path, _, _ = self._native_candidate_matching(
            lambda name: (
                name.startswith("content/components-") and name.endswith("/manifest.json")
            ),
            "manifesto de componentes",
        )
        try:
            manifest = json.loads(
                read_bounded_regular_file(manifest_path, maximum_size=CATALOG_MAX_BYTES),
            )
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as error:
            raise InstallerError(f"Manifesto de componentes nativo inválido: {manifest_name}") from error
        packages = manifest.get("packages") if isinstance(manifest, dict) else None
        if not isinstance(packages, list):
            raise InstallerError("Manifesto de componentes nativo sem packages.")
        matches = [
            package for package in packages
            if isinstance(package, dict)
            and package.get("package") == identifier
            and package.get("channel") == "content"
            and package.get("platform") == "any"
            and package.get("architecture") == "any"
        ]
        if len(matches) != 1:
            raise InstallerError(
                f"O candidato nativo deve publicar exatamente um pacote para {identifier}."
            )
        package = dict(matches[0])
        filename = package.get("filename")
        if not isinstance(filename, str):
            raise InstallerError(f"Nome ausente do pacote nativo {identifier}.")
        relative = PurePosixPath(manifest_name).parent / filename
        package[NATIVE_CANDIDATE_ARTIFACT_KEY] = relative.as_posix()
        self._native_candidate_artifact(package[NATIVE_CANDIDATE_ARTIFACT_KEY])
        return package

    def component_package_record(self, identifier: str) -> dict[str, object]:
        native_root = self._native_candidate_root()
        catalog = (
            {"packages": [self._native_component_package(identifier)]}
            if native_root is not None
            else self.public_catalog("Consultando o catálogo atual de componentes x86QW...")
        )
        packages = catalog["packages"]
        matches = [package for package in packages if isinstance(package, dict) and (
            package.get("package"), package.get("channel"),
            package.get("platform"), package.get("architecture"),
        ) == (identifier, "content", "any", "any")
            and package.get("component") in self.content_component_namespaces]
        if len(matches) != 1:
            raise InstallerError(f"O catálogo deve publicar exatamente um pacote atual para {identifier}.")
        package = matches[0]
        version = package.get("version")
        source_revision = package.get("source_revision", package.get("source_commit"))
        filename = package.get("filename")
        if not isinstance(version, str) or not COMPONENT_VERSION.fullmatch(version):
            raise InstallerError(f"Versão inválida do componente {identifier}.")
        if not isinstance(source_revision, str):
            raise InstallerError(f"Revisão de origem ausente do componente {identifier}.")
        revision_pattern = HEX40 if len(source_revision) == 40 else HEX64
        validate_hex(source_revision, revision_pattern, f"revisão de origem de {identifier}")
        if filename != f"{identifier}-{version}.zip":
            raise InstallerError(f"Identidade inconsistente do pacote {identifier}.")
        digest = package.get("sha256")
        if not isinstance(digest, str):
            raise InstallerError(f"SHA-256 ausente do componente {identifier}.")
        validate_hex(digest, HEX64, f"SHA-256 de {identifier}")
        if not isinstance(package.get("size"), int) or package["size"] <= 0:
            raise InstallerError(f"Tamanho inválido do componente {identifier}.")
        distribution_path = package.get("distribution_path", "")
        if distribution_path:
            self.validate_distribution_path(distribution_path, filename)
        urls = package.get("urls")
        if not isinstance(urls, list) or not urls or not all(isinstance(url, str) for url in urls):
            raise InstallerError(f"Mirrors inválidos do componente {identifier}.")
        for url in urls:
            if https_url_filename(url, f"mirror de {identifier}") != filename:
                raise InstallerError(f"Nome inesperado em um mirror de {identifier}.")
        if package.get("redistribution_reviewed") is not True:
            raise InstallerError(f"O pacote {identifier} ainda não foi liberado pelo x86QW.")
        if release_url := package.get("release_url"):
            validate_https_url(release_url, f"notas de versão de {identifier}")
        if "release_notes" in package and not isinstance(package["release_notes"], str):
            raise InstallerError(f"Resumo da versão inválido do componente {identifier}.")
        return package

    def core_id1_package_record(self) -> dict[str, object]:
        native_root = self._native_candidate_root()
        if native_root is not None:
            name, artifact, size, digest = self._native_candidate_matching(
                lambda value: (
                    value.startswith("content/core-")
                    and value.endswith("/x86qw-core-id1-0.1.0.zip")
                ),
                "pacote de dados base",
            )
            try:
                plan = scan_archive(artifact, required_members=("_x86qw/component.json",))
                metadata = json.loads(read_archive_member(plan, "_x86qw/component.json"))
            except (ArchiveError, OSError, KeyError, UnicodeError, json.JSONDecodeError, TypeError) as error:
                raise InstallerError(f"Metadados inválidos do pacote de dados base nativo: {name}") from error
            if not isinstance(metadata, dict):
                raise InstallerError("Metadados inválidos do pacote de dados base nativo.")
            return {
                "component": "core",
                "package": CORE_ID1_PACKAGE,
                "channel": "content",
                "platform": "any",
                "architecture": "any",
                "version": metadata.get("version"),
                "filename": Path(name).name,
                "source_revision": metadata.get("source_revision"),
                "sha256": digest,
                "size": size,
                "urls": [f"https://candidate.invalid/{Path(name).name}"],
                "redistribution_reviewed": True,
                NATIVE_CANDIDATE_ARTIFACT_KEY: name,
            }
        catalog = self.public_catalog("Consultando o pacote de dados base x86QW...")
        matches = [package for package in catalog["packages"] if isinstance(package, dict) and (
            package.get("component"), package.get("package"), package.get("channel"),
            package.get("platform"), package.get("architecture"),
        ) == ("core", CORE_ID1_PACKAGE, "content", "any", "any")]
        if len(matches) != 1:
            raise InstallerError("O catálogo deve publicar exatamente um pacote de dados base.")
        package = matches[0]
        version = package.get("version")
        filename = package.get("filename")
        if not isinstance(version, str) or not COMPONENT_VERSION.fullmatch(version):
            raise InstallerError("Versão inválida do pacote de dados base.")
        if filename != f"{CORE_ID1_PACKAGE}-{version}.zip":
            raise InstallerError("Identidade inconsistente do pacote de dados base.")
        source_revision = package.get("source_revision")
        if not isinstance(source_revision, str):
            raise InstallerError("Revisão de origem ausente do pacote de dados base.")
        validate_hex(source_revision, HEX64, "revisão do pacote de dados base")
        digest = package.get("sha256")
        if not isinstance(digest, str):
            raise InstallerError("SHA-256 ausente do pacote de dados base.")
        validate_hex(digest, HEX64, "SHA-256 do pacote de dados base")
        if not isinstance(package.get("size"), int) or package["size"] <= 0:
            raise InstallerError("Tamanho inválido do pacote de dados base.")
        urls = package.get("urls")
        if not isinstance(urls, list) or not urls or not all(isinstance(url, str) for url in urls):
            raise InstallerError("Mirrors inválidos do pacote de dados base.")
        for url in urls:
            if https_url_filename(url, "mirror do pacote de dados base") != filename:
                raise InstallerError("Nome inesperado em um mirror do pacote de dados base.")
        if package.get("redistribution_reviewed") is not True:
            raise InstallerError("O pacote de dados base ainda não foi liberado pelo x86QW.")
        return package

    def installer_bundle_record(self) -> dict[str, object]:
        catalog = self.public_catalog("Consultando atualizações do x86QW...")
        matches = [package for package in catalog["packages"] if isinstance(package, dict) and (
            package.get("component"), package.get("package"), package.get("channel"),
            package.get("platform"), package.get("architecture"),
        ) == ("installer", "x86qw-installer", "content", "any", "any")
            and package.get("current") is True]
        if len(matches) != 1:
            raise InstallerError("O catálogo deve publicar exatamente um bundle atual do x86QW.")
        package = matches[0]
        version = package.get("version")
        filename = package.get("filename")
        if not isinstance(version, str) or not SEMVER_VERSION.fullmatch(version):
            raise InstallerError("Versão inválida do bundle x86QW.")
        if filename != f"x86qw-installer-{version}.zip":
            raise InstallerError("Identidade inconsistente do bundle x86QW.")
        digest = package.get("sha256")
        if not isinstance(digest, str):
            raise InstallerError("SHA-256 ausente do bundle x86QW.")
        validate_hex(digest, HEX64, "SHA-256 do bundle x86QW")
        if not isinstance(package.get("size"), int) or package["size"] <= 0:
            raise InstallerError("Tamanho inválido do bundle x86QW.")
        urls = package.get("urls")
        if not isinstance(urls, list) or not urls or not all(isinstance(url, str) for url in urls):
            raise InstallerError("Mirrors inválidos do bundle x86QW.")
        for url in urls:
            if https_url_filename(url, "mirror do bundle x86QW") != filename:
                raise InstallerError("Nome inesperado em um mirror do bundle x86QW.")
        if package.get("redistribution_reviewed") is not True:
            raise InstallerError("O bundle x86QW ainda não foi liberado para atualização.")
        return package

    def validate_cli_receipt(self, receipt: Path) -> dict[str, object]:
        try:
            metadata = parse_cli_receipt(
                read_bounded_regular_file(
                    receipt, maximum_size=MAX_RECEIPT_BYTES,
                )
            ).to_legacy_dict()
        except MetadataFileError as error:
            raise InstallerError(f"Recibo da CLI x86QW inválido: {receipt}") from error
        except ReceiptError as error:
            if error.code == "cli_version":
                raise InstallerError(
                    f"Versão inválida no recibo da CLI x86QW: {error.value}"
                ) from error
            raise InstallerError(f"Recibo da CLI x86QW inválido: {receipt}") from error
        return metadata

    def write_cli_receipt_record(
        self, receipt: Path, metadata: dict[str, object],
    ) -> None:
        try:
            metadata = add_contract_versions(
                dict(metadata), ContractVersions(), kind=SchemaKind.RECEIPT,
            )
            model = parse_cli_receipt(
                json.dumps(metadata, ensure_ascii=False).encode("utf-8")
            )
            atomic_write_bytes(receipt, serialize_cli_receipt(model))
        except (AtomicWriteError, ReceiptError) as error:
            raise InstallerError(
                f"Recibo da CLI x86QW não pôde ser gravado: {receipt}"
            ) from error
        self.validate_cli_receipt(receipt)

    def cli_receipt_path(self) -> Path | None:
        canonical = self.target / CLI_RECEIPT
        legacy = self.target / LEGACY_CLI_RECEIPT
        canonical_exists, legacy_exists = lexists(canonical), lexists(legacy)
        if canonical_exists:
            canonical_metadata = self.validate_cli_receipt(canonical)
            if legacy_exists:
                legacy_metadata = self.validate_cli_receipt(legacy)
                if canonical_metadata != legacy_metadata:
                    raise InstallerError("Os recibos novo e legado da CLI x86QW divergem.")
            return canonical
        if legacy_exists:
            self.validate_cli_receipt(legacy)
            return legacy
        return None

    def installed_cli_version(self) -> str | None:
        receipt = self.cli_receipt_path()
        if receipt is None:
            return None
        metadata = self.validate_cli_receipt(receipt)
        return str(metadata["version"])

    def installer_bundle_identity(self) -> dict[str, object]:
        if ZIPAPP_PATH is not None:
            identity = read_zipapp_json(
                ZIPAPP_PATH, INSTALLER_BUNDLE_METADATA, "Identidade da CLI pública",
            )
            location = ZIPAPP_PATH
        else:
            path = self.project_root / INSTALLER_BUNDLE_METADATA
            if not path.is_file() or path.is_symlink():
                raise InstallerError(f"Identidade do bundle público ausente ou inválida: {path}")
            try:
                identity = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise InstallerError(f"Identidade do bundle público inválida: {path}") from error
            if not isinstance(identity, dict):
                raise InstallerError(f"Identidade do bundle público inválida: {path}")
            location = path
        version = identity.get("version")
        if (
            identity.get("format") != 1
            or identity.get("project") != "x86qw"
            or not isinstance(version, str)
            or not SEMVER_VERSION.fullmatch(version)
        ):
            raise InstallerError(f"Identidade do bundle público inválida: {location}")
        return identity

    def handoff_cli_update(self, action: str, *, dry_run: bool, assume_yes: bool = False) -> bool:
        package = self.installer_bundle_record()
        available = str(package["version"])
        current = self.installed_cli_version()
        if current == available:
            return False
        if current is not None and not self.release_is_newer(available, current, "stable"):
            console.warning(
                f"CLI x86QW instalada ({current}) é mais nova que o catálogo ({available}); preservada."
            )
            return False
        self._create_handoff_stage(".x86qw-update.")
        try:
            if self.update_ui:
                console.heading("Baixando o instalador x86QW")
            artifact = self.download_component_package(package)
            extracted = self.stage / "installer"
            try:
                plan = validate_installer_bundle(artifact, available)
                extract_archive(plan, extracted)
            except ArchiveError as error:
                raise InstallerError(f"Bundle de atualização x86QW inválido: {error}") from error
            bundle = extracted / f"x86qw-installer-{available}"
            application = bundle / CLI_ARCHIVE_NAME
            arguments = [
                "--online-only", "--installed-cli", "--skip-cli-update",
            ]
            if console.verbose:
                arguments.append("--verbose")
            if not console.color:
                arguments.append("--no-color")
            if dry_run:
                arguments.append("--dry-run")
            if assume_yes:
                arguments.append("--yes")
            arguments.extend([action, str(self.target)])
            returncode = python_runtime.run_handoff(application, arguments)
            if returncode:
                raise InstallerError(f"A atualização x86QW terminou com código {returncode}.")
            return True
        finally:
            self.cleanup_stage()

    @staticmethod
    def confirm_update_plan(
        action: str, *, assume_yes: bool, summary: str = "",
    ) -> bool:
        if assume_yes:
            confirmation_source = (
                "install --non-interactive" if action == "install" else "--yes"
            )
            console.info(f"Plano confirmado automaticamente por {confirmation_source}.")
            return True
        confirmation_help = (
            "ou execute install --non-interactive com todas as seleções obrigatórias."
            if action == "install"
            else "ou use --yes para confirmar o plano automaticamente."
        )
        if not navigation.supports_navigation():
            while True:
                try:
                    answer = input(
                        f"\n==> Deseja executar o plano de {action}? [y/n] "
                    ).strip().lower()
                except EOFError as error:
                    raise InstallerError(
                        "A confirmação não pôde ser lida. Execute em um terminal interativo "
                        + confirmation_help
                    ) from error
                if answer in ("y", "yes", "s", "sim"):
                    return True
                if answer in ("n", "no", "nao", "não", ""):
                    console.info("Operação cancelada; nenhum arquivo do jogo foi alterado.")
                    return False
                console.warning("Resposta inválida. Digite y para prosseguir ou n para cancelar.")
        try:
            accepted = navigation.confirm(
                "Deseja executar este plano?",
                breadcrumb=f"x86QW › {action.capitalize()} › Confirmação",
                subtitle=summary,
                description="aplicar as alterações apresentadas",
                default=False,
            )
        except navigation.MenuCancelled as error:
            raise InstallerError(
                "A confirmação não pôde ser lida. Execute em um terminal interativo "
                + confirmation_help
            ) from error
        if not accepted:
            console.info("Operação cancelada; nenhum arquivo do jogo foi alterado.")
        return accepted

    def cli_update_plan_row(self) -> UpdatePlanRow | None:
        identity = self.installer_bundle_identity()
        available = str(identity["version"])
        current = self.installed_cli_version()
        if current == available:
            return None
        package = self.installer_bundle_record()
        if package["version"] != available:
            raise InstallerError("A versão da CLI não corresponde ao catálogo público.")
        return UpdatePlanRow(
            "CLI", "x86QW", current or "não instalada", available, "Atualizar", package_size(package),
        )

    def public_catalog(self, message: str) -> dict[str, object]:
        if self._public_catalog is None:
            if self.update_ui:
                console.heading("Baixando manifestos de pacotes")
            else:
                console.info(message)
            catalog_url = os.environ.get("X86_QW_CATALOG_URL")
            local_catalog = self.project_root / PUBLIC_CATALOG
            catalog_payload: bytes | None = None
            catalog_status = "Loaded"
            try:
                if catalog_url:
                    raise InstallerError(
                        "X86_QW_CATALOG_URL não é autorizado a contornar a cadeia TUF."
                    )
                if not self.online_only and local_catalog.is_file() and not local_catalog.is_symlink():
                    catalog_payload = local_catalog.read_bytes()
                    catalog = json.loads(catalog_payload)
                    console.detail(f"Catálogo da distribuição local: {local_catalog}")
                else:
                    self.prepare_cache()
                    assert self.cache_root is not None
                    trust_root = self.cache_root / "trust"
                    private_fs.ensure_private_directories(
                        trust_root, stop=self.cache_root,
                    )
                    catalog = load_trusted_catalog(
                        bootstrap_root=trusted_root_bytes(),
                        metadata_dir=trust_root / "metadata",
                        target_dir=trust_root / "targets",
                        metadata_base_url=TRUST_METADATA_URL,
                        target_base_url=TRUST_TARGET_URL,
                        fetcher=BoundedTufFetcher(self.remote.get),
                    )
                    catalog_payload = json.dumps(
                        catalog, ensure_ascii=False, separators=(",", ":"),
                    ).encode("utf-8")
                    catalog_status = "Baixado"
                    console.detail(f"Catálogo TUF: {TRUST_METADATA_URL}")
            except TrustError as error:
                raise InstallerError(f"Falha de trust do catálogo x86QW: {error}") from error
            except (OSError, json.JSONDecodeError, UnicodeDecodeError, TypeError) as error:
                raise InstallerError("O catálogo x86QW recebido é inválido.") from error
            if not isinstance(catalog, dict):
                raise InstallerError("O catálogo x86QW recebido é inválido.")
            if catalog.get("format") != 1 or catalog.get("project") != "x86qw":
                raise InstallerError("O catálogo x86QW usa uma identidade ou formato incompatível.")
            if not isinstance(catalog.get("packages"), list):
                raise InstallerError("A lista de pacotes do catálogo x86QW é inválida.")
            if self.update_ui and catalog_payload is not None:
                console.download_result(
                    "x86QW Package Manifest", size=len(catalog_payload), status=catalog_status,
                )
            self._public_catalog = catalog
        return self._public_catalog

    def validate_distribution_path(self, value: object, filename: str) -> str:
        if not isinstance(value, str) or not value or "\\" in value or ":" in value:
            raise InstallerError(f"Caminho inválido na distribuição: {value!r}")
        relative = PurePosixPath(value)
        if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
            raise InstallerError(f"Caminho inseguro na distribuição: {value}")
        if relative.name != filename:
            raise InstallerError(f"O caminho da distribuição não termina em {filename}: {value}")
        return value

    def distribution_artifact(
        self,
        relative: str,
        filename: str,
        *,
        expected_size: int | None,
        expected_sha256: str,
    ) -> Path | None:
        self.validate_distribution_path(relative, filename)
        if self.online_only:
            return None
        root = self.project_root / "dist"
        candidate = root.joinpath(*PurePosixPath(relative).parts)
        if not lexists(candidate):
            return None
        if candidate.is_symlink() or not candidate.is_file():
            raise InstallerError(f"Artefato inválido na distribuição: {candidate}")
        if expected_size is not None and candidate.stat().st_size != expected_size:
            raise InstallerError(
                f"Artefato incompleto na distribuição: {candidate}. Execute git lfs pull e tente novamente."
            )
        if file_hash(candidate, maximum_size=MAX_ARTIFACT_BYTES) != expected_sha256:
            raise InstallerError(
                f"Artefato adulterado na distribuição: {candidate}. Execute git lfs pull e tente novamente."
            )
        return candidate

    def download_component_package(self, package: dict[str, object]) -> Path:
        assert self.stage is not None
        self.prepare_cache()
        assert self.cache_root is not None
        identifier = str(package["package"])
        filename = str(package["filename"])
        digest = str(package["sha256"])
        cache = self.cache_root / "components"
        cache.mkdir(parents=True, exist_ok=True)
        artifact = cache / filename
        ensure_no_symlink(artifact, "pacote de componente em cache")
        if artifact.is_file():
            if (
                artifact.stat().st_size != package["size"]
                or file_hash(artifact, maximum_size=MAX_ARTIFACT_BYTES) != digest
            ):
                raise InstallerError(f"O pacote {identifier} em cache é inválido. Execute cleanup e tente novamente.")
            if self.update_ui:
                console.download_result(
                    f"{identifier} {package['version']}", size=artifact.stat().st_size, status="Cached",
                )
            else:
                console.detail(f"Pacote validado no cache: {artifact}")
            return artifact
        native_relative = package.get(NATIVE_CANDIDATE_ARTIFACT_KEY)
        if isinstance(native_relative, str):
            source, expected_size, expected_sha256 = self._native_candidate_artifact(native_relative)
            if expected_size != int(package["size"]) or expected_sha256 != str(package["sha256"]):
                raise InstallerError(f"Identidade do pacote nativo diverge: {identifier}")
            self.publish_cache_artifact(
                source,
                artifact,
                expected_size=expected_size,
                expected_sha256=expected_sha256,
                label=f"o pacote nativo {identifier}",
            )
            console.success(f"Pacote candidato validado: {filename}")
            return artifact
        distribution_path = package.get("distribution_path")
        if isinstance(distribution_path, str) and distribution_path:
            local = self.distribution_artifact(
                distribution_path, filename,
                expected_size=int(package["size"]), expected_sha256=digest,
            )
            if local is not None:
                self.publish_cache_artifact(
                    local,
                    artifact,
                    expected_size=int(package["size"]),
                    expected_sha256=digest,
                    label=f"o pacote {identifier}",
                )
                if self.update_ui:
                    console.download_result(
                        f"{identifier} {package['version']}", size=artifact.stat().st_size, status="Loaded",
                    )
                else:
                    console.success(f"Pacote carregado da distribuição local: {distribution_path}")
                return artifact
        temporary = self.stage / f"{identifier}.download"
        if self.update_ui:
            console.download_start(
                f"{identifier} {package['version']}", size=int(package["size"]),
            )
        self.remote.get_mirrors(
            tuple(str(url) for url in package["urls"]),
            temporary,
            expected_size=int(package["size"]),
            expected_sha256=digest,
            maximum_size=MAX_ARTIFACT_BYTES,
        )
        if (
            temporary.stat().st_size != package["size"]
            or file_hash(temporary, maximum_size=MAX_ARTIFACT_BYTES) != digest
        ):
            raise InstallerError(f"Um mirror entregou um pacote inválido: {identifier}")
        self.publish_cache_artifact(
            temporary,
            artifact,
            expected_size=int(package["size"]),
            expected_sha256=digest,
            label=f"o pacote {identifier}",
        )
        if self.update_ui:
            console.download_result(
                f"{identifier} {package['version']}", size=artifact.stat().st_size,
            )
        else:
            console.success(f"Pacote baixado e validado: {filename}")
        return artifact

    def component_source_context(self) -> object | None:
        provider = self.component_source_provider
        if self.online_only or provider is None:
            return None
        distribution = self.project_root / "dist"
        if not (distribution / "distributions/nquake").is_dir():
            return None
        if self._component_source_context is None:
            try:
                self._component_source_context = provider.load_context(
                    distribution,
                    self.project_root / DEVELOPMENT_COMPONENT_CATALOG,
                    self.project_root / COMPONENT_RELEASES,
                )
            except (OSError, ValueError, json.JSONDecodeError, tarfile.TarError) as error:
                raise InstallerError(
                    "As fontes canônicas locais estão incompletas ou inválidas. "
                    "Execute git lfs pull e tente novamente."
                ) from error
        return self._component_source_context

    def prepare_component_sources(
        self, package: dict[str, object],
    ) -> tuple[Path, list[tuple[Path, Path]], str] | None:
        assert self.stage is not None
        context = self.component_source_context()
        if context is None:
            return None
        provider = self.component_source_provider
        assert provider is not None
        identifier = str(package["package"])
        try:
            release, source_revision, payloads = provider.resolve_payloads(context, identifier)
        except (OSError, ValueError, json.JSONDecodeError, tarfile.TarError) as error:
            raise InstallerError(
                f"Não foi possível materializar {identifier} a partir das fontes canônicas locais. "
                "Execute git lfs pull e tente novamente."
            ) from error
        revision_key = "source_revision" if "source_revision" in package else "source_commit"
        if release.get("version") != package.get("version") or package.get(revision_key) != source_revision:
            raise InstallerError(f"As fontes canônicas locais não correspondem ao catálogo de {identifier}.")

        root = self.stage / f"sources-{identifier}"
        root.mkdir()
        expected: set[str] = set()
        for _, member_name, payload, _ in payloads:
            relative = PurePosixPath(member_name)
            if (
                relative.is_absolute()
                or any(part in ("", ".", "..") for part in relative.parts)
                or relative.parts[0] not in ("payload", "defaults")
                or member_name in expected
            ):
                raise InstallerError(f"Destino inválido nas fontes canônicas de {identifier}: {member_name}")
            destination = root.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
            host_adapter.apply_mode(destination, 0o644)
            expected.add(member_name)
        managed = root / "payload"
        if not managed.is_dir():
            raise InstallerError(f"As fontes canônicas de {identifier} não produziram conteúdo instalável.")
        defaults_root = root / "defaults"
        defaults = [
            (path, self.target / path.relative_to(defaults_root))
            for path in defaults_root.rglob("*") if path.is_file()
        ] if defaults_root.is_dir() else []
        source = f"x86qw:dist/{identifier}@{source_revision}"
        console.success(f"Componente materializado das fontes canônicas locais: {identifier}")
        return managed, defaults, source

    def prepare_component_package(self, package: dict[str, object], artifact: Path) -> tuple[Path, list[tuple[Path, Path]]]:
        assert self.stage is not None
        identifier = str(package["package"])
        root = self.stage / f"package-{identifier}"
        safe_extract_zip(artifact, root)
        metadata_path = root / "_x86qw/component.json"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise InstallerError(f"Metadados internos inválidos no pacote {identifier}.") from error
        revision_key = "source_revision" if "source_revision" in package else "source_commit"
        source_revision = package.get(revision_key)
        if (
            not isinstance(metadata, dict)
            or metadata.get("format") != 1
            or metadata.get("project") != "x86qw"
            or metadata.get("package") != identifier
            or metadata.get(revision_key) != source_revision
            or not isinstance(metadata.get("members"), list)
        ):
            raise InstallerError(f"Identidade interna inválida no pacote {identifier}.")
        internal_version = metadata.get("version", str(source_revision)[:12])
        if internal_version != package["version"]:
            raise InstallerError(f"Versão interna inválida no pacote {identifier}.")
        expected: set[str] = set()
        for member in metadata["members"]:
            if not isinstance(member, dict) or not isinstance(member.get("path"), str) or not isinstance(member.get("sha256"), str):
                raise InstallerError(f"Inventário interno inválido no pacote {identifier}.")
            relative = member["path"]
            if not relative.startswith(("payload/", "defaults/")):
                raise InstallerError(f"Destino interno inválido no pacote {identifier}: {relative}")
            validate_hex(member["sha256"], HEX64, f"hash interno de {identifier}")
            path = root.joinpath(*PurePosixPath(relative).parts)
            if not path.is_file() or path.is_symlink() or file_hash(path) != member["sha256"]:
                raise InstallerError(f"Arquivo interno inválido no pacote {identifier}: {relative}")
            expected.add(relative)
        actual = {
            path.relative_to(root).as_posix() for path in root.rglob("*")
            if path.is_file() and path != metadata_path
        }
        if actual != expected:
            raise InstallerError(f"O conteúdo do pacote {identifier} diverge do inventário interno.")
        managed = root / "payload"
        if not managed.is_dir():
            raise InstallerError(f"O pacote {identifier} não contém payload instalável.")
        defaults = [
            (path, self.target / path.relative_to(root / "defaults"))
            for path in (root / "defaults").rglob("*") if path.is_file()
        ] if (root / "defaults").is_dir() else []
        normalized_defaults = root / "normalized-defaults"
        for relative in MUTABLE_COMPONENT_DEFAULTS.get(identifier, ()):
            source = managed.joinpath(*PurePosixPath(relative).parts)
            if not source.is_file():
                continue
            normalized = normalized_defaults.joinpath(*PurePosixPath(relative).parts)
            normalized.parent.mkdir(parents=True, exist_ok=True)
            source.replace(normalized)
            defaults.append((normalized, self.target.joinpath(*PurePosixPath(relative).parts)))
        remove_empty_directories(managed)
        return managed, defaults

    def migrate_mutable_component_defaults(self, component: str) -> None:
        mutable = set(MUTABLE_COMPONENT_DEFAULTS.get(component, ()))
        if not mutable:
            return
        present, entries, receipt = self.validate_component_pair(component)
        affected = [(name, digest) for name, digest in entries if name in mutable]
        if not present or not affected:
            return
        assert receipt is not None
        for name, _ in affected:
            path = self.target.joinpath(*PurePosixPath(name).parts)
            if lexists(path) and (not path.is_file() or path.is_symlink()):
                raise InstallerError(f"Configuração mutável inválida: {path}")
        previous_stage = self.stage
        previous_stage_identity = self._stage_identity
        previous_stage_created_roots = self._stage_created_roots
        self._create_stage(".quake-migrate.")
        try:
            inventory = self.stage / f"{component}.inventory"
            self.write_inventory_record(
                inventory,
                ((name, digest) for name, digest in entries if name not in mutable),
            )
            staged_receipt = self.stage / f"{component}.receipt"
            self.write_component_receipt(
                component, receipt["selection"], receipt["source"], inventory, staged_receipt,
            )
            self.commit_component_metadata(component, inventory, staged_receipt)
        finally:
            self.cleanup_stage()
            self.stage = previous_stage
            self._stage_identity = previous_stage_identity
            self._stage_created_roots = previous_stage_created_roots
        console.success(
            f"Configuração pessoal de {component} retirada do inventário imutável e preservada."
        )

    def migrate_legacy_nquake(
        self, mutation_results: list[MutationResult] | None = None,
    ) -> None:
        present, entries, _ = self.validate_nquake_pair()
        if not present:
            return
        console.info("Migrando o recibo nQuake antigo para componentes independentes...")
        selected: list[Path] = []
        for name, expected in entries:
            managed = self.target.joinpath(*PurePosixPath(name).parts)
            if not lexists(managed):
                continue
            if not managed.is_file() or managed.is_symlink():
                raise InstallerError(f"Caminho legado gerenciado inválido: {managed}")
            if file_hash(managed) == expected:
                selected.append(managed)
            else:
                console.warning(f"Arquivo modificado preservado durante a migração: {managed}")
        selected.extend((
            self.target / NQUAKE_RECEIPT,
            self.target / NQUAKE_INVENTORY,
        ))
        owned_stage = self.stage is None
        cleanup_stage = True
        try:
            result = self._remove_paths_transaction(
                (path for path in selected if lexists(path)),
                identifier="migrate-legacy-nquake",
                summary="Recolher a geração agregada nQuake",
            )
            if result is not None and mutation_results is not None:
                mutation_results.append(result)
        except MutationRollbackError:
            cleanup_stage = False
            raise
        finally:
            if owned_stage and cleanup_stage:
                self.cleanup_stage()
                self.stage = None

    def migrate_legacy_clan_arena(
        self,
        selected: list[str],
        mutation_results: list[MutationResult] | None = None,
    ) -> None:
        if not {"final-arena", "pro-x"} & set(selected):
            return
        present, _, _ = self.validate_component_pair("clan-arena")
        if not present:
            return
        console.info("Separando o componente antigo Clan Arena e Pro-X...")
        if mutation_results is None:
            removed = self.remove_component("clan-arena")
        else:
            removed, result = self.remove_component_transaction("clan-arena")
            mutation_results.append(result)
        console.success(
            f"Recibo combinado removido ({file_count(removed)}); arquivos modificados foram preservados."
        )

    def migrate_legacy_component_replacements(
        self,
        selected: list[str],
        mutation_results: list[MutationResult] | None = None,
    ) -> None:
        selected_set = set(selected)
        for legacy, replacement in LEGACY_COMPONENT_REPLACEMENTS.items():
            if replacement not in selected_set:
                continue
            present, _, _ = self.validate_component_pair(legacy)
            if not present:
                continue
            current_present, _, _ = self.validate_component_pair(replacement)
            if current_present:
                raise InstallerError(
                    f"Os componentes antigo ({legacy}) e atual ({replacement}) estão registrados ao mesmo tempo."
                )
            console.info(f"Migrando a identidade do componente {legacy} para {replacement}...")
            if mutation_results is None:
                removed = self.remove_component(legacy)
            else:
                removed, result = self.remove_component_transaction(legacy)
                mutation_results.append(result)
            console.success(
                f"Componente legado {legacy} removido ({file_count(removed)}); "
                "arquivos modificados foram preservados."
            )

    def release_play_support_profiles(
        self,
        selected: list[str],
        mutation_results: list[MutationResult] | None = None,
    ) -> None:
        present, entries, receipt = self.validate_component_pair("play-support")
        if not present:
            return
        released = {
            str(path)
            for identifier in selected
            for path in self.components[identifier].get(
                "managed_files",
                [
                    source["destination"]
                    for source in self.components[identifier].get("project_sources", [])
                    if source.get("mode") == "overlay"
                ],
            )
        }
        if not released:
            return
        remaining: list[tuple[str, str]] = []
        removable: list[Path] = []
        changed = False
        for name, digest in entries:
            if name not in released:
                remaining.append((name, digest))
                continue
            changed = True
            managed = self.target.joinpath(*PurePosixPath(name).parts)
            if not lexists(managed):
                continue
            if not managed.is_file() or managed.is_symlink():
                raise InstallerError(f"Perfil local legado inválido: {managed}")
            if file_hash(managed) == digest:
                removable.append(managed)
            else:
                console.warning(f"Perfil modificado preservado durante a migração: {managed}")
        if not changed:
            return
        if not remaining:
            if mutation_results is None:
                self.remove_component("play-support")
            else:
                _, result = self.remove_component_transaction("play-support")
                mutation_results.append(result)
            return
        assert self.stage is not None and receipt is not None
        local_results: list[MutationResult] = []
        destination_results = (
            mutation_results if mutation_results is not None else local_results
        )
        removal_result = self._remove_paths_transaction(
            removable,
            identifier="release-play-support-profiles",
            summary="Liberar perfis do play-support legado",
        )
        if removal_result is not None:
            destination_results.append(removal_result)
        inventory = self.stage / "play-support-migrated.inventory"
        self.write_inventory_record(inventory, remaining)
        staged_receipt = self.stage / "play-support-migrated.receipt"
        self.write_component_receipt(
            "play-support", receipt["selection"], receipt["source"], inventory, staged_receipt,
        )
        metadata_plan = MutationPlan(
            identifier="migrate-play-support-metadata",
            summary="Atualizar metadados do play-support legado",
            steps=(MutationStep(
                key="metadata",
                description="Publicar inventário restante do play-support",
                observe=lambda: self._component_metadata_observation("play-support"),
                apply=lambda: self.commit_component_metadata(
                    "play-support", inventory, staged_receipt,
                ),
                rollback=self._rollback_component_metadata,
            ),),
        )
        try:
            metadata_result = execute_mutation(prepare_mutation(metadata_plan))
            destination_results.append(metadata_result)
        except BaseException as error:
            if mutation_results is None:
                self.rollback_component_transactions(local_results, error)
            raise

    @staticmethod
    def rollback_component_transactions(
        results: Iterable[MutationResult], operation_error: BaseException,
    ) -> None:
        rollback_errors: list[str] = []
        for result in reversed(tuple(results)):
            try:
                rollback_mutation(result)
            except BaseException as error:
                rollback_errors.append(str(error))
        if rollback_errors:
            raise InstallerError(
                "A operação falhou e o rollback dos componentes ficou incompleto: "
                + "; ".join(rollback_errors)
            ) from operation_error

    @contextmanager
    def component_state_transaction(
        self, existing: list[MutationResult] | None = None,
    ) -> Iterator[list[MutationResult]]:
        """Keep managed inverses alive until the parent state commit resolves."""

        if existing is not None:
            yield existing
            return
        results: list[MutationResult] = []
        cleanup = True
        try:
            yield results
        except BaseException as error:
            if isinstance(error, MutationRollbackError):
                cleanup = False
            if not isinstance(error, PersistenceError) or not error.committed:
                try:
                    self.rollback_component_transactions(results, error)
                except BaseException:
                    cleanup = False
                    raise
            raise
        finally:
            if cleanup:
                self.cleanup_stage()
                self.stage = None

    @contextmanager
    def runtime_mutation_stage(
        self, prefix: str, *, parent_managed: bool,
    ) -> Iterator[None]:
        """Give one runtime an isolated workspace under its parent's live stage."""

        if not parent_managed:
            self._create_stage(prefix)
            try:
                yield
            finally:
                self.cleanup_stage()
                self.stage = None
            return
        if self.stage is None:
            self._create_stage(".x86qw-state-transaction.")
        parent_stage = self.stage
        workspace = private_fs.private_mkdtemp(
            directory=parent_stage, prefix=prefix,
        )
        self.stage = workspace
        try:
            yield
        finally:
            self.stage = parent_stage

    def install_component_batch(
        self,
        results: list[MutationResult],
        selected: list[str],
        *,
        stage_prefix: str,
    ) -> None:
        """Install one batch in the state transaction's shared live stage."""

        if not selected:
            return
        if self.stage is None:
            self._create_stage(stage_prefix)
        results.extend(self.install_components(selected))

    def _rollback_created_default(self, token: CreatedDefaultRollback) -> None:
        destination = token.destination
        removed = False
        if lexists(destination):
            try:
                identity = persistent_path_identity(
                    destination, directory=False,
                )
                unchanged = (
                    identity == token.identity
                    and file_hash(destination) == token.digest
                )
            except OSError:
                unchanged = False
            if unchanged:
                host_adapter.unlink_identity_bound_file(
                    destination, token.identity,
                )
                removed = True
            else:
                console.warning(
                    f"Configuração inicial alterada foi preservada: {destination}"
                )
        else:
            removed = True
        if removed:
            for directory, identity in reversed(token.created_directories):
                if not lexists(directory):
                    continue
                try:
                    remove_persistent_identity_bound_path(
                        directory, identity, directory=True,
                    )
                except OSError:
                    pass

    def _apply_created_default(
        self, source: Path, destination: Path, digest: str,
    ) -> CreatedDefaultRollback:
        if not source.is_file() or source.is_symlink() or file_hash(source) != digest:
            raise InstallerError(f"Configuração inicial mudou antes da cópia: {source}")
        if lexists(destination):
            raise InstallerError(
                f"Configuração pessoal apareceu durante a instalação: {destination}"
            )
        missing: list[Path] = []
        parent = destination.parent
        while parent != self.target and not lexists(parent):
            missing.append(parent)
            parent = parent.parent
        created: list[tuple[Path, tuple[int, int]]] = []
        descriptor = -1
        token: CreatedDefaultRollback | None = None
        try:
            for directory in reversed(missing):
                directory.mkdir(mode=0o700)
                created.append((directory, persistent_path_identity(
                    directory, directory=True,
                )))
            descriptor = private_fs.create_private_file(destination)
            identity = persistent_descriptor_identity(
                descriptor, directory=False,
            )
            copied = hashlib.sha256()
            with source.open("rb") as input_file, os.fdopen(
                descriptor, "wb", closefd=False,
            ) as output:
                for block in iter(lambda: input_file.read(1024 * 1024), b""):
                    output.write(block)
                    copied.update(block)
                output.flush()
                host_adapter.apply_descriptor_mode(descriptor, 0o644)
                os.fsync(descriptor)
            if copied.hexdigest() != digest:
                raise InstallerError(f"Configuração inicial copiada divergiu: {destination}")
            token = CreatedDefaultRollback(destination, digest, identity, tuple(created))
            return token
        except BaseException:
            if token is None and descriptor >= 0:
                identity = persistent_descriptor_identity(
                    descriptor, directory=False,
                )
                os.close(descriptor)
                descriptor = -1
                try:
                    host_adapter.unlink_identity_bound_file(
                        destination, identity,
                    )
                except OSError:
                    pass
            for directory, identity in reversed(created):
                try:
                    remove_persistent_identity_bound_path(
                        directory, identity, directory=True,
                    )
                except OSError:
                    pass
            raise
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def install_component_default_transaction(
        self, source: Path, destination: Path,
    ) -> MutationResult | None:
        digest = file_hash(source)
        destination_exists = lexists(destination)
        if destination_exists:
            if (
                not destination.is_file()
                or destination.is_symlink()
                or file_hash(destination) != digest
            ):
                return None
            present, entries = self.validate_personal_baseline()
            relative = destination.relative_to(self.target).as_posix()
            if present and relative in dict(entries):
                return None
        if self.stage is None:
            self._create_stage(".component-default.")
        baseline_receipt, baseline_inventory = (
            self._stage_personal_baseline_entry(destination, digest)
        )
        baseline_step = MutationStep(
            key="personal-baseline",
            description=f"Registrar configuração pessoal {destination.name}",
            observe=lambda: (
                self._mutation_path_observation(baseline_receipt),
                self._mutation_path_observation(baseline_inventory),
                self._personal_baseline_observation(),
            ),
            apply=lambda: self._commit_personal_baseline(
                baseline_receipt, baseline_inventory,
            ),
            rollback=self._rollback_component_metadata,
        )
        if destination_exists:
            plan = MutationPlan(
                identifier=(
                    "component-default-baseline:"
                    f"{destination.relative_to(self.target).as_posix()}"
                ),
                summary=f"Registrar configuração inicial existente {destination}",
                steps=(baseline_step,),
            )
            return execute_mutation(prepare_mutation(plan))
        plan = MutationPlan(
            identifier=f"component-default:{destination.relative_to(self.target).as_posix()}",
            summary=f"Criar configuração inicial {destination}",
            steps=(
                MutationStep(
                    key="default",
                    description=f"Criar configuração inicial {destination.name}",
                    observe=lambda: (
                        self._mutation_path_observation(source),
                        file_hash(source),
                        self._mutation_path_observation(destination),
                    ),
                    apply=lambda: self._apply_created_default(source, destination, digest),
                    rollback=self._rollback_created_default,
                ),
                baseline_step,
            ),
        )
        return execute_mutation(prepare_mutation(plan))

    def install_components(self, selected: list[str]) -> tuple[MutationResult, ...]:
        assert self.stage is not None
        results: list[MutationResult] = []
        total_files = 0
        noun = "componente" if len(selected) == 1 else "componentes"
        console.heading(f"Instalando {len(selected)} {noun} x86QW")
        try:
            self.migrate_legacy_nquake(results)
            self.migrate_legacy_clan_arena(selected, results)
            self.migrate_legacy_component_replacements(selected, results)
            self.release_play_support_profiles(selected, results)
            for index, identifier in enumerate(selected, 1):
                component = self.components[identifier]
                console.activity(index, len(selected))
                package = self.component_package_record(identifier)
                prepared = self.prepare_component_sources(package)
                if prepared is None:
                    artifact = self.download_component_package(package)
                    managed, defaults = self.prepare_component_package(package, artifact)
                    source = str(package["origin_url"])
                else:
                    managed, defaults, source = prepared
                self.normalize_component_platform_payload(identifier, managed)
                count, result = self.install_component_overlay_transaction(
                    identifier, managed, str(package["version"]), source,
                )
                total_files += count
                results.append(result)
                for staged, destination in defaults:
                    existed_before = lexists(destination)
                    default_result = self.install_component_default_transaction(
                        staged, destination,
                    )
                    if default_result is not None:
                        results.append(default_result)
                        if existed_before:
                            console.detail(
                                f"Configuração inicial existente registrada: {destination}"
                            )
                        else:
                            console.detail(f"Configuração inicial criada: {destination}")
                if identifier == "x86qw-client-bootstrap":
                    self.migrate_nquake_texture_limit(results)
            if "x86qw-client-bootstrap" in selected:
                preset = self.target / "ezquake/configs/preset.cfg"
                if not preset.is_file():
                    assert self.stage is not None
                    staged_preset = self.stage / "nquake-default-preset.cfg"
                    staged_preset.write_text(DEFAULT_PRESET, encoding="utf-8")
                    preset_result = self.install_component_default_transaction(
                        staged_preset, preset,
                    )
                    if preset_result is not None:
                        results.append(preset_result)
            self.migrate_saved_configs(results)
            self.refresh_qw_package_order(mutation_results=results)
            self.reconcile_play_support_transaction(mutation_results=results)
            console.activity_done()
            console.flush_download_summary()
            verb = "instalado" if len(selected) == 1 else "instalados"
            console.success(
                f"{len(selected)} {noun} {verb} · {file_count(total_files)}"
            )
            return tuple(results)
        except BaseException as error:
            console.activity_done()
            self.rollback_component_transactions(results, error)
            raise

    def play_support_player(self):
        gameplay = load_gameplay_module()
        return gameplay.Player(
            self.project_root, self.target, online_only=self.online_only,
        )

    def reconcile_play_support_transaction(
        self,
        *,
        dry_run: bool = False,
        plan_rows: list[UpdatePlanRow] | None = None,
        mutation_results: list[MutationResult] | None = None,
    ) -> tuple[bool, tuple[MutationResult, ...]]:
        player = self.play_support_player()
        games = player.available_local_games()
        issues = player.local_play_support_issues(games)
        if not issues:
            return False, ()
        if dry_run:
            if plan_rows is not None:
                plan_rows.append(UpdatePlanRow(
                    "Gerado", "Suporte de execução dos mods",
                    "ausente ou divergente", "derivado dos componentes instalados", "Reparar",
                ))
            return True, ()
        owned_stage = self.stage is None
        if owned_stage:
            self._create_stage(".quake-play-reconcile.")
        assert self.stage is not None
        player.stage = self.stage
        player._stage_identity = self._stage_identity
        player._stage_created_roots = self._stage_created_roots
        player._stage_lease = self._stage_lease
        local_results: list[MutationResult] = []
        destination = mutation_results if mutation_results is not None else local_results
        initial_result_count = len(destination)
        cleanup = owned_stage and mutation_results is None
        try:
            player.ensure_local_play_support(
                games, mutation_results=destination,
            )
            player.verify_local_play_support(games)
        except BaseException as error:
            if isinstance(error, MutationRollbackError):
                cleanup = False
            if mutation_results is None:
                try:
                    self.rollback_component_transactions(local_results, error)
                except BaseException:
                    cleanup = False
                    raise
            raise
        finally:
            if cleanup:
                self.cleanup_stage()
                self.stage = None
        console.success("Suporte de execução derivado foi reconciliado.")
        created = tuple(destination[initial_result_count:])
        return True, created if mutation_results is not None else ()

    def reconcile_play_support(
        self,
        *,
        dry_run: bool = False,
        plan_rows: list[UpdatePlanRow] | None = None,
        mutation_results: list[MutationResult] | None = None,
    ) -> bool:
        """Compatibility wrapper for callers that only consume changed/no-op."""

        changed, _ = self.reconcile_play_support_transaction(
            dry_run=dry_run,
            plan_rows=plan_rows,
            mutation_results=mutation_results,
        )
        return changed

    def _personal_config_transaction(
        self,
        identifier: str,
        replacements: list[tuple[Path, bytes]],
        backups: list[tuple[Path, bytes, int]],
        *,
        mutation_results: list[MutationResult] | None,
    ) -> MutationResult | None:
        if not replacements and not backups:
            return None
        created_stage = self.stage is None
        if created_stage:
            self._create_stage(f".x86qw-{identifier}.")
        assert self.stage is not None
        workspace = private_fs.private_mkdtemp(
            directory=self.stage, prefix=f".{identifier}.prepared.",
        )
        steps: list[MutationStep] = []
        prepared_entries = [
            ("backup", destination, payload, mode)
            for destination, payload, mode in backups
        ] + [
            (
                "config", destination, payload,
                host_adapter.path_mode(destination),
            )
            for destination, payload in replacements
        ]
        counters = {"backup": 0, "config": 0}
        for kind, destination, payload, source_mode in prepared_entries:
            counters[kind] += 1
            index = counters[kind]
            prepared = workspace / f"{kind}-{index}"
            prepared.write_bytes(payload)
            host_adapter.apply_mode(prepared, source_mode)
            steps.append(MutationStep(
                key=f"{kind}:{index}",
                description=f"Publicar {kind} pessoal {destination.name}",
                observe=lambda prepared=prepared, destination=destination: (
                    self._mutation_path_observation(prepared),
                    self._mutation_path_observation(destination),
                ),
                apply=lambda prepared=prepared, destination=destination: (
                    self._apply_runtime_payload(prepared, destination)
                ),
                rollback=self._rollback_runtime_payload,
            ))
        plan = MutationPlan(
            identifier=f"personal-config:{identifier}",
            summary="Migrar configurações pessoais de forma reversível",
            steps=tuple(steps),
        )
        cleanup = created_stage
        try:
            result = execute_mutation(prepare_mutation(plan))
        except MutationRollbackError:
            cleanup = False
            raise
        except MutationApplyError as error:
            if isinstance(error.operation_error, InstallerError):
                raise error.operation_error
            raise InstallerError(
                "A migração das configurações pessoais falhou e foi revertida."
            ) from error
        finally:
            if cleanup:
                self.cleanup_stage()
        if mutation_results is not None:
            mutation_results.append(result)
        return result

    def migrate_nquake_texture_limit(
        self, mutation_results: list[MutationResult] | None = None,
    ) -> None:
        replacements: list[tuple[Path, bytes]] = []
        configs = sorted(set(self.target.glob("*/configs/config.cfg")))
        for config in configs:
            if not lexists(config):
                continue
            if (
                not config.is_file()
                or config.is_symlink()
                or config.parent.is_symlink()
                or config.parent.parent.is_symlink()
            ):
                raise InstallerError(f"Configuração pessoal nQuake inválida: {config}")
            contents = config.read_bytes()
            updated, count = NQUAKE_TEXTURE_LIMIT.subn(rb'\g<1>"16384"\g<2>\g<3>', contents)
            if count == 0:
                continue
            replacements.append((config, updated))
        self._personal_config_transaction(
            "nquake-texture-limit", replacements, [],
            mutation_results=mutation_results,
        )
        if replacements:
            console.info(
                "Limite de textura nQuake ajustado de 32768 para 16384 em "
                f"{file_count(len(replacements))}; "
                "demais preferências foram preservadas."
            )

    def managed_temporary_aliases(self) -> set[str]:
        aliases: set[str] = set()
        candidates = (
            "qw/autoexec.cfg",
            "qw/x86qw-ktx.cfg",
            "arena/x86qw-arena.cfg",
            "prox/x86qw-prox.cfg",
            "fortress/x86qw-fortress.cfg",
            "td2/x86qw-td2.cfg",
        )
        for relative in candidates:
            path = self.target / relative
            if not path.is_file() or path.is_symlink():
                continue
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                match = re.match(r"^\s*tempalias\s+([^\s]+)", line, flags=re.IGNORECASE)
                if match:
                    aliases.add(match.group(1).casefold())
        return aliases

    def migrate_saved_configs(
        self, mutation_results: list[MutationResult] | None = None,
    ) -> None:
        aliases = self.managed_temporary_aliases()
        configs = sorted(set(self.target.glob("*/configs/config.cfg")))
        prox = self.target / "prox/configs/config.cfg"
        base = self.target / "ezquake/configs/config.cfg"
        originals: dict[Path, bytes] = {}
        working: dict[Path, bytes] = {}
        for config in configs:
            if not config.is_file() or config.is_symlink():
                raise InstallerError(f"Configuração pessoal inválida: {config}")
            originals[config] = config.read_bytes()
            working[config] = originals[config]
        backups: list[tuple[Path, bytes, int]] = []
        prox_migrated = False
        if prox.is_file() and not prox.is_symlink():
            contents = working[prox]
            if b"// Niclas's config" in contents:
                backup = prox.with_name("config.pre-x86qw.cfg")
                if not lexists(backup):
                    backups.append((
                        backup, contents, host_adapter.path_mode(prox),
                    ))
                if not base.is_file() or base.is_symlink():
                    raise InstallerError("A migração do Pro-X exige ezquake/configs/config.cfg válido.")
                modern = (
                    b"// x86QW: base Pro-X migrada; original preservado em config.pre-x86qw.cfg\n"
                    + working[base]
                )
                working[prox] = modern
                prox_migrated = True

        changed = 0
        alias_pattern = re.compile(rb'^\s*alias\s+([^\s]+).*(?:\r?\n|$)', re.MULTILINE | re.IGNORECASE)
        broken_remote_capabilities = re.compile(
            rb'^\s*cl_remote_capabilities\s+"\$cl_remote_capabilities,[^"]*"\s*(?:\r?\n|$)',
            re.MULTILINE | re.IGNORECASE,
        )
        for config in configs:
            original = working[config]

            def keep_personal_alias(match: re.Match[bytes]) -> bytes:
                name = match.group(1).decode("utf-8", errors="replace").casefold()
                return b"" if name in aliases else match.group(0)

            updated = alias_pattern.sub(keep_personal_alias, original)
            updated = broken_remote_capabilities.sub(b"", updated)
            if updated != original:
                backup = config.with_name("config.aliases-pre-x86qw.cfg")
                if not lexists(backup):
                    backups.append((
                        backup, original, host_adapter.path_mode(config),
                    ))
                working[config] = updated
                changed += 1
        replacements = [
            (config, working[config])
            for config in configs
            if working[config] != originals[config]
        ]
        self._personal_config_transaction(
            "saved-configs", replacements, backups,
            mutation_results=mutation_results,
        )
        if prox_migrated:
            console.success("Configuração Pro-X migrada para a base x86QW atual; backup pessoal preservado.")
        if changed:
            console.success(
                f"Configurações persistidas saneadas em {file_count(changed)}; "
                "backups pessoais foram preservados e os aliases x86QW agora são temporários."
            )

    def choose_components_to_remove(self) -> list[str]:
        installed = self.installed_components()
        if not installed:
            console.info("Nenhum componente x86QW gerenciado está instalado.")
            return []
        try:
            chosen = navigation.select_many(
                "Quais componentes deseja remover?",
                (
                    navigation.MenuOption(
                        identifier,
                        str(self.components[identifier]["label"]),
                        detail=str(self.components[identifier]["description"]),
                    )
                    for identifier in installed
                ),
                breadcrumb="x86QW › Gerenciar › Componentes › Remover",
                subtitle="Dependentes instalados também serão incluídos no plano.",
                searchable=True,
                allow_back=True,
            )
        except (EOFError, navigation.MenuCancelled, ValueError) as error:
            raise InstallerError("Nenhum componente foi selecionado para remoção.") from error
        selected = list(chosen or ())
        if not selected:
            return []
        expanded = set(selected)
        changed = True
        while changed:
            changed = False
            for identifier in installed:
                if identifier not in expanded and set(self.components[identifier]["requires"]) & expanded:
                    expanded.add(identifier)
                    changed = True
        added = [identifier for identifier in installed if identifier in expanded and identifier not in selected]
        if added:
            console.warning("Dependentes também serão removidos: " + ", ".join(added))
        return [identifier for identifier in reversed(installed) if identifier in expanded]

    def manage_components(
        self, *, mutation_results: list[MutationResult] | None = None,
    ) -> None:
        answer = navigation.select_one(
            "O que deseja fazer com os componentes?",
            (
                navigation.MenuOption("install", "Instalar ou atualizar", "escolher perfil e componentes"),
                navigation.MenuOption("remove", "Remover", "preservar arquivos pessoais"),
            ),
            breadcrumb="x86QW › Gerenciar › Componentes",
            allow_back=True,
        )
        if answer is None:
            return
        self.check_paks()
        if answer == "remove":
            selected = self.choose_components_to_remove()
            with self.component_state_transaction(mutation_results) as results:
                if selected and self.stage is None:
                    self._create_stage(".x86qw-components-remove.")
                for identifier in selected:
                    removed, result = self.remove_component_transaction(identifier)
                    results.append(result)
                    console.success(
                        f"{self.components[identifier]['label']} removido "
                        f"({file_count(removed)})."
                    )
                self.refresh_qw_package_order(mutation_results=results)
                self.reconcile_play_support_transaction(
                    mutation_results=results,
                )
                self.write_install_state(
                    "custom" if self.installed_components() else "none",
                    self.installed_components(),
                    mutation_results=results,
                )
            return
        selected = self.choose_components()
        with self.component_state_transaction(mutation_results) as results:
            if self.stage is None:
                self._create_stage(".quake-install.")
            results.extend(self.install_components(selected))
            self.write_install_state(
                self.selected_component_profile,
                self.requested_components,
                mutation_results=results,
            )

    def install_component_phase(
        self, *, selected: list[str] | None = None,
    ) -> tuple[MutationResult, ...]:
        assert self.stage is not None
        selected = self.choose_components() if selected is None else selected
        results = list(self.install_components(selected))
        try:
            self.write_install_state(
                self.selected_component_profile,
                self.requested_components,
                mutation_results=results,
            )
        except BaseException as error:
            if not isinstance(error, PersistenceError) or not error.committed:
                self.rollback_component_transactions(results, error)
            raise
        return tuple(results)

    def hub_servers(self) -> list[dict[str, object]]:
        console.info("Consultando servidores ativos no QuakeWorld Hub...")
        remote: list[dict[str, object]] | None = None
        try:
            servers = json.loads(self.remote.get(
                HUB_SERVERS_API,
                maximum_size=HUB_MAX_BYTES,
                timeout=CATALOG_TIMEOUT,
            ))
        except (json.JSONDecodeError, TypeError, InstallerError):
            servers = None
        if isinstance(servers, list):
            valid = []
            for server in servers:
                if not isinstance(server, dict):
                    continue
                address = server.get("address")
                if not isinstance(address, str) or not re.fullmatch(r"[A-Za-z0-9_.:\[\]-]+:[0-9]{1,5}", address):
                    continue
                port = int(address.rsplit(":", 1)[1])
                if not 1 <= port <= 65535:
                    continue
                valid.append(server)
            if valid:
                remote = sorted(
                    valid,
                    key=lambda item: sum(
                        1 for player in item.get("players", [])
                        if isinstance(player, dict) and not player.get("is_bot")
                    ),
                    reverse=True,
                )
        discovered = discover_servers(remote, load_library(self.target))
        if discovered:
            if remote is None:
                console.info("Hub indisponível; usando favoritos e recentes locais.")
            return list(discovered)
        raise InstallerError("Hub indisponível e não há favoritos ou recentes locais.")

    def host_runtimes(self) -> list[tuple[str, Path]]:
        platform_key = HOST_PLATFORMS.get(host_platform.system())
        if platform_key is None:
            raise InstallerError(f"A abertura automática não é suportada neste sistema: {host_platform.system()}.")
        choices: list[tuple[str, Path]] = []
        self._runtime_launch_hashes.clear()
        spec = PLATFORMS[platform_key]
        for channel in ("stable", "nightly"):
            receipt_path = self.ezquake_receipt_path(spec, channel)
            if receipt_path is None:
                continue
            receipt = self.validate_ezquake_receipt(receipt_path, spec, channel)
            self.check_runtime(spec, channel, receipt)
            runtime = self.target / spec.runtime(channel)
            macos_action = (
                self.macos_runtime_action(
                    runtime, channel, receipt["artifact_sha256"], receipt["binary_sha256"],
                )
                if spec.key == "macos" else None
            )
            if macos_action is not None:
                raise InstallerError(
                    f"O runtime macOS requer reparo antes da execução: {runtime}. Execute update."
                )
            self._runtime_launch_hashes[runtime] = receipt["binary_sha256"]
            choices.append((f"ezQuake {channel} {receipt['selection']}", runtime))
        return choices

    def choose_host_runtime(
        self, *, breadcrumb: str = "x86QW › Cliente",
    ) -> tuple[str, Path] | None:
        choices = self.host_runtimes()
        if not choices:
            raise InstallerError("Nenhum ezQuake gerenciado para este sistema está instalado. Execute install primeiro.")
        if len(choices) == 1:
            return choices[0]
        selected = navigation.select_one(
            "Qual cliente deseja abrir?",
            (
                navigation.MenuOption(str(index), label, "runtime instalado")
                for index, (label, _) in enumerate(choices)
            ),
            breadcrumb=breadcrumb,
            allow_back=True,
        )
        if selected is None:
            return None
        return choices[int(selected)]

    def launch_runtime(
        self, runtime: Path, quake_arguments: list[str],
    ) -> Any:
        # A distribuição é autocontida: ~/.ezquake não pode sobrepor configs ou assets.
        base_arguments = ["-nohome", "-basedir", str(self.target)]
        if os.environ.get("X86QW_TEST_WINDOWED") == "1":
            # Smoke tests must not capture the user's display. Fullscreen is
            # exercised only by tests that explicitly clear this environment.
            # Disable nQuake's automatic config save before any test command so
            # -window can never leak into the player's persistent config.cfg.
            # Port zero asks ezQuake to bind an ephemeral client port, allowing
            # a windowed smoke to coexist with a client already in use.
            base_arguments.extend([
                "-window", "-width", "1280", "-height", "720",
                "-clientport", "0",
            ])
        if os.environ.get("X86QW_TEST_CONSOLE_LOG") == "1":
            # Native smokes can assert gamecode output without changing the
            # regular launch contract. The resulting qconsole.log is runtime
            # data and is removed by the smoke harness after inspection.
            base_arguments.append("-condebug")
        if os.environ.get("X86QW_TEST_WINDOWED") == "1":
            # Keep native local-game smokes independent from a server-browser
            # refresh that might already be running in another ezQuake client.
            base_arguments.extend([
                "+cfg_save_onquit", "0",
                "+sb_findroutes", "0",
                "+sb_autoupdate", "0",
            ])
        target = host_adapter.client_launch_target(
            runtime,
            system=host_platform.system(),
            expected_sha256=self._runtime_launch_hashes.get(runtime),
        )
        command = [str(target.executable), *base_arguments, *quake_arguments]
        console.detail("$ " + " ".join(command))
        try:
            return supervisor_core.spawn_detached_client(
                tuple(command), self.target, launch_target=target,
            )
        except OSError as error:
            raise InstallerError(f"Não foi possível abrir {runtime}: {error}") from error

    def browse_hub(self) -> None:
        servers = self.hub_servers()
        server_options = []
        for index, server in enumerate(servers, 1):
            settings = server.get("settings") if isinstance(server.get("settings"), dict) else {}
            players = server.get("players") if isinstance(server.get("players"), list) else []
            humans = sum(1 for player in players if isinstance(player, dict) and not player.get("is_bot"))
            bots = sum(1 for player in players if isinstance(player, dict) and player.get("is_bot"))
            mode = str(server.get("mode") or settings.get("mode") or "-")[:12]
            map_name = str(settings.get("map") or "-")[:18]
            hostname = str(settings.get("hostname") or server.get("title") or server["address"])
            hostname = "".join(character if character.isprintable() and character != "\ufffd" else "?" for character in hostname)[:34]
            human_label = "humano" if humans == 1 else "humanos"
            bot_label = "bot" if bots == 1 else "bots"
            server_options.append(navigation.MenuOption(
                str(index - 1), hostname,
                f"{humans} {human_label} + {bots} {bot_label} · {mode} · {map_name}",
                str(server["address"]),
            ))
        selected = None
        action = None
        runtime_choice = None
        quake_arguments: list[str] = []
        operation = "conexão"
        qtv_url = ""
        address = ""
        state = "server"
        while True:
            if state == "server":
                selected = navigation.select_one(
                    "Servidores QuakeWorld ativos",
                    server_options,
                    breadcrumb="x86QW › Encontrar servidor",
                    subtitle="Jogadores humanos primeiro. Use a busca para filtrar servidores.",
                    searchable=True,
                    allow_back=True,
                )
                if selected is None:
                    console.info("Hub fechado; nenhum cliente foi aberto.")
                    return
                action = None
                runtime_choice = None
                state = "action"
                continue
            server = servers[int(selected)]
            address = str(server["address"])
            qtv = server.get("qtv_stream")
            qtv_url = qtv.get("url", "") if isinstance(qtv, dict) else ""
            has_qtv = isinstance(qtv_url, str) and re.fullmatch(
                r"[0-9]+@[A-Za-z0-9_.:\[\]-]+:[0-9]{1,5}", qtv_url,
            )
            if state == "action":
                action = navigation.select_one(
                    "Como deseja entrar?",
                    (
                        navigation.MenuOption("join", "Jogar", "conectar como jogador"),
                        navigation.MenuOption("observe", "Observar", "entrar como espectador"),
                        navigation.MenuOption(
                            "qtv", "Assistir pelo QTV", "reproduzir o stream publicado",
                            enabled=bool(has_qtv),
                            disabled_reason="este servidor não publicou um stream QTV válido",
                        ),
                    ),
                    breadcrumb="x86QW › Encontrar servidor › " + address,
                    allow_back=True,
                )
                if action is None:
                    state = "server"
                    continue
                runtime_choice = None
                state = "runtime"
                continue
            if action == "qtv":
                if not has_qtv:
                    raise InstallerError("Este servidor não publicou um stream QTV válido.")
                quake_arguments = ["+qtvplay", qtv_url]
                operation = "QTV"
            elif action == "observe":
                quake_arguments = ["+observe", address]
                operation = "observação"
            else:
                quake_arguments = ["+join", address]
                operation = "conexão"
            if state == "runtime":
                runtime_choice = self.choose_host_runtime(
                    breadcrumb="x86QW › Encontrar servidor › Cliente",
                )
                if runtime_choice is None:
                    state = "action"
                    continue
                state = "confirm"
                continue
            label, runtime = runtime_choice
            action_label = {
                "join": "Jogar",
                "observe": "Observar",
                "qtv": "Assistir pelo QTV",
            }[action]
            summary = "\n".join((
                "Resumo da conexão",
                f"  Servidor | {server_options[int(selected)].label}",
                f"  Endereço | {address}",
                f"  Ação     | {action_label}",
                f"  Cliente  | {label}",
                *((f"  Stream   | {qtv_url}",) if action == "qtv" else ()),
            ))
            confirmed = navigation.confirm(
                "Abrir este servidor?",
                breadcrumb="x86QW › Encontrar servidor › Confirmação",
                subtitle="\n" + summary,
                description="abrir o ezQuake com a conexão selecionada",
                default=True,
                allow_back=True,
            )
            if confirmed is None:
                runtime_choice = None
                state = "runtime"
                continue
            if not confirmed:
                console.info("Conexão cancelada; nenhum cliente foi aberto.")
                return
            break
        label, runtime = runtime_choice
        self.launch_runtime(runtime, quake_arguments)
        title = str(server_options[int(selected)].label)
        origin = server.get("origin")
        origin = origin if origin in {"user", "hub", "local"} else "hub"
        try:
            record_recent(self.target, address, title=title, origin=str(origin))
        except (OSError, ValueError) as error:
            console.warning(f"Não foi possível gravar o recente local: {error}")
        console.success(f"{label} aberto para {operation} em {address}.")

    def verify_ezquake_variants(self, *, report_details: bool = True) -> int:
        verified = 0
        for spec in PLATFORMS.values():
            for channel in ("stable", "nightly"):
                receipt_path = self.ezquake_receipt_path(spec, channel)
                if receipt_path is None:
                    continue
                receipt = self.validate_ezquake_receipt(receipt_path, spec, channel)
                runtime = self.target / spec.runtime(channel)
                if not lexists(runtime):
                    raise InstallerError(f"missing ezQuake runtime: {runtime}")
                self.check_runtime(spec, channel, receipt)
                macos_action = (
                    self.macos_runtime_action(
                        runtime, channel, receipt["artifact_sha256"], receipt["binary_sha256"],
                    )
                    if spec.key == "macos" else None
                )
                if macos_action is not None:
                    if channel == "stable":
                        raise InstallerError(
                            "O ezQuake stable foi re-assinado localmente por uma versão anterior. "
                            "Restaure o bundle upstream integral reexecutando o bootstrap no mesmo "
                            f"destino: {runtime}"
                        )
                    raise InstallerError(
                        f"O fullscreen integral ainda não foi aplicado em {runtime}. Execute update."
                    )
                if report_details:
                    console.success(
                        f"ezQuake {spec.label} {channel} "
                        f"{receipt['selection']} íntegro."
                    )
                verified += 1
        return verified

    def verify_installation(self, *, report_details: bool | None = None) -> None:
        if report_details is None:
            report_details = console.verbose
        console.heading("Verificando instalação")
        self.check_paks()
        runtime_count = self.verify_ezquake_variants(report_details=report_details)
        if runtime_count == 0:
            raise InstallerError(f"Nenhum ezQuake gerenciado foi encontrado em {self.target}. Execute install primeiro.")
        legacy, _, _ = self.validate_nquake_pair()
        if legacy:
            raise InstallerError(
                "Metadados nQuake antigos encontrados. Reexecute o bootstrap x86QW "
                "no mesmo destino para migrar a instalação."
            )
        installed = self.installed_components()
        if lexists(self.target / INSTALL_STATE):
            state = self.load_install_state()
            recorded = set(state["recorded_components"])
            if recorded != set(installed):
                raise InstallerError(
                    "O estado da instalação diverge dos componentes registrados. Execute update para reconciliar."
                )
        if not installed:
            console.info("Nenhum componente x86QW está instalado.")
        managed_file_count = 0
        for identifier in installed:
            managed_file_count += self.verify_component(
                identifier, report_details=report_details,
            )
        if lexists(self.target / "id1/gpl_maps.pk3"):
            raise InstallerError("shareware gpl_maps.pk3 must not be installed with registered PAKs")
        self.verify_component("maps", report_details=report_details)
        self.verify_component("presets", report_details=report_details)
        player = self.play_support_player()
        player.verify_local_play_support(player.available_local_games())
        self.verify_qw_package_order(report_details=report_details)
        if report_details:
            self.report_nquake_startup_state(installed)
        component_noun = "componente" if len(installed) == 1 else "componentes"
        console.success(
            f"ezQuake + {len(installed)} {component_noun} íntegros · "
            f"{file_count(managed_file_count)}"
        )

    def installation_change_ignored_paths(self) -> tuple[str, ...]:
        paths = {
            ".DS_Store",
            ".gitignore",
            ".x86qw",
            "LICENSE",
            "README-X86QW.txt",
            "ezquake/qconsole.log",
            "ezquake/temp",
            "id1/pak0.pak",
            "id1/pak1.pak",
            "qw/demos",
            "qw/screenshots",
            "x86qw.cmd",
            "x86qw.sh",
        }
        for spec in PLATFORMS.values():
            paths.update((spec.runtime("stable"), spec.runtime("nightly")))
        return tuple(sorted(paths))

    def installation_change_baseline(self) -> dict[str, ManagedInstallationFile]:
        managed: dict[str, ManagedInstallationFile] = {}
        for component in self.metadata_component_ids():
            present, entries, _ = self.validate_component_pair(component)
            if not present:
                continue
            for name, digest in entries:
                record = ManagedInstallationFile(component, digest)
                previous = managed.get(name)
                if previous is not None and previous != record:
                    raise InstallerError(
                        f"Inventários de componentes divergem sobre o arquivo: {name}"
                    )
                managed[name] = record
        personal_present, personal_entries = self.validate_personal_baseline()
        if personal_present:
            for name, digest in personal_entries:
                record = ManagedInstallationFile(
                    PERSONAL_BASELINE_COMPONENT, digest,
                )
                previous = managed.get(name)
                if previous is not None and previous != record:
                    raise InstallerError(
                        "Inventários gerenciado e pessoal divergem sobre o arquivo: "
                        f"{name}"
                    )
                managed[name] = record
        return managed

    def report_installation_changes(
        self, *, sync_gitignore: bool = False,
    ) -> tuple[InstallationChange, ...]:
        managed = self.installation_change_baseline()
        ignored = self.installation_change_ignored_paths()
        if sync_gitignore:
            payload = render_installation_gitignore(
                managed,
                ignored_paths=ignored,
            ).encode("utf-8")
            destination = self.target / ".gitignore"
            try:
                atomic_write_bytes(destination, payload, mode=0o644)
            except AtomicWriteError as error:
                raise InstallerError(
                    f"Não foi possível atualizar o filtro Git da instalação: {destination}"
                ) from error
            console.info(f"Filtro Git seletivo atualizado: {destination}")

        changes = inspect_installation_changes(
            self.target,
            managed,
            ignored_paths=ignored,
        )
        console.heading("Mudanças locais da instalação")
        for change in changes:
            component = f"  [{change.component}]" if change.component else ""
            print(f"{change.status}  {change.path}{component}")
        if changes:
            console.info(
                f"{len(changes)} diferença(s): A novo, M alterado, D removido."
            )
        else:
            console.success("Nenhuma diferença local em relação à instalação-base.")
        return changes

    def component_metadata_assessment(self) -> tuple[list[str], list[str]]:
        valid: list[str] = []
        diagnostics: list[str] = []
        metadata = self.target / METADATA_DIR
        for identifier in self.metadata_component_ids():
            canonical = self.component_pair_paths(identifier, metadata)
            legacy = self.component_pair_paths(identifier, metadata, legacy=True)
            if not any(lexists(path) for path in (*canonical, *legacy)):
                continue
            try:
                present, _, _ = self.validate_component_pair(identifier)
            except InstallerError as error:
                diagnostics.append(f"{identifier}: {error}")
                continue
            if present:
                valid.append(identifier)
        return valid, diagnostics

    def client_catalog_release(
        self, spec: PlatformSpec, channel: str, selection: str,
    ) -> ReleaseRecord | None:
        self.spec = spec
        records = self.stable_catalog() if channel == "stable" else self.nightly_catalog()
        return next((record for record in records if record[0] == selection), None)

    def client_repair_assessment(self) -> tuple[list[ClientRepairIssue], list[str]]:
        issues: list[ClientRepairIssue] = []
        diagnostics: list[str] = []
        for spec in PLATFORMS.values():
            for channel in ("stable", "nightly"):
                runtime = self.target / spec.runtime(channel)
                canonical = self.target / spec.receipt(channel)
                legacy = self.target / spec.legacy_receipt(channel)
                has_metadata = lexists(canonical) or lexists(legacy)
                if not has_metadata:
                    if lexists(runtime):
                        diagnostics.append(
                            f"ezQuake {spec.label} {channel}: runtime presente sem recibo; preservado"
                        )
                    continue
                try:
                    receipt_path = self.ezquake_receipt_path(spec, channel)
                    assert receipt_path is not None
                    receipt = self.validate_ezquake_receipt(receipt_path, spec, channel)
                except (InstallerError, AssertionError) as error:
                    diagnostics.append(
                        f"ezQuake {spec.label} {channel}: recibo inválido ou parcial ({error})"
                    )
                    continue
                if not lexists(runtime):
                    try:
                        release = self.client_catalog_release(spec, channel, receipt["selection"])
                    except InstallerError:
                        release = None
                    issues.append(ClientRepairIssue(
                        spec, channel, receipt_path, receipt,
                        "runtime ausente; payload precisa do bootstrap" if release is None else "runtime ausente",
                        "payload", release,
                    ))
                    continue
                if (
                    spec.key == "linux"
                    and runtime.is_file()
                    and not runtime.is_symlink()
                    and file_hash(runtime) == receipt["binary_sha256"]
                    and host_adapter.executable_permission_missing(runtime)
                ):
                    issues.append(ClientRepairIssue(
                        spec, channel, receipt_path, receipt,
                        "AppImage sem permissão de execução", "permission", None, "local-repair",
                    ))
                    continue
                try:
                    self.check_runtime(spec, channel, receipt)
                except InstallerError:
                    try:
                        release = self.client_catalog_release(spec, channel, receipt["selection"])
                    except InstallerError:
                        release = None
                    issues.append(ClientRepairIssue(
                        spec, channel, receipt_path, receipt,
                        "runtime ausente, incompleto ou divergente", "payload", release,
                    ))
                    continue
                macos_action = (
                    self.macos_runtime_action(
                        runtime, channel, receipt["artifact_sha256"], receipt["binary_sha256"],
                    )
                    if spec.key == "macos" else None
                )
                if macos_action is not None:
                    if macos_action == "restore-upstream":
                        try:
                            release = self.client_catalog_release(
                                spec, channel, receipt["selection"],
                            )
                        except InstallerError:
                            release = None
                        issues.append(ClientRepairIssue(
                            spec, channel, receipt_path, receipt,
                            "bundle x86QW re-assinado; restaurar o bundle upstream integral",
                            "payload", release,
                        ))
                    else:
                        issues.append(ClientRepairIssue(
                            spec, channel, receipt_path, receipt,
                            "preparação macOS ausente", "macos-preparation", None, "local-repair",
                        ))
        return issues, diagnostics

    def runtime_permission_repairs(self, installed: set[str] | None = None) -> list[Path]:
        if not host_adapter.supports_posix_permissions():
            return []
        repairs: list[Path] = []
        seen: set[Path] = set()
        installed = set(self.installed_components()) if installed is None else installed
        for runtime in RUNTIMES.values():
            component = runtime.get("component")
            if component not in installed:
                continue
            for platform_entry in runtime.get("platforms", []):
                if not isinstance(platform_entry, dict) or platform_entry.get("permissions") != "executable":
                    continue
                relative = platform_entry.get("runtime_path")
                if not isinstance(relative, str):
                    continue
                binary = self.target.joinpath(*PurePosixPath(relative).parts)
                if (
                    binary not in seen
                    and binary.is_file()
                    and not binary.is_symlink()
                    and host_adapter.executable_permission_missing(binary)
                ):
                    seen.add(binary)
                    repairs.append(binary)
        return repairs

    def _rollback_runtime_permission(self, token: RuntimePermissionRollback) -> None:
        if self._regular_identity(token.path) != token.identity:
            raise InstallerError(
                f"Runtime mudou durante o rollback da permissão: {token.path}"
            )
        host_adapter.apply_mode(token.path, token.mode)

    def _apply_runtime_permission(self, path: Path) -> RuntimePermissionRollback:
        identity = self._regular_identity(path)
        mode = host_adapter.path_mode(path)
        token = RuntimePermissionRollback(path, identity, mode)
        try:
            host_adapter.add_owner_execute(path)
            if self._regular_identity(path) != identity:
                raise InstallerError(
                    f"Runtime mudou durante o reparo da permissão: {path}"
                )
            return token
        except BaseException as error:
            try:
                self._rollback_runtime_permission(token)
            except BaseException as rollback_error:
                raise InstallerError(
                    f"O reparo da permissão falhou e o rollback ficou incompleto: {rollback_error}"
                ) from error
            raise

    def repair_runtime_permission(
        self, path: Path, mutation_results: list[MutationResult],
    ) -> None:
        plan = MutationPlan(
            identifier=f"runtime-permission:{path.name}",
            summary=f"Restaurar a permissão de execução de {path.name}",
            steps=(MutationStep(
                key="permission",
                description="Adicionar execução somente ao proprietário",
                observe=lambda: self._mutation_path_observation(path),
                apply=lambda: self._apply_runtime_permission(path),
                rollback=self._rollback_runtime_permission,
            ),),
        )
        try:
            result = execute_mutation(prepare_mutation(plan))
        except MutationApplyError as error:
            if isinstance(error.operation_error, InstallerError):
                raise error.operation_error
            raise InstallerError(
                f"A permissão de execução não pôde ser restaurada: {path}"
            ) from error
        mutation_results.append(result)
        console.success(f"Permissão de execução restaurada em {path}.")

    def repair_plan(self) -> RepairAssessment:
        self.check_paks()
        valid_metadata, metadata_diagnostics = self.component_metadata_assessment()
        state_path = self.target / INSTALL_STATE
        recovered_state: dict[str, object] | None = None
        try:
            if state_path.is_file() and not state_path.is_symlink():
                state = self.current_install_state(
                    self.read_install_state_document(state_path)
                )
                recorded = set(state["recorded_components"])
                installed = {identifier for identifier in valid_metadata if identifier in self.components}
                missing_metadata = recorded - installed
                if missing_metadata:
                    metadata_diagnostics.append(
                        "state.json registra componentes sem metadados íntegros: "
                        + ", ".join(sorted(missing_metadata))
                    )
                elif installed != recorded:
                    recovered_state = state
            elif lexists(state_path):
                raise InstallerError(f"Estado da instalação inválido: {state_path}")
            elif metadata_diagnostics:
                metadata_diagnostics.append(
                    "state.json ausente e metadados parciais impedem inferência segura"
                )
            elif valid_metadata:
                recovered_state = self.infer_install_state()
            else:
                self.infer_install_state()
        except InstallerError as error:
            metadata_diagnostics.append(f"state.json: {error}")
        invalid_components: list[str] = []
        for identifier in valid_metadata:
            if identifier not in self.components:
                continue
            try:
                self.verify_component(identifier)
            except InstallerError:
                invalid_components.append(identifier)
        support_invalid = False
        try:
            player = self.play_support_player()
            support_invalid = bool(player.local_play_support_issues(player.available_local_games()))
        except InstallerError as error:
            metadata_diagnostics.append(f"play-support: {error}")
        permissions = self.runtime_permission_repairs(set(valid_metadata))
        package_order_invalid = False
        try:
            self.verify_qw_package_order()
        except InstallerError:
            package_order_invalid = True
        client_issues, client_diagnostics = self.client_repair_assessment()
        metadata_diagnostics.extend(client_diagnostics)
        return RepairAssessment(
            tuple(invalid_components), support_invalid, tuple(permissions),
            package_order_invalid, tuple(client_issues), tuple(metadata_diagnostics), recovered_state,
        )

    def repair_client_runtime(
        self,
        issue: ClientRepairIssue,
        *,
        mutation_results: list[MutationResult] | None = None,
    ) -> None:
        if issue.release is None:
            raise InstallerError(
                f"A versão registrada do ezQuake {issue.channel} não está disponível para reparo."
            )
        self.spec = issue.spec
        self.channel = issue.channel
        self.configure_release(issue.release)
        self.ensure_macos_ezquake_closed()
        with self.runtime_mutation_stage(
            ".x86qw-client-repair.",
            parent_managed=mutation_results is not None,
        ):
            self.prepare_cache()
            archive = self.ensure_archive()
            prepared = self.prepare_runtime(archive)
            assert self.stage is not None
            staged_receipt = self.stage / "ezquake-receipt"
            self.write_ezquake_receipt(staged_receipt)
            try:
                result = self.commit_runtime(prepared, staged_receipt)
            except RuntimeCommitPersistenceError as error:
                if mutation_results is not None:
                    mutation_results.append(error.result)
                raise
            if mutation_results is not None:
                mutation_results.append(result)
        console.success(
            f"ezQuake {issue.spec.label} {issue.channel} {issue.receipt['selection']} reparado."
        )

    def repair(
        self,
        *,
        dry_run: bool,
        plan_rows: list[UpdatePlanRow],
        allow_download: bool = True,
    ) -> bool:
        assessment = self.repair_plan()
        for diagnostic in assessment.metadata_diagnostics:
            category = repair_diagnostic_category(diagnostic)
            plan_rows.append(UpdatePlanRow(
                f"Diagnóstico {category}", "Metadados gerenciados", diagnostic,
                "preservar e reconciliar pelo bootstrap", "Inspecionar",
            ))
        if assessment.recovered_state is not None:
            plan_rows.append(UpdatePlanRow(
                "Metadados", "state.json", "ausente ou desatualizado",
                "reconstruído dos recibos íntegros", "Reparar",
            ))
        for issue in assessment.client_issues:
            plan_rows.append(UpdatePlanRow(
                "Cliente", f"ezQuake {issue.spec.label} {issue.channel}",
                issue.reason, issue.receipt["selection"], "Reparar",
            ))
        for identifier in assessment.invalid_components:
            _, _, receipt = self.validate_component_pair(identifier)
            assert receipt is not None
            package = self.component_package_record(identifier)
            plan_rows.append(UpdatePlanRow(
                "Componente", str(self.components[identifier]["label"]),
                str(receipt["selection"]), str(package["version"]), "Reparar", package_size(package),
            ))
        if assessment.support_invalid:
            plan_rows.append(UpdatePlanRow(
                "Gerado", "Suporte de execução dos mods", "ausente ou divergente",
                "derivado dos componentes instalados", "Reparar",
            ))
        for binary in assessment.permission_repairs:
            plan_rows.append(UpdatePlanRow(
                "Runtime", binary.name, "sem execução", "executável", "Reparar",
            ))
        if assessment.package_order_invalid:
            plan_rows.append(UpdatePlanRow(
                "Gerado", "Ordem de PK3", "ausente ou divergente", "catálogo instalado", "Reparar",
            ))
        if dry_run or not plan_rows:
            return bool(plan_rows)
        fatal_diagnostics = [
            diagnostic for diagnostic in assessment.metadata_diagnostics
            if repair_diagnostic_category(diagnostic) == "fatal"
        ]
        if fatal_diagnostics:
            raise InstallerError(
                "O repair encontrou metadados incompletos ou ambíguos e não alterou a instalação. "
                "Preserve os arquivos e reexecute o bootstrap install.sh para reconciliar o estado."
            )
        payload_clients = [issue for issue in assessment.client_issues if issue.mode == "payload"]
        unavailable_clients = [issue for issue in payload_clients if issue.release is None]
        if unavailable_clients or (
            not allow_download and (payload_clients or assessment.invalid_components)
        ):
            raise InstallerError(
                "O plano exige payload validado. A CLI instalada não baixa conteúdo durante repair; "
                "reexecute o bootstrap install.sh no mesmo destino."
            )
        with self.component_state_transaction() as mutation_results:
            for issue in assessment.client_issues:
                runtime = self.target / issue.spec.runtime(issue.channel)
                if issue.mode == "permission":
                    self.repair_runtime_permission(runtime, mutation_results)
                elif issue.mode == "macos-preparation":
                    self.repair_installed_macos_runtime(
                        issue.spec,
                        issue.channel,
                        issue.receipt_path,
                        issue.receipt,
                        mutation_results=mutation_results,
                    )
            for binary in assessment.permission_repairs:
                self.repair_runtime_permission(binary, mutation_results)
            for issue in payload_clients:
                self.repair_client_runtime(
                    issue, mutation_results=mutation_results,
                )
            if assessment.invalid_components:
                self.install_component_batch(
                    mutation_results,
                    list(assessment.invalid_components),
                    stage_prefix=".x86qw-repair.",
                )
            elif assessment.support_invalid:
                self.reconcile_play_support_transaction(
                    mutation_results=mutation_results,
                )
            if assessment.package_order_invalid:
                self.refresh_qw_package_order(mutation_results=mutation_results)
            state = assessment.recovered_state or self.load_install_state()
            self.write_install_state(
                str(state["profile"]), list(state["requested_components"]),
                known=list(state["known_components"]), capabilities=list(state["capabilities"]),
                mutation_results=mutation_results,
            )
            self.verify_installation()
        return True

    def report_nquake_startup_state(self, installed: list[str] | None = None) -> None:
        installed = self.installed_components() if installed is None else installed
        if "x86qw-client-bootstrap" not in installed:
            return
        config = self.target / "ezquake/configs/config.cfg"
        if not config.is_file():
            console.warning(f"Não foi possível localizar a configuração inicial nQuake: {config}")
            return
        content = config.read_text(encoding="utf-8", errors="replace")
        match = re.search(
            r'^\s*set\s+_nquake_first_startup\s+"?([01])"?',
            content,
            flags=re.MULTILINE,
        )
        if match is None:
            console.warning("Não foi possível determinar o estado de inicialização do nQuake.")
        elif match.group(1) == "0":
            console.success("Configurações nQuake carregadas pelo ezQuake.")
        else:
            console.info("Configurações nQuake instaladas e aguardando a primeira execução do ezQuake.")

    def metadata_component_ids(self) -> tuple[str, ...]:
        identifiers = list(LEGACY_COMPONENTS)
        identifiers.extend(("maps", "presets", "play-support", "package-order"))
        metadata = self.target / COMPONENT_METADATA_DIR
        if metadata.is_dir() and not metadata.is_symlink():
            identifiers.extend(
                entry.name
                for entry in metadata.iterdir()
                if COMPONENT_VERSION.fullmatch(entry.name)
            )
        legacy_metadata = self.target / METADATA_DIR
        if legacy_metadata.is_dir() and not legacy_metadata.is_symlink():
            reserved_receipts = {
                Path(NQUAKE_RECEIPT).name,
                Path(LEGACY_CLI_RECEIPT).name,
                *(
                    Path(spec.legacy_receipt(channel)).name
                    for spec in PLATFORMS.values()
                    for channel in ("stable", "nightly")
                ),
            }
            identifiers.extend(
                entry.name.removesuffix(".receipt")
                for entry in legacy_metadata.glob("*.receipt")
                if COMPONENT_VERSION.fullmatch(entry.name.removesuffix(".receipt"))
                and entry.name not in reserved_receipts
            )
        return tuple(dict.fromkeys(identifiers))

    def legacy_metadata_present(self) -> bool:
        if lexists(self.target / LEGACY_CLI_RECEIPT):
            return True
        for spec in PLATFORMS.values():
            for channel in ("stable", "nightly"):
                if lexists(self.target / spec.legacy_receipt(channel)):
                    return True
        metadata = self.target / METADATA_DIR
        return any(
            lexists(path)
            for component in self.metadata_component_ids()
            for path in self.component_pair_paths(component, metadata, legacy=True)
        )

    def _migrate_metadata_file_transaction(
        self,
        legacy: Path,
        canonical: Path,
        *,
        key: str,
        validate: Callable[[Path], object],
    ) -> MutationResult:
        assert self.stage is not None
        prepared = self.stage / f".{key}.next"
        shutil.copy2(legacy, prepared)
        validate(prepared)
        metadata = self.target / METADATA_DIR
        plan = MutationPlan(
            identifier=f"metadata-layout:{key}",
            summary=f"Migrar metadado {key} para o layout contextual",
            steps=(
                MutationStep(
                    key="topology",
                    description="Preparar o diretório contextual do metadado",
                    observe=lambda: (
                        self._mutation_path_observation(metadata),
                        self._mutation_path_observation(canonical.parent),
                    ),
                    apply=lambda: self._create_private_directory_chain(
                        canonical.parent,
                        root=metadata,
                        label=f"metadado {key}",
                    ),
                    rollback=lambda created: self._rollback_created_directory_chain(
                        created, label=f"metadado {key}",
                    ),
                ),
                MutationStep(
                    key="canonical",
                    description="Publicar o metadado no caminho contextual",
                    observe=lambda: (
                        self._mutation_path_observation(prepared),
                        self._mutation_path_observation(canonical),
                    ),
                    apply=lambda: self._apply_runtime_payload(prepared, canonical),
                    rollback=self._rollback_runtime_payload,
                ),
                MutationStep(
                    key="legacy",
                    description="Recolher o metadado legado",
                    observe=lambda: self._mutation_path_observation(legacy),
                    apply=lambda: self._apply_managed_path_removal(
                        legacy, label=f"metadado legado {key}",
                    ),
                    rollback=self._rollback_runtime_payload,
                ),
            ),
        )
        try:
            result = execute_mutation(prepare_mutation(plan))
            validate(canonical)
            return result
        except MutationApplyError as error:
            if isinstance(error.operation_error, InstallerError):
                raise error.operation_error
            raise InstallerError(f"Não foi possível migrar o metadado {key}.") from error
        except BaseException as error:
            if "result" in locals():
                try:
                    rollback_mutation(result)
                except BaseException as rollback_error:
                    raise InstallerError(
                        f"A validação de {key} falhou e o rollback ficou incompleto: "
                        f"{rollback_error}"
                    ) from error
            raise

    def migrate_metadata_layout(
        self, mutation_results: list[MutationResult] | None = None,
    ) -> bool:
        if not self.legacy_metadata_present():
            return False
        metadata = self.target / METADATA_DIR
        self.ensure_metadata_directory()
        created_stage = self.stage is None
        if created_stage:
            self._create_stage(".x86qw-metadata.")
        assert self.stage is not None
        completed: list[MutationResult] = []
        cleanup = True
        try:
            legacy_cli = self.target / LEGACY_CLI_RECEIPT
            if lexists(legacy_cli):
                self.cli_receipt_path()
                completed.append(self._migrate_metadata_file_transaction(
                    legacy_cli,
                    self.target / CLI_RECEIPT,
                    key="cli-receipt",
                    validate=self.validate_cli_receipt,
                ))

            for spec in PLATFORMS.values():
                for channel in ("stable", "nightly"):
                    legacy = self.target / spec.legacy_receipt(channel)
                    if not lexists(legacy):
                        continue
                    self.ezquake_receipt_path(spec, channel)
                    completed.append(self._migrate_metadata_file_transaction(
                        legacy,
                        self.target / spec.receipt(channel),
                        key=f"ezquake-{spec.key}-{channel}",
                        validate=lambda path, spec=spec, channel=channel: (
                            self.validate_ezquake_receipt(path, spec, channel)
                        ),
                    ))

            for component in self.metadata_component_ids():
                legacy = self.component_pair_paths(component, metadata, legacy=True)
                if not any(lexists(path) for path in legacy):
                    continue
                self.validate_component_pair(component)
                plan = MutationPlan(
                    identifier=f"metadata-layout:component:{component}",
                    summary=f"Migrar metadados do componente {component}",
                    steps=(MutationStep(
                        key="metadata",
                        description="Publicar recibo e inventário no layout contextual",
                        observe=lambda component=component: (
                            self._component_metadata_observation(component)
                        ),
                        apply=lambda component=component, legacy=legacy: (
                            self.commit_component_metadata(
                                component, legacy[1], legacy[0],
                            )
                        ),
                        rollback=self._rollback_component_metadata,
                    ),),
                )
                completed.append(execute_mutation(prepare_mutation(plan)))
        except BaseException as error:
            try:
                self.rollback_component_transactions(completed, error)
            except BaseException:
                cleanup = False
                raise
            if isinstance(error, InstallerError):
                raise
            raise InstallerError(
                "A reorganização dos metadados falhou e foi revertida."
            ) from error
        finally:
            if cleanup:
                if created_stage and mutation_results is None:
                    self.cleanup_stage()
        if mutation_results is not None:
            mutation_results.extend(completed)
        console.success("Metadados da instalação reorganizados por contexto.")
        return True

    def preflight_ezquake_receipts(self) -> None:
        for spec in PLATFORMS.values():
            for channel in ("stable", "nightly"):
                receipt_path = self.ezquake_receipt_path(spec, channel)
                if receipt_path is None:
                    continue
                self.validate_ezquake_receipt(receipt_path, spec, channel)

    def preflight_component_receipts(self) -> None:
        for component in self.metadata_component_ids():
            self.validate_component_pair(component)

    def _empty_metadata_topology(
        self, *, include_target: bool = False,
    ) -> tuple[tuple[Path, tuple[int, int], int], ...] | None:
        root = self.target / METADATA_DIR
        if not lexists(root):
            if not include_target or not lexists(self.target):
                return ()
            target_metadata = self.target.lstat()
            if (
                stat.S_ISLNK(target_metadata.st_mode)
                or not stat.S_ISDIR(target_metadata.st_mode)
                or any(self.target.iterdir())
            ):
                return None
            return ((
                self.target,
                (int(target_metadata.st_dev), int(target_metadata.st_ino)),
                host_adapter.path_mode(self.target),
            ),)
        metadata = root.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise InstallerError(f"Metadados da instalação inválidos: {root}")
        directories: list[tuple[Path, tuple[int, int], int]] = []
        for current, names, files in os.walk(root, topdown=False, followlinks=False):
            if files:
                return None
            directory = Path(current)
            for name in names:
                child = directory / name
                child_metadata = child.lstat()
                if stat.S_ISLNK(child_metadata.st_mode) or not stat.S_ISDIR(
                    child_metadata.st_mode
                ):
                    return None
            current_metadata = directory.lstat()
            if stat.S_ISLNK(current_metadata.st_mode) or not stat.S_ISDIR(
                current_metadata.st_mode
            ):
                return None
            directories.append((
                directory,
                (int(current_metadata.st_dev), int(current_metadata.st_ino)),
                host_adapter.path_mode(directory),
            ))
        if include_target:
            target_metadata = self.target.lstat()
            if (
                stat.S_ISLNK(target_metadata.st_mode)
                or not stat.S_ISDIR(target_metadata.st_mode)
                or tuple(self.target.iterdir()) != (root,)
            ):
                return None
            directories.append((
                self.target,
                (int(target_metadata.st_dev), int(target_metadata.st_ino)),
                host_adapter.path_mode(self.target),
            ))
        return tuple(directories)

    def _restore_metadata_topology(self, token: MetadataTopologyRollback) -> None:
        errors: list[str] = []
        for directory, _identity, mode in reversed(token.directories):
            if lexists(directory):
                if not directory.is_dir() or directory.is_symlink():
                    errors.append(f"caminho concorrente preservado em {directory}")
                continue
            try:
                directory.mkdir(mode=mode)
                host_adapter.apply_mode(directory, mode)
            except OSError as error:
                errors.append(f"{directory}: {error}")
        if errors:
            raise InstallerError(
                "Rollback da topologia de metadados ficou incompleto: "
                + "; ".join(errors)
            )

    def _apply_empty_metadata_topology(
        self, expected: tuple[tuple[Path, tuple[int, int], int], ...],
    ) -> MetadataTopologyRollback:
        removed: list[tuple[Path, tuple[int, int], int]] = []
        try:
            for directory, identity, mode in expected:
                metadata = directory.lstat()
                if (
                    stat.S_ISLNK(metadata.st_mode)
                    or not stat.S_ISDIR(metadata.st_mode)
                    or (int(metadata.st_dev), int(metadata.st_ino)) != identity
                ):
                    raise InstallerError(
                        f"Diretório de metadados mudou durante a remoção: {directory}"
                    )
                directory.rmdir()
                removed.append((directory, identity, mode))
            return MetadataTopologyRollback(tuple(removed))
        except BaseException as error:
            try:
                self._restore_metadata_topology(
                    MetadataTopologyRollback(tuple(removed)),
                )
            except BaseException as rollback_error:
                raise InstallerError(
                    "A limpeza vazia dos metadados falhou e o rollback ficou "
                    f"incompleto: {rollback_error}"
                ) from error
            raise

    def remove_empty_metadata_transaction(
        self, *, include_target: bool = False,
    ) -> MutationResult | None:
        expected = self._empty_metadata_topology(include_target=include_target)
        if not expected:
            return None
        identifier = (
            "purge-empty-installation"
            if include_target else "uninstall-empty-metadata"
        )
        plan = MutationPlan(
            identifier=identifier,
            summary=(
                "Remover somente a topologia vazia da instalação"
                if include_target else "Remover somente diretórios vazios de metadados"
            ),
            steps=(MutationStep(
                key="metadata-topology",
                description=(
                    f"Remover {self.target} quando vazio"
                    if include_target
                    else f"Remover {self.target / METADATA_DIR} quando vazio"
                ),
                observe=lambda: self._empty_metadata_topology(
                    include_target=include_target,
                ),
                apply=lambda: self._apply_empty_metadata_topology(expected),
                rollback=self._restore_metadata_topology,
            ),),
        )
        try:
            return execute_mutation(prepare_mutation(plan))
        except MutationRollbackError:
            raise
        except MutationApplyError as error:
            if isinstance(error.operation_error, InstallerError):
                raise error.operation_error
            raise InstallerError(
                "Os metadados vazios mudaram durante a desinstalação e foram preservados."
            ) from error

    def uninstall(self) -> None:
        self.require_managed_installation_identity("uninstall")
        metadata_names = [
            NQUAKE_INVENTORY, NQUAKE_RECEIPT,
            CLI_RECEIPT, LEGACY_CLI_RECEIPT, INSTALL_STATE,
        ]
        for component in self.metadata_component_ids():
            metadata_names.extend(self.component_metadata(component))
            metadata_names.extend(self.legacy_component_metadata(component))
        for spec in PLATFORMS.values():
            metadata_names.extend((
                spec.stable_receipt, spec.nightly_receipt,
                spec.legacy_receipt("stable"), spec.legacy_receipt("nightly"),
            ))
        if not any(lexists(self.target / name) for name in metadata_names):
            console.info(f"Nenhum runtime gerenciado está instalado em {self.target}.")
            self.remove_installed_cli()
            return
        self.preflight_ezquake_receipts()
        self.preflight_component_receipts()
        modular_nquake_present = bool(self.installed_components())
        present, entries, _ = self.validate_nquake_pair()
        for name, _ in entries:
            managed = self.target.joinpath(*PurePosixPath(name).parts)
            if lexists(managed) and (not managed.is_file() or managed.is_symlink()):
                raise InstallerError(f"managed path is not a regular file: {managed}")
        preserved = {}
        for relative in ("id1/pak0.pak", "id1/pak1.pak", "ezquake/configs/config.cfg"):
            path = self.target / relative
            if path.is_file():
                preserved[relative] = file_hash(path)
        removal_paths: list[Path] = []
        if present:
            for name, expected in entries:
                managed = self.target.joinpath(*PurePosixPath(name).parts)
                if lexists(managed):
                    if file_hash(managed) == expected:
                        removal_paths.append(managed)
                    else:
                        console.warning(f"Arquivo modificado preservado: {managed}")
        elif not modular_nquake_present:
            console.info("Os componentes x86QW não estão instalados; arquivos pessoais serão preservados.")
        for spec in PLATFORMS.values():
            for channel in ("stable", "nightly"):
                receipt_path = self.ezquake_receipt_path(spec, channel)
                if receipt_path is None:
                    continue
                self.validate_ezquake_receipt(receipt_path, spec, channel)
                removal_paths.extend((self.target / spec.runtime(channel), receipt_path))
        removal_paths.extend((
            self.target / NQUAKE_RECEIPT,
            self.target / NQUAKE_INVENTORY,
            self.target / INSTALL_STATE,
            *self._installed_cli_paths(),
        ))
        cli_present = any(
            lexists(path) for path in self._installed_cli_paths()
        )
        with self.component_state_transaction() as mutation_results:
            if self.stage is None:
                self._create_stage(".x86qw-uninstall.")
            result = self._remove_paths_transaction(
                (path for path in removal_paths if lexists(path)),
                identifier="uninstall-managed-roots",
                summary="Remover runtimes e metadados gerenciados",
            )
            if result is not None:
                mutation_results.append(result)
            for component in (
                "package-order", "play-support", "presets", "maps",
                *reversed(tuple(self.components)), *LEGACY_COMPONENTS,
            ):
                component_present, _, _ = self.validate_component_pair(component)
                if not component_present:
                    continue
                _, component_result = self.remove_component_transaction(component)
                mutation_results.append(component_result)
            for relative, expected in preserved.items():
                if file_hash(self.target / relative) != expected:
                    raise InstallerError(f"{relative} changed during uninstall")
            metadata_result = self.remove_empty_metadata_transaction()
            if metadata_result is not None:
                mutation_results.append(metadata_result)
        if cli_present:
            console.success("CLI permanente x86QW removida.")
        console.success(f"Componentes gerenciados removidos de {self.target}.")
        console.info("PAKs registrados e arquivos pessoais foram preservados.")

    def _installed_cli_paths(self) -> tuple[Path, ...]:
        paths = [
            self.target / METADATA_DIR / "cli",
            self.target / LEGACY_CLI_RECEIPT,
            *(
                self.target / name
                for name in host_adapter.cli_launcher_names_for_removal()
            ),
        ]
        return tuple(paths)

    def remove_installed_cli(
        self, mutation_results: list[MutationResult] | None = None,
    ) -> None:
        selected = tuple(path for path in self._installed_cli_paths() if lexists(path))
        if not selected:
            return
        owned_stage = mutation_results is None and self.stage is None
        cleanup_stage = True
        try:
            result = self._remove_paths_transaction(
                selected,
                identifier="uninstall-cli",
                summary="Remover a CLI instalada",
            )
            if result is not None and mutation_results is not None:
                mutation_results.append(result)
        except MutationRollbackError:
            cleanup_stage = False
            raise
        finally:
            if owned_stage and cleanup_stage:
                self.cleanup_stage()
                self.stage = None
        if selected:
            console.success("CLI permanente x86QW removida.")

    def purge(self, *, preserve_operation_lock: bool = False) -> None:
        cache_targets = self._owned_cache_targets(include_legacy=True)
        caches = [root for root, _ in cache_targets]
        candidates: list[Path] = []
        observations = dict(cache_targets)
        if lexists(self.target):
            before = quarantine.observe_quarantine_target(self.target)
            self.require_managed_installation_identity("uninstall --purge")
            after = quarantine.observe_quarantine_target(self.target)
            if before != after:
                raise InstallerError(
                    f"A instalação mudou durante a validação de ownership: {self.target}"
                )
            observations[self.target] = after
            current = Path.cwd().resolve()
            if current == self.target or self.target in current.parents:
                os.chdir(self.target.parent)
            if preserve_operation_lock:
                metadata = self.target / METADATA_DIR
                sessions = metadata / "sessions"
                for child in tuple(self.target.iterdir()):
                    if child != metadata:
                        candidates.append(child)
                if metadata.is_dir() and not metadata.is_symlink():
                    for child in tuple(metadata.iterdir()):
                        if child != sessions:
                            candidates.append(child)
                if sessions.is_dir() and not sessions.is_symlink():
                    for child in tuple(sessions.iterdir()):
                        if child.name != "active.lock":
                            candidates.append(child)
            else:
                candidates.append(self.target)
        else:
            console.info(f"Nenhum diretório de instalação foi encontrado em {self.target}.")
        candidates.extend(caches)
        selected = self._minimal_removal_paths(candidates)
        if selected:
            plan = MutationPlan(
                identifier="purge-installation",
                summary="Recolher instalação e caches em quarantines reversíveis",
                steps=tuple(
                    MutationStep(
                        key=f"domain-{index}",
                        description=f"Recolher {path}",
                        observe=lambda path=path: (
                            quarantine.observe_quarantine_target(path)
                        ),
                        apply=lambda path=path, expected=observations.get(path): (
                            quarantine.apply_quarantine_removal(
                                path, expected_observation=expected,
                                allow_non_regular=True,
                            )
                        ),
                        rollback=quarantine.rollback_quarantine,
                        finalize=quarantine.finalize_quarantine,
                    )
                    for index, path in enumerate(selected, 1)
                ),
            )
            try:
                result = execute_mutation(prepare_mutation(plan))
            except MutationRollbackError:
                raise
            except MutationApplyError as error:
                if isinstance(error.operation_error, InstallerError):
                    raise error.operation_error
                raise InstallerError(
                    "A remoção total falhou; instalação e caches anteriores foram restaurados."
                ) from error
            finalize_mutation(result)
        if preserve_operation_lock and lexists(self.target):
            console.info(
                "Conteúdo da instalação removido; o diretório do lock será "
                "finalizado ao encerrar a operação."
            )
        elif not lexists(self.target):
            console.success(f"Diretório da instalação removido: {self.target}")
        for root in caches:
            if not lexists(root):
                console.success(f"Cache removido: {root}")
        if not caches:
            console.info(f"Nenhum cache do instalador foi encontrado em {self.cache_root}.")
        console.success("Remoção total concluída; nenhum dado gerenciado pelo x86QW foi preservado.")

    def install(
        self,
        *,
        platform: str | None = None,
        channel: str | None = None,
        release: str | None = None,
        profile: str | None = None,
        non_interactive: bool = False,
        native_profile: str | None = None,
        before_mutation: Callable[[], None] | None = None,
        mutation_results: list[MutationResult] | None = None,
    ) -> None:
        if non_interactive:
            missing = [
                name for name, value in (
                ("--platform", platform),
                ("--channel", channel),
                ("--release", release),
                ("--profile", profile if profile is not None else native_profile),
                ) if value is None
            ]
            if missing:
                raise InstallerError(
                    "Instalação não interativa exige as opções: " + ", ".join(missing)
                )
        self._non_interactive_install = non_interactive
        self._requested_channel = channel
        self._requested_release = release
        self._requested_profile = profile
        self.select_platform(platform)
        if non_interactive:
            self.choose_channel(channel)
            self.choose_release(release)
        else:
            # Keep the historical zero-argument interactive seam.  Apart
            # from preserving subclasses and test doubles, this ensures
            # that ordinary installs do not accidentally enter the
            # non-interactive selection contract.
            self.choose_channel()
            self.choose_release()
        if native_profile is not None:
            if native_profile != "complete" or self._native_candidate_root() is None:
                raise InstallerError(
                    "--native-profile exige o perfil complete em um candidato nativo explícito."
                )
            selected = self.select_components_profile(native_profile)
        else:
            selected = self.choose_install_content()
        assert self.spec is not None
        plan_rows = [UpdatePlanRow(
            "Cliente", f"ezQuake {self.spec.label} {self.channel}",
            "não instalado", self.selected_version, "Instalar",
            self.app_expected_size or None,
        )]
        for identifier in selected or ():
            package = self.component_package_record(identifier)
            plan_rows.append(UpdatePlanRow(
                "Componente", str(self.components[identifier]["label"]),
                "não instalado", str(package["version"]), "Instalar",
                package_size(package),
            ))
        plan_summary = console.update_plan(plan_rows, "install")
        if not self.confirm_update_plan(
            "install", assume_yes=self._non_interactive_install,
            summary=plan_summary,
        ):
            return
        if before_mutation is not None:
            before_mutation()
        reset_macos_game_directory = self.macos_game_directory_reset_required()
        self.ensure_macos_ezquake_closed()
        self.check_runtime_destination_ownership()
        installation_results = mutation_results if mutation_results is not None else []
        try:
            topology_result = self.prepare_install_target()
            if topology_result is not None:
                installation_results.append(topology_result)
            self.reject_target_symlinks()
            self._create_stage(".quake-install.")
            pak_result = self.provision_install_target()
            if pak_result is not None:
                installation_results.append(pak_result)
            self.check_paks()
            pak0_before = file_hash(self.target / "id1/pak0.pak")
            pak1_before = file_hash(self.target / "id1/pak1.pak")
            self.prepare_cache()
            console.heading(
                f"Instalando ezQuake {self.spec.label} {self.channel} "
                f"{self.selected_version}"
            )
            archive = self.ensure_archive()
            assert self.spec is not None and self.stage is not None
            console.activity(1, 1)
            prepared = self.prepare_runtime(archive)
            staged_receipt = self.stage / "ezquake-receipt"
            self.write_ezquake_receipt(staged_receipt)
            self.ensure_metadata_directory()
            installation_results.append(
                self.commit_runtime(prepared, staged_receipt)
            )
            if reset_macos_game_directory:
                preference_result = self.reset_macos_game_directory()
                if preference_result is not None:
                    installation_results.append(preference_result)
            console.success(f"ezQuake {self.selected_version} instalado")
            if reset_macos_game_directory:
                console.info(f"Na primeira abertura, selecione este diretório quando o macOS solicitar: {self.target}")
            if native_profile is not None:
                installation_results.extend(self.install_component_phase(selected=selected))
            elif selected is not None:
                installation_results.extend(self.install_component_phase(selected=selected))
            else:
                console.warning(
                    "Somente cliente: Jogar recusará até instalar ao menos KTX."
                )
                self.write_install_state(
                    "none", [], mutation_results=installation_results,
                )
            if (
                file_hash(self.target / "id1/pak0.pak") != pak0_before
                or file_hash(self.target / "id1/pak1.pak") != pak1_before
            ):
                raise InstallerError(
                    "Um PAK registrado foi alterado durante a instalação; "
                    "a operação foi interrompida."
                )
            self.verify_installation()
        except BaseException as error:
            if (
                mutation_results is None
                and (not isinstance(error, PersistenceError) or not error.committed)
            ):
                self.rollback_component_transactions(installation_results, error)
            raise
        console.section("Resumo")
        print(f"  Sistema: {self.spec.label}")
        print(f"  Canal:   {self.channel}")
        print(f"  Versão:  {self.selected_version}")
        print(f"  Destino: {self.target}")
        launcher = public_launcher_name()
        play_command = f"{launcher} play"
        if self.installed_components():
            console.success("Instalação completa e pronta para uso.")
            print(f"  Jogar agora: {play_command}")
        else:
            console.success(f"ezQuake pronto em {self.target / self.spec.runtime(self.channel)}")
            console.warning("Somente cliente: Jogar recusará até instalar ao menos KTX.")
            print(f"  Jogar agora fica indisponível até adicionar componentes com {launcher}.")

    @staticmethod
    def release_is_newer(candidate: str, installed: str, channel: str) -> bool:
        if channel == "stable":
            return parse_semver(candidate) > parse_semver(installed)
        return candidate > installed

    def update_runtime(
        self,
        spec: PlatformSpec,
        channel: str,
        receipt: dict[str, str],
        *,
        dry_run: bool,
        preview: bool = False,
        plan_rows: list[UpdatePlanRow] | None = None,
        mutation_results: list[MutationResult] | None = None,
    ) -> bool:
        self.spec = spec
        self.channel = channel
        selected = self.latest_release()
        available = selected[0]
        installed = receipt["selection"]
        runtime = self.target / spec.runtime(channel)
        macos_action = (
            self.macos_runtime_action(
                runtime, channel, receipt["artifact_sha256"], receipt["binary_sha256"],
            )
            if spec.key == "macos" else None
        )
        restore_stable_bundle = macos_action == "restore-upstream"
        prepare_nightly_bundle = macos_action == "prepare-nightly"
        if available == installed and not restore_stable_bundle and not prepare_nightly_bundle:
            return False
        if available == installed and prepare_nightly_bundle:
            if dry_run:
                if plan_rows is not None:
                    plan_rows.append(UpdatePlanRow(
                        "Cliente", f"ezQuake {spec.label} {channel}",
                        "área segura", "tela inteira", "Reparar",
                    ))
                return True
            receipt_path = self.ezquake_receipt_path(spec, channel)
            assert receipt_path is not None
            self.repair_installed_macos_runtime(
                spec,
                channel,
                receipt_path,
                receipt,
                mutation_results=mutation_results,
            )
            return True
        if available != installed and not self.release_is_newer(available, installed, channel):
            console.warning(
                f"ezQuake {spec.label} {channel} instalado ({installed}) é mais novo que o catálogo ({available}); preservado."
            )
            return False

        self.configure_release(selected)

        if dry_run:
            if plan_rows is not None:
                plan_rows.append(UpdatePlanRow(
                    "Cliente", f"ezQuake {spec.label} {channel}",
                    "bundle re-assinado localmente" if restore_stable_bundle else installed,
                    "bundle upstream integral" if restore_stable_bundle else available,
                    "Restaurar" if restore_stable_bundle else "Atualizar",
                    self.app_expected_size or None,
                ))
            return True

        self.ensure_macos_ezquake_closed()
        self.check_runtime_destination_ownership()
        with self.runtime_mutation_stage(
            ".x86qw-runtime-update.",
            parent_managed=mutation_results is not None,
        ):
            self.prepare_cache()
            operation_label = "Restaurando" if restore_stable_bundle else "Atualizando"
            console.heading(
                f"{operation_label} ezQuake {spec.label} {channel} "
                f"{installed} -> {available}"
            )
            archive = self.ensure_archive()
            console.activity(1, 1)
            prepared = self.prepare_runtime(archive)
            assert self.stage is not None
            staged_receipt = self.stage / "ezquake-receipt"
            self.write_ezquake_receipt(staged_receipt)
            try:
                result = self.commit_runtime(prepared, staged_receipt)
            except RuntimeCommitPersistenceError as error:
                if mutation_results is not None:
                    mutation_results.append(error.result)
                raise
            if mutation_results is not None:
                mutation_results.append(result)
        if restore_stable_bundle:
            console.success("Bundle upstream integral do ezQuake stable restaurado.")
        else:
            console.success(f"ezQuake atualizado para {available}")
        return True

    def outdated_installed_components(self) -> list[str]:
        outdated = []
        for identifier in self.installed_components():
            _, _, receipt = self.validate_component_pair(identifier)
            assert receipt is not None
            available = str(self.component_package_record(identifier)["version"])
            installed = str(receipt["selection"])
            if installed == available:
                continue
            installed_overlay = re.fullmatch(r"(.+)\+x86qw\.(\d+)", installed)
            available_overlay = re.fullmatch(r"(.+)\+x86qw\.(\d+)", available)
            if (
                installed_overlay is not None
                and available_overlay is not None
                and installed_overlay.group(1) == available_overlay.group(1)
                and int(installed_overlay.group(2)) > int(available_overlay.group(2))
            ):
                console.warning(
                    f"{self.components[identifier]['label']} instalado ({installed}) é mais novo "
                    f"que o catálogo ({available}); preservado."
                )
                continue
            outdated.append(identifier)
        return outdated

    def update(
        self, *, dry_run: bool = False, profile_upgrade: bool = False, preview: bool = False,
        plan_rows: list[UpdatePlanRow] | None = None,
        mutation_results: list[MutationResult] | None = None,
    ) -> bool:
        self.preflight_ezquake_receipts()
        self.preflight_component_receipts()
        layout_change = self.legacy_metadata_present()
        if dry_run and layout_change and plan_rows is not None:
            plan_rows.append(UpdatePlanRow(
                "Sistema", "Metadados da instalação", "formato plano", "por contexto", "Reorganizar",
            ))
        self.check_paks()
        state_path = self.target / INSTALL_STATE
        persisted_state: dict[str, object] | None = None
        if state_path.is_file() and not state_path.is_symlink():
            persisted_state = self.read_install_state_document(state_path)
        state = self.current_install_state(
            self.load_install_state()
        )
        state_change = persisted_state != state
        if dry_run and state_change and plan_rows is not None:
            plan_rows.append(UpdatePlanRow(
                "Sistema", "Estado da instalação",
                "ausente ou formato histórico", "formato 2", "Migrar",
            ))
        legacy_replacements = self.installed_legacy_component_replacements()
        legacy_removals = self.installed_legacy_component_removals()
        known = set(state["known_components"])
        newly_published = [identifier for identifier in self.components if identifier not in known]
        if newly_published and not dry_run:
            console.info("Novos componentes publicados: " + ", ".join(newly_published))
        runtimes: list[tuple[PlatformSpec, str, dict[str, str]]] = []
        for spec in PLATFORMS.values():
            for channel in ("stable", "nightly"):
                receipt_path = self.ezquake_receipt_path(spec, channel)
                if receipt_path is None:
                    continue
                receipt = self.validate_ezquake_receipt(receipt_path, spec, channel)
                self.check_runtime(spec, channel, receipt)
                runtimes.append((spec, channel, receipt))
        if not runtimes:
            raise InstallerError(
                f"Nenhum ezQuake gerenciado foi encontrado em {self.target}. Use install.sh para instalar o x86QW."
            )

        pak_hashes = {
            name: file_hash(self.target / "id1" / name)
            for name in ("pak0.pak", "pak1.pak")
        }
        changed = layout_change or state_change
        with self.component_state_transaction(mutation_results) as mutation_results:
            if not dry_run and layout_change:
                self.migrate_metadata_layout(mutation_results)
            for spec, channel, receipt in runtimes:
                changed = self.update_runtime(
                    spec, channel, receipt,
                    dry_run=dry_run,
                    preview=preview,
                    plan_rows=plan_rows,
                    mutation_results=mutation_results,
                ) or changed

            if legacy_removals:
                if dry_run:
                    for identifier in legacy_removals:
                        _, _, receipt = self.validate_component_pair(identifier)
                        assert receipt is not None
                        if plan_rows is not None:
                            plan_rows.append(UpdatePlanRow(
                                "Componente", "Sons redundantes nQuake",
                                str(receipt["selection"]), "incorporado ao KTX", "Remover",
                            ))
                else:
                    for identifier in legacy_removals:
                        if self.stage is None:
                            self._create_stage(".x86qw-components-remove.")
                        removed, removal_result = self.remove_component_transaction(
                            identifier,
                        )
                        mutation_results.append(removal_result)
                        console.success(
                            f"Componente obsoleto {identifier} removido ({file_count(removed)}); "
                            f"{LEGACY_COMPONENT_REMOVALS[identifier]}."
                        )
                changed = True
            if legacy_replacements:
                replacements = list(dict.fromkeys(legacy_replacements.values()))
                if dry_run:
                    for legacy, replacement in legacy_replacements.items():
                        _, _, receipt = self.validate_component_pair(legacy)
                        assert receipt is not None
                        package = self.component_package_record(replacement)
                        if plan_rows is not None:
                            plan_rows.append(UpdatePlanRow(
                                "Componente", str(self.components[replacement]["label"]),
                                str(receipt["selection"]), str(package["version"]), "Migrar",
                                package_size(package),
                            ))
                else:
                    self.install_component_batch(
                        mutation_results,
                        replacements,
                        stage_prefix=".x86qw-components-migrate.",
                    )
                changed = True
            outdated = self.outdated_installed_components()
            if outdated:
                if dry_run:
                    for identifier in outdated:
                        _, _, receipt = self.validate_component_pair(identifier)
                        assert receipt is not None
                        package = self.component_package_record(identifier)
                        available = str(package["version"])
                        if plan_rows is not None:
                            plan_rows.append(UpdatePlanRow(
                                "Componente", str(self.components[identifier]["label"]),
                                str(receipt["selection"]), available, "Atualizar", package_size(package),
                            ))
                else:
                    self.install_component_batch(
                        mutation_results,
                        outdated,
                        stage_prefix=".x86qw-components-update.",
                    )
                changed = True
            elif not dry_run and not self.installed_components():
                console.info("Nenhum componente x86QW está instalado; nenhum componente novo foi adicionado.")

            support_changed, _ = self.reconcile_play_support_transaction(
                dry_run=dry_run, plan_rows=plan_rows,
                mutation_results=mutation_results,
            )
            changed = support_changed or changed

            desired = self.desired_components(state)
            installed_or_planned = {
                *self.installed_components(), *legacy_replacements.values(),
            }
            missing_from_profile = [identifier for identifier in desired if identifier not in installed_or_planned]
            if missing_from_profile and not dry_run:
                suffix = (
                    ". Elas serão incorporadas nesta operação."
                    if profile_upgrade
                    else ". Use ./x86qw.sh upgrade para incorporá-las."
                )
                console.info(
                    "Novidades disponíveis para o perfil " + str(state["profile"]) + ": "
                    + ", ".join(missing_from_profile) + suffix
                )

            for name, expected in pak_hashes.items():
                if file_hash(self.target / "id1" / name) != expected:
                    raise InstallerError(f"O PAK registrado {name} foi alterado durante a atualização.")
            if not dry_run:
                state = self.write_install_state(
                    str(state["profile"]), list(state["requested_components"]), known=list(self.components),
                    capabilities=list(state["capabilities"]),
                    mutation_results=mutation_results,
                )
            if not dry_run and changed and not profile_upgrade:
                self.verify_installation()
        if not dry_run and changed and not profile_upgrade:
            console.success("Conteúdo instalado atualizado e validado.")
        return changed

    def upgrade(
        self, *, dry_run: bool = False, preview: bool = False,
        plan_rows: list[UpdatePlanRow] | None = None,
        mutation_results: list[MutationResult] | None = None,
    ) -> bool:
        with self.component_state_transaction(mutation_results) as component_results:
            changed = self.update(
                dry_run=dry_run,
                profile_upgrade=True,
                preview=preview,
                plan_rows=plan_rows,
                mutation_results=component_results,
            )
            state = self.current_install_state(
                self.load_install_state()
            )
            desired = self.desired_components(state)
            installed = self.installed_components()
            legacy_replacements = self.installed_legacy_component_replacements()
            installed_or_planned = {*installed, *legacy_replacements.values()}
            missing = [
                identifier for identifier in desired
                if identifier not in installed_or_planned
            ]
            extras = [identifier for identifier in installed if identifier not in desired]

            if not dry_run:
                console.heading(f"Convergindo perfil {state['profile']}")
            if extras and not dry_run:
                console.warning(
                    "Componentes fora do perfil foram preservados: "
                    + ", ".join(extras) + "."
                )
            if not missing:
                pass
            elif dry_run:
                for identifier in missing:
                    package = self.component_package_record(identifier)
                    if plan_rows is not None:
                        plan_rows.append(UpdatePlanRow(
                            "Componente", str(self.components[identifier]["label"]),
                            "não instalado", str(package["version"]), "Adicionar", package_size(package),
                        ))
                changed = True
            else:
                console.info("Novos componentes do perfil: " + ", ".join(missing))
                self.install_component_batch(
                    component_results,
                    missing,
                    stage_prefix=".x86qw-profile-upgrade.",
                )
                changed = True

            if not dry_run:
                self.write_install_state(
                    str(state["profile"]), list(state["requested_components"]), known=list(self.components),
                    capabilities=list(state["capabilities"]),
                    mutation_results=component_results,
                )
            if not dry_run and changed:
                self.verify_installation()
        if not dry_run and changed:
            console.success("Distribuição atualizada conforme o perfil da instalação.")
        return changed

    def _create_stage(self, prefix: str) -> Path:
        """Create one private staging root and bind cleanup to its identity."""
        stage_root = self.target / METADATA_DIR / "staging"
        created_roots: list[tuple[Path, tuple[int, int]]] = []
        try:
            for directory in (stage_root.parent, stage_root):
                existed = lexists(directory)
                private_fs.ensure_private_directory(directory)
                if not existed:
                    root_metadata = directory.lstat()
                    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(
                        root_metadata.st_mode
                    ):
                        raise private_fs.PrivateFilesystemError(
                            f"Raiz privada de staging inválida: {directory}"
                        )
                    created_roots.append((
                        directory,
                        (int(root_metadata.st_dev), int(root_metadata.st_ino)),
                    ))
            stage = private_fs.private_mkdtemp(directory=stage_root, prefix=prefix)
            metadata = stage.lstat()
        except OSError as error:
            try:
                self._cleanup_stage_roots(tuple(created_roots))
            except InstallerError as cleanup_error:
                raise InstallerError(
                    "Não foi possível criar nem reconciliar a área privada de staging: "
                    f"{cleanup_error}"
                ) from error
            raise InstallerError(
                f"Não foi possível criar a área privada de staging em {self.target}: {error}"
            ) from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise InstallerError(f"Área privada de staging inválida: {stage}")
        self.stage = stage
        self._stage_identity = (int(metadata.st_dev), int(metadata.st_ino))
        self._stage_created_roots = tuple(created_roots)
        self._stage_lease = None
        return stage

    def _create_handoff_stage(self, prefix: str) -> Path:
        """Create a guarded private stage outside the installation target."""
        parent = Path(tempfile.gettempdir()).resolve()
        try:
            stage = private_fs.private_mkdtemp(directory=parent, prefix=prefix)
            metadata = stage.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise private_fs.PrivateFilesystemError(
                    f"Área privada de handoff inválida: {stage}"
                )
            lease = private_fs.hold_private_path(stage, directory=True)
        except OSError as error:
            if "stage" in locals() and lexists(stage):
                try:
                    stage.rmdir()
                except OSError:
                    pass
            raise InstallerError(
                f"Não foi possível criar a área privada de handoff fora do destino: {error}"
            ) from error
        self.stage = stage
        self._stage_identity = (int(metadata.st_dev), int(metadata.st_ino))
        self._stage_created_roots = ()
        self._stage_lease = lease
        return stage

    def _cleanup_stage_roots(
        self, roots: tuple[tuple[Path, tuple[int, int]], ...],
    ) -> None:
        for directory, expected_identity in reversed(roots):
            if not lexists(directory):
                continue
            metadata = directory.lstat()
            identity = (int(metadata.st_dev), int(metadata.st_ino))
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISDIR(metadata.st_mode)
                or identity != expected_identity
            ):
                raise InstallerError(
                    f"Raiz de staging mudou de identidade e foi preservada: {directory}"
                )
            try:
                private_fs.validate_private_directory(directory)
                directory.rmdir()
            except OSError as error:
                if error.errno in {errno.ENOTEMPTY, errno.EEXIST}:
                    continue
                raise InstallerError(
                    f"Não foi possível remover a raiz vazia de staging {directory}: {error}"
                ) from error

    def cleanup_stage(self) -> None:
        stage = self.stage
        if stage is None:
            self._stage_identity = None
            lease = self._stage_lease
            self._stage_lease = None
            if lease is not None:
                lease.close()
            roots = self._stage_created_roots
            self._cleanup_stage_roots(roots)
            self._stage_created_roots = ()
            return
        if not lexists(stage):
            self.stage = None
            self._stage_identity = None
            lease = self._stage_lease
            self._stage_lease = None
            if lease is not None:
                lease.close()
            roots = self._stage_created_roots
            self._cleanup_stage_roots(roots)
            self._stage_created_roots = ()
            return
        metadata = stage.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise InstallerError(f"Área de staging mudou de tipo e foi preservada: {stage}")
        identity = (int(metadata.st_dev), int(metadata.st_ino))
        if self._stage_identity is not None and identity != self._stage_identity:
            raise InstallerError(f"Área de staging mudou de identidade e foi preservada: {stage}")
        if self._stage_identity is not None:
            try:
                private_fs.validate_private_directory(stage)
            except OSError as error:
                raise InstallerError(
                    f"Área de staging deixou de ser privada e foi preservada: {stage}"
                ) from error
        lease = self._stage_lease
        if lease is not None:
            cleanup_error: BaseException | None = None
            try:
                private_fs.validate_private_directory(stage)
                for child in tuple(stage.iterdir()):
                    remove_path(child)
            except BaseException as error:
                cleanup_error = error
            finally:
                self._stage_lease = None
                try:
                    lease.close()
                except BaseException as error:
                    if cleanup_error is None:
                        cleanup_error = error
            if cleanup_error is not None:
                raise InstallerError(
                    f"Área privada de handoff foi preservada após falha de limpeza: {stage}"
                ) from cleanup_error
            try:
                stage.rmdir()
            except OSError as error:
                raise InstallerError(
                    f"Área privada de handoff mudou após a liberação e foi preservada: {stage}"
                ) from error
        else:
            remove_path(stage, identity[0])
        self.stage = None
        self._stage_identity = None
        roots = self._stage_created_roots
        self._cleanup_stage_roots(roots)
        self._stage_created_roots = ()

    def install_online_cli(
        self, *, mutation_results: list[MutationResult] | None = None,
    ) -> None:
        if not self.online_only:
            return
        identity = self.installer_bundle_identity()
        cli_version = str(identity["version"])
        launchers = (
            ("x86qw.sh", 0o755),
            ("x86qw.cmd", 0o644),
        )
        rendered_launchers: dict[str, tuple[str, int]] = {}
        for name, mode in launchers:
            source = self.project_root / name
            if not source.is_file() and ZIPAPP_PATH is None:
                source = self.project_root / "dist/installer/bin" / name
            if not source.is_file() or source.is_symlink():
                raise InstallerError(f"Launcher público ausente ou inválido: {source}")
            try:
                template = source.read_text(encoding="utf-8")
                rendered = python_runtime.render_launcher(name, template, sys.executable)
            except (OSError, UnicodeDecodeError, ValueError) as error:
                raise InstallerError(f"Launcher público inválido: {source}") from error
            rendered_launchers[name] = (rendered, mode)

        application = ZIPAPP_PATH or self.project_root / CLI_ARCHIVE_NAME
        if not application.is_file() or application.is_symlink():
            raise InstallerError(f"Aplicativo da CLI pública ausente ou inválido: {application}")
        embedded = read_zipapp_json(
            application, INSTALLER_BUNDLE_METADATA, "Identidade interna da CLI",
        )
        if embedded != identity:
            raise InstallerError("A identidade do aplicativo x86QW diverge do recibo do bundle.")
        application_digest = file_hash(application)

        metadata_root = self.target / METADATA_DIR
        cli_root = metadata_root / "cli"
        if lexists(cli_root) and (not cli_root.is_dir() or cli_root.is_symlink()):
            raise InstallerError(f"Diretório da CLI instalada inválido: {cli_root}")
        self.cli_receipt_path()
        for name, _ in launchers:
            destination = self.target / name
            if lexists(destination) and (
                not destination.is_file() or destination.is_symlink()
            ):
                raise InstallerError(f"Launcher instalado inválido: {destination}")

        parent_managed = mutation_results is not None or self.stage is not None
        with self.runtime_mutation_stage(
            ".x86qw-cli.", parent_managed=parent_managed,
        ):
            assert self.stage is not None
            prepared_cli = self.stage / "cli"
            prepared_cli.mkdir()
            prepared_application = prepared_cli / CLI_ARCHIVE_NAME
            shutil.copyfile(application, prepared_application)
            host_adapter.apply_mode(prepared_application, 0o644)
            if file_hash(prepared_application) != application_digest:
                raise InstallerError("A cópia preparada da CLI diverge do bundle validado.")
            self.write_cli_receipt_record(prepared_cli / "receipt", identity)

            prepared_launchers: dict[str, Path] = {}
            launcher_stage = self.stage / "launchers"
            launcher_stage.mkdir()
            for name, (rendered, mode) in rendered_launchers.items():
                prepared = launcher_stage / name
                prepared.write_text(rendered, encoding="utf-8", newline="\n")
                host_adapter.apply_mode(prepared, mode)
                prepared_launchers[name] = prepared

            legacy_receipt = self.target / LEGACY_CLI_RECEIPT
            steps = [
                MutationStep(
                    key="cli",
                    description="Publicar o aplicativo e o recibo da CLI",
                    observe=lambda: (
                        self._mutation_path_observation(prepared_cli),
                        self._mutation_path_observation(cli_root),
                    ),
                    apply=lambda: self._apply_runtime_payload(prepared_cli, cli_root),
                    rollback=self._rollback_runtime_payload,
                ),
                MutationStep(
                    key="legacy-receipt",
                    description="Remover o recibo legado da CLI",
                    observe=lambda: self._mutation_path_observation(legacy_receipt),
                    apply=lambda: self._apply_managed_path_removal(
                        legacy_receipt, label="recibo legado da CLI",
                    ),
                    rollback=self._rollback_runtime_payload,
                ),
            ]
            for name, _ in launchers:
                prepared = prepared_launchers[name]
                destination = self.target / name
                steps.append(MutationStep(
                    key=f"launcher:{name}",
                    description=f"Publicar o launcher {name}",
                    observe=lambda prepared=prepared, destination=destination: (
                        self._mutation_path_observation(prepared),
                        self._mutation_path_observation(destination),
                    ),
                    apply=lambda prepared=prepared, destination=destination: (
                        self._apply_runtime_payload(prepared, destination)
                    ),
                    rollback=self._rollback_runtime_payload,
                ))
            plan = MutationPlan(
                identifier=f"cli:{cli_version}",
                summary=f"Publicar a CLI x86QW {cli_version}",
                steps=tuple(steps),
            )
            try:
                result = execute_mutation(prepare_mutation(plan))
            except MutationApplyError as error:
                if isinstance(error.operation_error, InstallerError):
                    raise error.operation_error
                raise InstallerError(
                    "Não foi possível publicar a CLI, o recibo e os launchers como uma geração única."
                ) from error
            try:
                installed_identity = self.validate_cli_receipt(cli_root / "receipt")
                if any(
                    installed_identity.get(field) != identity.get(field)
                    for field in ("format", "project", "version")
                ):
                    raise InstallerError("O recibo instalado da CLI diverge do bundle publicado.")
                if file_hash(cli_root / CLI_ARCHIVE_NAME) != application_digest:
                    raise InstallerError("O aplicativo instalado da CLI diverge do bundle publicado.")
                for name, (rendered, _) in rendered_launchers.items():
                    if (self.target / name).read_text(encoding="utf-8") != rendered:
                        raise InstallerError(f"O launcher instalado diverge do modelo: {name}")
            except BaseException as error:
                try:
                    rollback_mutation(result)
                except BaseException as rollback_error:
                    raise InstallerError(
                        f"A validação da CLI falhou e o rollback ficou incompleto: {rollback_error}"
                    ) from error
                raise

        if mutation_results is not None:
            mutation_results.append(result)

        shell_launcher = self.target / "x86qw.sh"
        console.success(f"CLI permanente instalada: {shell_launcher} (versão {cli_version})")
        console.info(OWNER_ONLY_FIRST_RUN)


def platform_argument(value: str) -> str:
    if value not in PLATFORMS:
        available = ", ".join(PLATFORMS)
        raise argparse.ArgumentTypeError(
            f"plataforma desconhecida: {value}; disponíveis: {available}"
        )
    return value


def parse_arguments(arguments: list[str], project_root: Path) -> argparse.Namespace:
    public_cli = ZIPAPP_PATH is not None
    version = application_version()
    parser = FriendlyArgumentParser(
        prog="x86qw" if public_cli else "dist/installer/bin/manager.py",
        description=(
            f"x86QW {version}\n"
            "Instala e mantém uma coleção QuakeWorld moderna em um diretório autocontido."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemplo: x86qw update"
            if public_cli
            else "Exemplo: ./dist/installer/bin/manager.py install ./quake-world"
        ),
        add_help=False,
    )
    parser._positionals.title = "argumentos"
    parser._optionals.title = "opções"
    parser.add_argument("-h", "--help", action="help", help="mostra esta ajuda e encerra")
    parser.add_argument(
        "--version", action="version", version=f"x86QW {version}",
        help="mostra a versão da CLI e encerra",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="mostra URLs, comandos, hashes e caminhos técnicos")
    parser.add_argument("--no-color", action="store_true", help="desativa cores mesmo em um terminal interativo")
    parser.add_argument(
        "--platform", type=platform_argument, metavar="SO",
        help="instala um cliente para macos, linux ou windows em vez do SO detectado",
    )
    parser.add_argument(
        "--non-interactive", action="store_true",
        help="no install, exige todas as seleções por argumento e nunca abre prompts",
    )
    parser.add_argument(
        "--channel", choices=("stable", "nightly"),
        help="no install não interativo, seleciona o canal do cliente",
    )
    parser.add_argument(
        "--release",
        help="no install não interativo, usa latest ou uma versão exata do cliente",
    )
    parser.add_argument(
        "--profile", choices=("essential", "recommended", "complete"),
        help="no install não interativo, seleciona o perfil de componentes",
    )
    parser.add_argument(
        "--native-profile", choices=("complete",),
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--online-only", action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--installed-cli", action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--skip-cli-update", action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="simula update, upgrade, repair ou migrate sem alterar arquivos",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="emite uma resposta JSON estável (sem prompts ou ANSI)",
    )
    parser.add_argument(
        "--output",
        metavar="ARQUIVO",
        help="com ui, grava o HTML de diagnóstico fora da instalação",
    )
    parser.add_argument(
        "--bundle",
        nargs="?",
        const=DEFAULT_BUNDLE_NAME,
        default=None,
        metavar="ARQUIVO",
        help=f"com doctor, grava um zip sanitizado para revisão (padrão: {DEFAULT_BUNDLE_NAME})",
    )
    parser.add_argument(
        "--backup",
        nargs="?",
        const=DEFAULT_PROFILE_BUNDLE,
        default=None,
        metavar="ARQUIVO",
        help=f"com profile, grava um zip das configurações pessoais (padrão: {DEFAULT_PROFILE_BUNDLE})",
    )
    parser.add_argument(
        "--restore",
        metavar="ARQUIVO",
        help="com profile, restaura o zip de configurações pessoais",
    )
    parser.add_argument(
        "--add",
        metavar="ENDEREÇO",
        help="com library, grava um favorito local",
    )
    parser.add_argument(
        "--remove",
        metavar="ENDEREÇO",
        help="com library, remove um favorito local",
    )
    parser.add_argument(
        "--yes", action="store_true",
        help="confirma automaticamente o plano de update ou upgrade",
    )
    parser.add_argument(
        "--downloads", action="store_true",
        help="no cleanup, remove também arquivos não gerenciados baixados por servidores",
    )
    parser.add_argument(
        "--personal-data", action="store_true",
        help="no cleanup, remove também histórico, logs e demos locais",
    )
    parser.add_argument(
        "--purge", action="store_true",
        help="com uninstall, remove a instalação inteira, dados pessoais e caches x86QW",
    )
    parser.add_argument(
        "--sync-gitignore", action="store_true",
        help="com changes, atualiza o .gitignore seletivo usando os inventories instalados",
    )
    parser.add_argument(
        "action", nargs="?", default="install",
        help=(
            "install, menu, play, host, proxy, qtv, status, version, update, upgrade, repair, migrate, components, presets, hub, "
            "verify, doctor, ui, profile, library, changes, uninstall ou cleanup"
        ),
    )
    parser.add_argument(
        "--target",
        dest="target_option",
        type=Path,
        default=None,
        metavar="DIR",
        help="diretório de instalação (o launcher instalado injeta esta flag)",
    )
    parser.add_argument(
        "target", nargs="?", type=Path,
        help="diretório de instalação (o instalador público pergunta antes de iniciar)",
    )
    # Python 3.10/3.11 do not accept the installation target after an option
    # with the regular parser.  The public CLI documents both orders, so use
    # the stdlib intermixed parser when available and retain the older fallback
    # for runtimes that do not expose it.
    parse_intermixed = getattr(parser, "parse_intermixed_args", None)
    namespace = (
        parse_intermixed(arguments)
        if callable(parse_intermixed)
        else parser.parse_args(arguments)
    )
    if namespace.target_option is not None:
        if (
            namespace.target is not None
            and Path(namespace.target) != Path(namespace.target_option)
        ):
            parser.error("--target e o destino posicional não podem divergir")
        namespace.target = namespace.target_option
    # version is intentionally usable before any bundled catalog is opened;
    # this is the bootstrap diagnostic path for a damaged or partial bundle.
    if namespace.action == "version":
        return namespace
    internal_actions = ("install", "menu", "components", "presets")
    valid_actions = (*internal_actions, *public_command_names())
    if namespace.action not in valid_actions:
        parser.error(f"ação desconhecida: {namespace.action}. Use {', '.join(valid_actions)}")
    if namespace.action != "cleanup" and (namespace.downloads or namespace.personal_data):
        parser.error("--downloads e --personal-data só podem ser usados com cleanup")
    selection_flags = (
        namespace.non_interactive, namespace.channel, namespace.release, namespace.profile,
    )
    if namespace.action != "install" and any(value is not None and value is not False for value in selection_flags):
        parser.error("--non-interactive, --channel, --release e --profile só podem ser usados com install")
    if namespace.action != "install" and namespace.native_profile is not None:
        parser.error("--native-profile só pode ser usado com install")
    if namespace.native_profile is not None and not os.environ.get(NATIVE_CANDIDATE_ROOT_ENV):
        parser.error("--native-profile é reservado a um candidato nativo explícito")
    if namespace.native_profile is not None and namespace.profile is not None:
        parser.error("--native-profile não pode ser combinado com --profile")
    if namespace.native_profile is not None and (
        namespace.channel is None or namespace.release is None
    ):
        parser.error("--native-profile exige --channel e --release")
    if namespace.action != "install" and namespace.non_interactive:
        parser.error("--non-interactive só pode ser usado com install")
    explicit_target = namespace.target is not None
    if namespace.non_interactive:
        missing = [
            name for name, value in (
                ("--platform", namespace.platform),
                ("--channel", namespace.channel),
                ("--release", namespace.release),
                (
                    "--profile",
                    namespace.profile if namespace.profile is not None else namespace.native_profile,
                ),
            ) if value is None
        ]
        if not explicit_target:
            missing.append("target")
        if missing:
            parser.error(
                "install não interativo exige: " + ", ".join(missing)
            )
    if namespace.purge and namespace.action != "uninstall":
        parser.error("--purge só pode ser usado com uninstall")
    if namespace.sync_gitignore and namespace.action != "changes":
        parser.error("--sync-gitignore só pode ser usado com changes")
    if namespace.installed_cli and namespace.action in {"install", "components", "presets"}:
        parser.error(
            f"{namespace.action} não está disponível na CLI instalada; use install.sh para instalar ou adicionar conteúdo"
        )
    if namespace.skip_cli_update and not (namespace.installed_cli and namespace.action in {"update", "upgrade"}):
        parser.error("--skip-cli-update é reservado ao processo interno de atualização da CLI")
    if namespace.dry_run and namespace.action not in {"update", "upgrade", "repair", "migrate"}:
        parser.error("--dry-run só pode ser usado com update, upgrade, repair ou migrate")
    if namespace.bundle is not None and namespace.action != "doctor":
        parser.error("--bundle só pode ser usado com doctor")
    if namespace.output is not None and namespace.action != "ui":
        parser.error("--output só pode ser usado com ui")
    if (namespace.backup is not None or namespace.restore is not None) and namespace.action != "profile":
        parser.error("--backup e --restore só podem ser usados com profile")
    if namespace.backup is not None and namespace.restore is not None:
        parser.error("--backup e --restore não podem ser combinados")
    if (namespace.add is not None or namespace.remove is not None) and namespace.action != "library":
        parser.error("--add e --remove só podem ser usados com library")
    if namespace.add is not None and namespace.remove is not None:
        parser.error("--add e --remove não podem ser combinados")
    if namespace.json and namespace.action not in {
        "version", "status", "hub", "verify", "doctor", "repair", "update", "upgrade",
    }:
        parser.error(
            "--json só pode ser usado com version, status, hub, verify, doctor, repair, update ou upgrade"
        )
    if namespace.json and namespace.action in {"repair", "update", "upgrade"} and not namespace.dry_run:
        parser.error(f"{namespace.action} --json exige --dry-run para não ocultar mutações")
    if namespace.yes and namespace.action not in {"update", "upgrade"}:
        parser.error("--yes só pode ser usado com update ou upgrade")
    if namespace.platform is not None and namespace.action != "install":
        parser.error("--platform só pode ser usado com install")
    if namespace.target is None and not namespace.online_only:
        namespace.target = project_root / "quake-world"
    return namespace


def choose_public_target(suggested: Path | None = None) -> Path:
    suggested = suggested or Path.home() / "Games" / "x86qw"
    print("\nOnde deseja instalar o x86QW?")
    print(f"Sugestão: {suggested}")
    print("Pressione Enter para aceitar a sugestão ou informe outro diretório.")
    try:
        answer = input("Diretório de instalação: ").strip()
    except EOFError as error:
        raise InstallerError(
            "Não foi possível ler o diretório de instalação. "
            "Execute em um terminal interativo ou informe o caminho na linha de comando."
        ) from error
    target = Path(answer).expanduser() if answer else suggested
    if not target.is_absolute():
        target = Path.cwd() / target
    return target


def pause_menu_result(message: str) -> bool:
    """Keep a completed action visible before the navigator redraws the screen."""
    try:
        input(message)
    except EOFError:
        return False
    return True


def run_main_menu(target: Path, *, verbose: bool = False, no_color: bool = False) -> int:
    """Run the installed CLI as a task-oriented navigator without changing command contracts."""
    breadcrumb = f"x86QW {application_version()}"
    while True:
        try:
            selected = navigation.select_one(
                "O que você quer fazer?",
                (
                    navigation.MenuOption(
                        "play", "Jogar", "mods locais e modos KTX",
                        "Escolha jogo, modo, mapa e regras.", group="Partida",
                    ),
                    navigation.MenuOption(
                        "experience", "Experiência de jogo",
                        "x86QW Ruleset ou regras competitivas",
                        "Altere a preferência usada ao abrir o cliente.",
                        group="Partida",
                    ),
                    navigation.MenuOption(
                        "hub", "Encontrar servidor",
                        "jogar, observar ou assistir QTV",
                        "Servidores públicos com busca.", group="Partida",
                    ),
                    navigation.MenuOption(
                        "host", "Hospedar partida",
                        "MVDSV com QTV e QWFWD opcionais",
                        "Servidor dedicado em primeiro plano.", group="Partida",
                    ),
                    navigation.MenuOption(
                        "services", "Serviços",
                        "visualizar, transmitir ou usar proxy",
                        "Estado da stack, QTV e QWFWD isolados.",
                        group="Instalação",
                    ),
                    navigation.MenuOption(
                        "manage", "Gerenciar instalação",
                        "atualizar, reparar ou limpar",
                        "Operações seguras sobre conteúdo instalado.",
                        group="Instalação",
                    ),
                    navigation.MenuOption(
                        "info", "Ajuda e informações",
                        "versão, caminhos e comandos",
                        "A CLI por argumentos continua disponível.",
                        group="Instalação",
                    ),
                    navigation.MenuOption(
                        "exit", "Sair", "encerrar o menu", group="Instalação",
                    ),
                ),
                breadcrumb=breadcrumb,
                searchable=True,
                allow_back=True,
            )
        except navigation.MenuCancelled:
            if sys.stdin.isatty():
                raise
            launcher = public_launcher_name()
            print(f"\nUso: {launcher} <comando> [opções]")
            print(f"Exemplo: {launcher} play")
            print("Comandos: play, host, proxy, qtv, status, hub, update, upgrade, verify, doctor, ui, profile, library, changes, migrate, repair, cleanup, uninstall e version.")
            return 0
        if selected in (None, "exit"):
            print("\nAté a próxima partida.")
            return 0
        if selected == "play":
            gameplay = load_gameplay_module()
            result = gameplay.main([
                "--target", str(target), "--menu",
                *(("--verbose",) if verbose else ()),
                *(("--no-color",) if no_color else ()),
            ], propagate_menu_exit=True)
            if result == 130:
                return result
            if not pause_menu_result("\nPressione Enter para retornar ao menu principal..."):
                return result
            continue
        if selected == "experience":
            gameplay = importlib.import_module("gameplay")
            result = gameplay.main([
                "--target", str(target), "--configure-ruleset",
                *(("--verbose",) if verbose else ()),
                *(("--no-color",) if no_color else ()),
            ], propagate_menu_exit=True)
            if result == 130:
                return result
            if not pause_menu_result("\nPressione Enter para retornar ao menu principal..."):
                return result
            continue
        if selected == "host":
            services = load_services_module()
            result = services.main([
                "host", "--target", str(target), "--menu",
                *(("--verbose",) if verbose else ()),
                *(("--no-color",) if no_color else ()),
            ], propagate_menu_exit=True)
            if result == 130:
                return result
            if not pause_menu_result("\nPressione Enter para retornar ao menu principal..."):
                return result
            continue
        if selected == "hub":
            result = main([
                "--online-only", "--installed-cli", "hub", str(target),
                *(("--verbose",) if verbose else ()),
                *(("--no-color",) if no_color else ()),
            ])
            if result == 130:
                return result
            if not pause_menu_result("\nPressione Enter para retornar ao menu principal..."):
                return result
            continue
        if selected == "services":
            while True:
                service = navigation.select_one(
                    "Serviços x86QW",
                    (
                        navigation.MenuOption(
                            "status", "Visualizar serviços ativos",
                            "PIDs, endpoints e parâmetros não sensíveis",
                            "Use também em outro terminal enquanto a stack estiver ativa.",
                        ),
                        navigation.MenuOption(
                            "stop", "Encerrar serviços ativos",
                            "shutdown coordenado da stack atual",
                            "Confirma identidade, encerra filhos e remove temporários.",
                        ),
                        navigation.MenuOption(
                            "qtv", "QTV", "relay HTTP/MVD",
                            "Pode iniciar isolado ou conectado a um MVDSV.",
                        ),
                        navigation.MenuOption(
                            "proxy", "QWFWD", "proxy UDP QuakeWorld",
                            "Encaminha conexões sem exigir um MVDSV local.",
                        ),
                    ),
                    breadcrumb=breadcrumb + " › Serviços",
                    searchable=True,
                    allow_back=True,
                )
                if service is None:
                    break
                services = load_services_module()
                service_action = "status" if service == "stop" else service
                result = services.main([
                    service_action, "--target", str(target), "--menu",
                    *(("--stop",) if service == "stop" else ()),
                    *(("--verbose",) if verbose else ()),
                    *(("--no-color",) if no_color else ()),
                ], propagate_menu_exit=True)
                if result == 130:
                    return result
                if not pause_menu_result("\nPressione Enter para retornar ao menu de serviços..."):
                    return result
            continue
        if selected == "manage":
            action = navigation.select_one(
                "Gerenciar instalação",
                (
                    navigation.MenuOption("content", "Alterar conteúdo pelo bootstrap", "mostrar o comando para adicionar ou remover componentes"),
                    navigation.MenuOption("update", "Atualizar", "atualizar somente o que já está instalado"),
                    navigation.MenuOption("upgrade", "Incorporar novidades", "convergir com o perfil atual"),
                    navigation.MenuOption("verify", "Verificar integridade", "operação somente leitura"),
                    navigation.MenuOption("doctor", "Diagnosticar instalação", "somente leitura, sem alterar arquivos"),
                    navigation.MenuOption("ui", "Painel local", "HTML somente leitura sobre doctor e library"),
                    navigation.MenuOption("profile", "Perfil pessoal", "backup e restore das configurações user-owned"),
                    navigation.MenuOption("library", "Favoritos e recentes", "servidores locais com origem e freshness"),
                    navigation.MenuOption("changes", "Ver mudanças locais", "comparar a instalação com o baseline registrado"),
                    navigation.MenuOption("migrate", "Migrar metadados", "converter o estado legado para o contrato 1.0"),
                    navigation.MenuOption("repair", "Diagnosticar e reparar", "preserva arquivos pessoais"),
                    navigation.MenuOption("cleanup", "Limpar dados locais", "cache e dados regeneráveis"),
                    navigation.MenuOption("uninstall", "Desinstalar", "preservar ou remover todos os dados"),
                ),
                breadcrumb=breadcrumb + " › Gerenciar instalação",
                searchable=True,
                allow_back=True,
            )
            if action is None:
                continue
            if action == "content":
                print("\nA CLI instalada não baixa componentes novos durante o gameplay.")
                print("Reexecute o bootstrap e informe este mesmo destino:")
                bootstrap = public_bootstrap_command(
                    PUBLIC_UNIX_BOOTSTRAP_COMMAND,
                    PUBLIC_POWERSHELL_BOOTSTRAP_COMMAND,
                )
                print(f"\n  {bootstrap}")
                print(f"\nDestino atual: {target}")
                try:
                    input("\nPressione Enter para voltar ao menu...")
                except EOFError:
                    return 0
                continue
            extra: list[str] = []
            if action == "cleanup":
                while True:
                    scope = navigation.select_one(
                        "O que deseja limpar?",
                        (
                            navigation.MenuOption("cache", "Somente cache", "mantém downloads, histórico, logs e demos"),
                            navigation.MenuOption("downloads", "Cache e downloads", "mantém histórico, logs e demos"),
                            navigation.MenuOption("personal", "Cache e dados pessoais", "remove histórico, logs e demos"),
                            navigation.MenuOption("all", "Todos os dados locais", "remove downloads, histórico, logs e demos"),
                        ),
                        breadcrumb=breadcrumb + " › Gerenciar instalação › Limpeza",
                        searchable=True,
                        allow_back=True,
                    )
                    if scope is None:
                        break
                    if scope in {"personal", "all"}:
                        print("\nSerão removidos dados pessoais locais:")
                        print("  - histórico do console")
                        print("  - logs do ezQuake")
                        print("  - demos de Total Destruction 2")
                        if scope == "all":
                            print("  - downloads recebidos de servidores")
                        confirmed = navigation.confirm(
                            "Confirma a limpeza destes dados?",
                            breadcrumb=breadcrumb + " › Gerenciar instalação › Limpeza › Confirmação",
                            description="remover permanentemente os dados listados",
                            default=False,
                            allow_back=True,
                        )
                        if confirmed is None:
                            continue
                        if not confirmed:
                            scope = None
                            break
                    if scope in {"downloads", "all"}:
                        extra.append("--downloads")
                    if scope in {"personal", "all"}:
                        extra.append("--personal-data")
                    break
                if scope is None:
                    continue
            elif action == "uninstall":
                while True:
                    scope = navigation.select_one(
                        "Como deseja desinstalar?",
                        (
                            navigation.MenuOption("preserve", "Preservar dados pessoais", "mantém PAKs, configurações, logs e demos"),
                            navigation.MenuOption("purge", "Remover completamente", "apaga toda a instalação e o cache"),
                    ),
                    breadcrumb=breadcrumb + " › Gerenciar instalação › Desinstalar",
                    searchable=True,
                    allow_back=True,
                )
                    if scope is None:
                        break
                    confirmed = navigation.confirm(
                        "Confirma a desinstalação?",
                        breadcrumb=breadcrumb + " › Gerenciar instalação › Desinstalar › Confirmação",
                        description="Esta operação altera o conteúdo instalado.",
                        default=False,
                        allow_back=True,
                    )
                    if confirmed is None:
                        continue
                    if not confirmed:
                        scope = None
                    break
                if scope is None:
                    continue
                if scope == "purge":
                    extra.append("--purge")
            result = main([
                "--online-only", "--installed-cli", action, str(target), *extra,
                *(("--verbose",) if verbose else ()),
                *(("--no-color",) if no_color else ()),
            ])
            if action == "uninstall" and result == 0:
                return 0
            if not pause_menu_result("\nPressione Enter para retornar ao menu principal..."):
                return result
            continue
        if selected == "info":
            print(f"\nx86QW {application_version()}")
            print(f"Instalação: {target}")
            print("Comandos: play, host, hub, qtv, proxy, status, update, upgrade, verify, doctor, ui, profile, library, changes, migrate, repair, cleanup, uninstall e version.")
            launcher = public_launcher_name()
            print(f"Use {launcher} <comando> --help para ver todas as opções avançadas.")
            print("No menu: ↑↓ navega, →/Enter seleciona, ← volta e Esc sai; / busca quando aparecer na legenda.")
            try:
                input("\nPressione Enter para voltar ao menu...")
            except EOFError:
                return 0


def _json_status_data(target: Path) -> dict[str, object]:
    """Return a read-only installation/status snapshot without loading catalogs."""

    state_path = target / INSTALL_STATE
    sessions_path = target / ".x86qw" / "sessions"
    sessions: list[dict[str, object]] = []
    if sessions_path.is_dir() and not sessions_path.is_symlink():
        for directory in sorted(sessions_path.iterdir()):
            if not directory.is_dir() or directory.is_symlink():
                continue
            journal = directory / "session.json"
            if not journal.is_file() or journal.is_symlink():
                continue
            try:
                document = json.loads(journal.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if not isinstance(document, dict):
                continue
            # Keep the public status surface deliberately small and redactable.
            sessions.append({
                "session_id": str(document.get("session_id", directory.name)),
                "status": str(document.get("status", "unknown")),
                "command": str(document.get("command", "unknown")),
            })
    return {
        "project": "x86qw",
        "target": str(target),
        "installation": "present" if target.exists() else "missing",
        "state": "present" if state_path.is_file() else "missing",
        "sessions": sessions,
    }


def _doctor_trust_timestamp() -> Path | None:
    try:
        cache_root = host_adapter.user_cache_directory(CACHE_DIR_NAME)
    except Exception:
        return None
    path = cache_root / "trust" / "metadata" / "timestamp.json"
    if path.is_file() and not path.is_symlink():
        return path
    return None


def _doctor_report(target: Path) -> dict[str, object]:
    commands: tuple[str, ...] | None
    try:
        commands = public_command_names()
    except Exception:
        commands = None
    return diagnose(
        target,
        catalog_commands=commands,
        trust_timestamp_path=_doctor_trust_timestamp(),
    )


def _write_doctor_bundle(report: dict[str, object], bundle: str, target: Path) -> Path:
    try:
        return write_doctor_bundle(report, resolve_bundle_destination(bundle, target))
    except OSError as error:
        raise InstallerError(str(error)) from error


def _write_profile_backup(target: Path, bundle: str) -> Path:
    try:
        return backup_user_profile(target, resolve_bundle_destination(bundle, target))
    except OSError as error:
        raise InstallerError(str(error)) from error


def _restore_profile_backup(target: Path, archive: str) -> tuple[str, ...]:
    path = Path(archive).expanduser()
    try:
        return restore_user_profile(path, target)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        raise InstallerError(str(error)) from error


def _mutate_library(target: Path, *, add: str | None, remove: str | None) -> None:
    try:
        if add is not None:
            add_favorite(target, add)
        if remove is not None:
            remove_favorite(target, remove)
    except (OSError, ValueError) as error:
        raise InstallerError(str(error)) from error


def _json_plan_row(row: UpdatePlanRow) -> dict[str, object]:
    """Project one maintenance row into the stable dry-run wire shape."""

    return {
        "kind": row.kind,
        "item": row.item,
        "installed": row.installed,
        "available": row.available,
        "action": row.action,
        "size": row.size,
    }


def _json_hub_servers(servers: object) -> list[dict[str, object]]:
    """Project the mutable Hub response into the closed public JSON shape.

    The Hub response is intentionally kept out of the public contract: it may
    contain nested settings, player records, or QTV metadata that are useful to
    the terminal UI but are not stable API.  Normalize it here so the JSON
    command remains deterministic and cannot leak arbitrary fields.
    """

    if not isinstance(servers, list):
        raise InstallerError("O Hub retornou um catálogo de servidores inválido.")
    projected: list[dict[str, object]] = []
    seen_addresses: set[str] = set()
    address_pattern = re.compile(r"^[A-Za-z0-9_.:\[\]-]+:[0-9]{1,5}$")
    qtv_pattern = re.compile(r"^[0-9]+@[A-Za-z0-9_.:\[\]-]+:[0-9]{1,5}$")
    for server in servers:
        if not isinstance(server, dict):
            continue
        address = server.get("address")
        if not isinstance(address, str) or not address_pattern.fullmatch(address):
            continue
        if address in seen_addresses:
            continue
        port = int(address.rsplit(":", 1)[1])
        if not 1 <= port <= 65535:
            continue
        settings = server.get("settings")
        settings = settings if isinstance(settings, dict) else {}

        def text_value(*values: object, fallback: str) -> str:
            for value in values:
                if isinstance(value, str):
                    cleaned = "".join(
                        character if character.isprintable() and character != "\ufffd" else "?"
                        for character in value
                    ).strip()
                    if cleaned:
                        return cleaned[:4096]
            return fallback

        raw_players = server.get("players")
        humans = bots = 0
        if isinstance(raw_players, list):
            for player in raw_players:
                if isinstance(player, dict) and player.get("is_bot"):
                    bots += 1
                elif isinstance(player, dict):
                    humans += 1
        elif isinstance(raw_players, dict):
            raw_humans = raw_players.get("humans")
            raw_bots = raw_players.get("bots")
            if type(raw_humans) is int and raw_humans >= 0:
                humans = raw_humans
            if type(raw_bots) is int and raw_bots >= 0:
                bots = raw_bots

        qtv_stream: str | None = None
        raw_qtv = server.get("qtv_stream")
        if isinstance(raw_qtv, dict):
            raw_qtv = raw_qtv.get("url") or raw_qtv.get("stream")
        if isinstance(raw_qtv, str) and qtv_pattern.fullmatch(raw_qtv):
            qtv_stream = raw_qtv

        projected.append({
            "address": address,
            "title": text_value(
                settings.get("hostname"), server.get("title"), server.get("name"),
                fallback=address,
            ),
            "mode": text_value(server.get("mode"), settings.get("mode"), fallback="-"),
            "map": text_value(settings.get("map"), server.get("map"), fallback="-"),
            "players": {"humans": humans, "bots": bots},
            "qtv_stream": qtv_stream,
        })
        seen_addresses.add(address)
    if not projected:
        raise InstallerError("Nenhum servidor ativo reconhecido foi retornado pelo Hub.")
    return sorted(projected, key=lambda item: str(item["address"]))


def execute_json_action(options: argparse.Namespace, project_root: Path) -> int:
    """Execute a read-only public command and emit exactly one JSON document."""

    command = str(options.action)
    target = options.target.expanduser().resolve() if options.target is not None else None
    captured = io.StringIO()
    try:
        with redirect_stdout(captured), redirect_stderr(captured):
            if command == "version":
                data: object = {
                    "project": "x86qw",
                    "version": application_version(),
                }
            elif command == "status":
                assert target is not None
                data = _json_status_data(target)
            elif command == "hub":
                assert target is not None
                installer = Installer(project_root, target, online_only=options.online_only)
                installer.validate_target("verify", purge=False)
                data = {
                    "servers": _json_hub_servers(installer.hub_servers()),
                    "target": str(target),
                }
            elif command == "verify":
                assert target is not None
                installer = Installer(project_root, target, online_only=options.online_only)
                installer.validate_target("verify", purge=False)
                installer.reject_target_symlinks()
                installer.verify_installation()
                data = {"target": str(target), "verified": True}
            elif command == "doctor":
                assert target is not None
                data = _doctor_report(target)
            else:
                # Dry-run maintenance goes through the same lock/plan path as
                # the human CLI, but rows are projected directly from the
                # operation model.  Human terminal rendering remains captured
                # and is never part of the JSON contract.
                plan_rows: list[dict[str, object]] = []
                execute_manager_action(options, project_root, plan_sink=plan_rows)
                data = {
                    "target": str(target) if target is not None else "",
                    "status": "planned" if plan_rows else "noop",
                    "operations": plan_rows,
                }
        output = make_json_output(
            command,
            data=data,
            dry_run=bool(options.dry_run),
        )
        if command == "doctor" and options.bundle is not None:
            assert target is not None
            print(
                f"Bundle sanitizado: {_write_doctor_bundle(data, options.bundle, target)}",
                file=sys.stderr,
            )
        print(render_json_output(output), end="")
        return int(ExitCode.SUCCESS)
    except KeyboardInterrupt as error:
        output = make_json_output(
            command,
            ok=False,
            exit_code=ExitCode.INTERRUPTED,
            errors=({"code": "interrupted", "message": "Operação cancelada."},),
            dry_run=bool(options.dry_run),
        )
        print(render_json_output(output), end="")
        return int(ExitCode.INTERRUPTED)
    except (InstallerError, session_control.SessionControlError) as error:
        output = make_json_output(
            command,
            ok=False,
            exit_code=getattr(error, "exit_code", ExitCode.FAILURE),
            errors=({
                "code": getattr(error, "code", "operation"),
                "message": str(error),
            },),
            dry_run=bool(options.dry_run),
        )
        print(render_json_output(output), end="")
        return int(getattr(error, "exit_code", ExitCode.FAILURE))
    except Exception as error:  # pragma: no cover - defensive JSON boundary
        output = make_json_output(
            command,
            ok=False,
            exit_code=ExitCode.FAILURE,
            errors=({"code": "unexpected", "message": str(error)},),
            dry_run=bool(options.dry_run),
        )
        print(render_json_output(output), end="")
        return int(ExitCode.FAILURE)


def execute_manager_action(
    options: argparse.Namespace,
    project_root: Path,
    *,
    plan_sink: list[dict[str, object]] | None = None,
) -> int:
    """Execute a parsed manager action under the installation operation contract."""
    action_labels = {
        "install": "instalar ezQuake + componentes x86QW", "components": "gerenciar componentes x86QW",
        "presets": "gerenciar presets",
        "hub": "navegar servidores", "verify": "verificar", "changes": "comparar instalação",
        "uninstall": "desinstalar",
        "cleanup": "limpar caches e dados locais", "update": "atualizar o conteúdo instalado",
        "upgrade": "incorporar novidades da distribuição", "repair": "reparar conteúdo gerenciado",
        "migrate": "migrar metadados para o contrato 1.0",
    }
    action_label = "desinstalar e remover todos os dados" if options.purge else action_labels[options.action]
    console.banner(action_label, options.target)
    installer = Installer(project_root, options.target, online_only=options.online_only)
    operation_lock: session_control.InstallationLock | None = None
    recovery_confirmed = False
    purge_completed = False
    uninstall_completed = False

    def acquire_operation_lock() -> None:
        nonlocal operation_lock, recovery_confirmed
        if operation_lock is not None:
            return
        operation_lock = session_control.InstallationLock.acquire(
            installer.target, options.action, "maintenance",
        )
        try:
            session_recovery = importlib.import_module(
                "x86qw_runtime.supervisor.sessions"
            )
            session_recovery.recover_sessions(installer.target, reporter=console)
            operation_lock.confirm_recovery()
            recovery_confirmed = True
        except Exception:
            try:
                operation_lock.release(restore_reclaimed=True)
            except Exception as cleanup_error:
                console.warning(
                    f"Falha ao restaurar o lock após recuperação recusada: {cleanup_error}"
                )
            raise

    try:
        if options.action == "cleanup":
            installer.validate_target(options.action, purge=False)
            installer.require_managed_installation_identity("cleanup")
            acquire_operation_lock()
            console.section("Limpeza segura")
            cache_count, personal_count = installer.cleanup_data(
                downloads=options.downloads,
                personal_data=options.personal_data,
            )
            if cache_count:
                console.success(f"Dados regeneráveis removidos ({file_count(cache_count)}).")
            else:
                console.info("Nenhum dado regenerável da instalação precisava ser removido.")
            if personal_count:
                console.success(f"Dados pessoais locais removidos ({file_count(personal_count)}).")
            elif not options.personal_data:
                console.info("Histórico, logs e demos válidas foram preservados; use --personal-data para removê-los.")
            if not options.downloads:
                console.info("Downloads de servidores foram preservados; use --downloads para removê-los.")
            return 0

        installer.validate_target(options.action, purge=options.purge)
        console.detail(f"Destino normalizado: {installer.target}")
        if not options.purge:
            installer.reject_target_symlinks()
        if options.action == "migrate":
            # Planning is intentionally read-only.  Unlike mutating maintenance
            # actions, dry-run must not create the operation lock or recover
            # sessions; the planner snapshots the target before any write.
            if not options.dry_run:
                acquire_operation_lock()
                # A previous process may have crashed after publishing a
                # migration journal.  Recovery is a mutation and therefore
                # belongs only to the non-dry-run path, under the exclusive
                # installation lock, before the preview is rebuilt.
                if inspect_pending_migration(installer.target) is not None:
                    recover_migration(installer.target)
            console.section("Migração da instalação")
            migration = migrate_installation(
                installer.target, target_version="1.0.0", dry_run=True,
            )
            if migration.conflicts:
                details = "; ".join(
                    f"{item.path}: {item.detail}" for item in migration.conflicts
                )
                raise InstallerError(
                    "A migração foi bloqueada por conflitos preservados: " + details
                )
            for component in migration.retired_components:
                console.info(
                    f"Componente aposentado preservado para diagnóstico: {component}"
                )
            if not migration.operations:
                console.success("Nenhuma migração é necessária; os metadados já estão convergentes.")
            else:
                for operation in migration.operations:
                    console.info(
                        f"{operation.phase.value}: {operation.source} → {operation.destination}"
                    )
                if options.dry_run:
                    console.heading("Simulação concluída; nenhum arquivo foi alterado")
                else:
                    migrate_installation(
                        installer.target, target_version="1.0.0", dry_run=False,
                    )
                    console.success("Migração concluída e validada.")
        elif options.action == "changes":
            installer.report_installation_changes(
                sync_gitignore=options.sync_gitignore,
            )
        elif options.action == "verify":
            installer.verify_installation()
        elif options.action == "repair":
            acquire_operation_lock()
            plan_rows: list[UpdatePlanRow] = []
            needs_repair = installer.repair(dry_run=True, plan_rows=plan_rows)
            if plan_sink is not None:
                plan_sink.extend(_json_plan_row(row) for row in plan_rows)
            if not needs_repair:
                console.success("Nenhum reparo é necessário; a instalação está íntegra.")
            else:
                console.update_plan(plan_rows, "repair")
                if options.dry_run:
                    console.heading("Simulação concluída; nenhum arquivo foi alterado")
                else:
                    installer.repair(
                        dry_run=False, plan_rows=[], allow_download=not options.installed_cli,
                    )
                    console.success("Reparo concluído e validado.")
        elif options.action == "uninstall":
            if options.purge:
                console.section("Desinstalação completa")
                if lexists(installer.target):
                    installer.require_managed_installation_identity(
                        "uninstall --purge"
                    )
                    acquire_operation_lock()
                    installer.purge(preserve_operation_lock=True)
                else:
                    installer.purge()
                purge_completed = True
            else:
                installer.require_managed_installation_identity("uninstall")
                acquire_operation_lock()
                console.section("Desinstalação")
                installer.uninstall()
                uninstall_completed = True
        elif options.action == "hub":
            console.section("QuakeWorld Hub")
            installer.browse_hub()
        elif options.action in {"update", "upgrade"}:
            installer.update_ui = True
            if (
                options.installed_cli
                and not options.skip_cli_update
                and installer.handoff_cli_update(
                    options.action, dry_run=options.dry_run, assume_yes=options.yes,
                )
            ):
                return 0
            acquire_operation_lock()
            plan_rows: list[UpdatePlanRow] = []
            if options.skip_cli_update:
                cli_row = installer.cli_update_plan_row()
                if cli_row is not None:
                    plan_rows.append(cli_row)
            operation = installer.upgrade if options.action == "upgrade" else installer.update
            content_changed = operation(
                dry_run=True, preview=not options.dry_run, plan_rows=plan_rows,
            )
            if plan_sink is not None:
                plan_sink.extend(_json_plan_row(row) for row in plan_rows)
            if not plan_rows:
                message = (
                    "Nenhuma novidade disponível; a instalação já corresponde ao perfil atual."
                    if options.action == "upgrade"
                    else "Nenhuma atualização disponível; o conteúdo instalado já está atualizado."
                )
                console.heading("Já está atualizado")
                console.success(message)
                return 0
            plan_summary = console.update_plan(plan_rows, options.action)
            if options.dry_run:
                console.heading("Simulação concluída; nenhum arquivo foi alterado")
                return 0
            if not installer.confirm_update_plan(
                options.action, assume_yes=options.yes, summary=plan_summary,
            ):
                return 0
            console.heading(
                "Atualizando pacotes" if options.action == "update" else "Incorporando novidades"
            )
            with installer.component_state_transaction() as operation_results:
                if content_changed:
                    operation(dry_run=False, mutation_results=operation_results)
                if options.skip_cli_update:
                    installer.install_online_cli(mutation_results=operation_results)
        else:
            if options.action == "install":
                installer.update_ui = True
                with installer.component_state_transaction() as operation_results:
                    install_kwargs: dict[str, object] = {
                        "platform": options.platform,
                        "before_mutation": acquire_operation_lock,
                        "mutation_results": operation_results,
                    }
                    if options.non_interactive:
                        install_kwargs.update({
                            "channel": options.channel,
                            "release": options.release,
                            "profile": options.profile,
                            "non_interactive": True,
                        })
                    if options.native_profile is not None:
                        install_kwargs.update({
                            "channel": options.channel,
                            "release": options.release,
                            "native_profile": options.native_profile,
                            "non_interactive": True,
                        })
                    installer.install(
                        **install_kwargs,
                    )
                    installer.install_online_cli(mutation_results=operation_results)
            else:
                acquire_operation_lock()
                with installer.component_state_transaction() as operation_results:
                    if options.action == "components":
                        installer.manage_components(mutation_results=operation_results)
                    else:
                        installer.manage_presets(mutation_results=operation_results)
                    installer.install_online_cli(mutation_results=operation_results)
        return 0
    finally:
        original_error = sys.exc_info()[0] is not None
        cleanup_errors: list[str] = []
        try:
            installer.cleanup_stage()
        except Exception as error:
            cleanup_errors.append(f"área temporária: {error}")
        lock_released = operation_lock is None
        if operation_lock is not None:
            try:
                operation_lock.release(restore_reclaimed=not recovery_confirmed)
                lock_released = True
            except Exception as error:
                cleanup_errors.append(f"lock da instalação: {error}")
        if uninstall_completed and lock_released:
            try:
                metadata_result = installer.remove_empty_metadata_transaction()
                if metadata_result is not None:
                    finalize_mutation(metadata_result)
            except Exception as error:
                cleanup_errors.append(f"metadados finais da instalação: {error}")
        if purge_completed and lock_released and lexists(installer.target):
            try:
                topology_result = installer.remove_empty_metadata_transaction(
                    include_target=True,
                )
                if topology_result is None or lexists(installer.target):
                    raise InstallerError(
                        "A topologia final da instalação não está vazia e foi preservada."
                    )
                finalize_mutation(topology_result)
                console.success(
                    f"Diretório da instalação removido: {installer.target}"
                )
            except Exception as error:
                cleanup_errors.append(f"diretório final da instalação: {error}")
        for error in cleanup_errors:
            console.warning(f"Falha durante a finalização de {error}")
        if cleanup_errors and not original_error:
            raise InstallerError("A operação terminou com falha crítica de finalização.")


def main(arguments: list[str] | None = None) -> int:
    project_root = INSTALLER_ROOT
    options = None
    try:
        raw_arguments = sys.argv[1:] if arguments is None else arguments
        if raw_arguments[:1] == ["play"]:
            gameplay = load_gameplay_module()
            return gameplay.main(raw_arguments[1:])
        if (
            raw_arguments[:1]
            and raw_arguments[0] in {"host", "proxy", "qtv", "status"}
            and "--json" not in raw_arguments
        ):
            services = load_services_module()
            return services.main(raw_arguments)
        options = parse_arguments(raw_arguments, project_root)
        console.configure(verbose=options.verbose, no_color=options.no_color)
        navigation.configure(no_color=options.no_color)
        if options.json:
            return execute_json_action(options, project_root)
        if options.action == "version":
            print(f"x86QW {application_version()}")
            return 0
        if options.action == "doctor":
            target = options.target.expanduser().resolve()
            report = _doctor_report(target)
            print(render_doctor_report(report), end="")
            if options.bundle is not None:
                print(f"Bundle sanitizado: {_write_doctor_bundle(report, options.bundle, target)}")
            return 0
        if options.action == "ui":
            target = options.target.expanduser().resolve()
            if options.output is None:
                handle, name = tempfile.mkstemp(prefix="x86qw-ui-", suffix=".html")
                os.close(handle)
                os.unlink(name)
                destination = Path(name)
            else:
                destination = Path(options.output)
            print(write_local_ui(target, destination).as_posix())
            return 0
        if options.action == "profile":
            target = options.target.expanduser().resolve()
            print(render_profile_report(classify_install_data(target)), end="")
            if options.backup is not None:
                path = _write_profile_backup(target, options.backup)
                print(f"Perfil gravado: {path}")
            if options.restore is not None:
                restored = _restore_profile_backup(target, options.restore)
                print("Arquivos restaurados: " + (", ".join(restored) if restored else "(nenhum)"))
            return 0
        if options.action == "library":
            target = options.target.expanduser().resolve()
            _mutate_library(target, add=options.add, remove=options.remove)
            print(render_library_report(load_library(target)), end="")
            return 0
        if options.online_only and options.target is None:
            options.target = choose_public_target()
        if options.action == "menu":
            return run_main_menu(
                options.target,
                verbose=options.verbose,
                no_color=options.no_color,
            )
        if options.action == "play":
            gameplay = load_gameplay_module()
            play_arguments = [str(options.target)]
            if options.verbose:
                play_arguments.insert(0, "--verbose")
            if options.no_color:
                play_arguments.insert(0, "--no-color")
            return gameplay.main(play_arguments)
        return execute_manager_action(options, project_root)
    except KeyboardInterrupt:
        console.error("Operação cancelada. Nenhuma seleção pendente foi aplicada.")
        return int(ExitCode.INTERRUPTED)
    except navigation.MenuExit:
        console.info("Menu encerrado; nenhuma seleção pendente foi aplicada.")
        return int(ExitCode.SUCCESS)
    except navigation.MenuCancelled:
        console.info("Operação cancelada; nenhuma seleção pendente foi aplicada.")
        return int(ExitCode.INTERRUPTED)
    except (InstallerError, session_control.SessionControlError) as error:
        console.error(str(error))
        if options is not None and not options.verbose:
            print("       Execute novamente com --verbose para obter detalhes técnicos.", file=sys.stderr)
        return int(getattr(error, "exit_code", ExitCode.FAILURE))
    except Exception as error:  # pragma: no cover - last-resort CLI protection
        console.error(f"Falha inesperada: {error}")
        if options is not None and options.verbose:
            traceback.print_exc()
        else:
            print("       Execute novamente com --verbose para exibir o diagnóstico completo.", file=sys.stderr)
        return int(ExitCode.FAILURE)


if __name__ == "__main__":
    raise SystemExit(main())
