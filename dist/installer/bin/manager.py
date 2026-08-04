#!/usr/bin/env python3
"""Cross-platform ezQuake + x86QW component installer."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import errno
import hashlib
import importlib
import json
import os
import platform as host_platform
import plistlib
import re
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import time
import traceback
import urllib.parse
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath

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
from x86qw_runtime.ui import menu as navigation

from x86qw_runtime.io.archive import (
    ArchiveError,
    extract_archive,
    read_archive_member,
    scan_archive,
    validate_installer_bundle,
)
from x86qw_runtime.io import private_fs
from x86qw_runtime.io.atomic import (
    AtomicWriteError,
    atomic_copy_file,
    atomic_write_bytes,
)
from x86qw_runtime.io.metadata import MetadataFileError, read_bounded_regular_file
from x86qw_runtime.errors import ExitCode, InstallerError, PersistenceError
from x86qw_runtime.migrations import migrate_install_state
from x86qw_runtime.state import (
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
    STABLE_VERSION,
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
    BoundedMetadata,
    DownloadError,
    DownloadHTTPError,
    DownloadPolicyError,
    MAX_ARTIFACT_BYTES,
    PinnedArtifact,
    RetryPolicy,
    download as bounded_download,
    download_mirrors as bounded_download_mirrors,
    safe_url_for_log,
    validate_https_url as validate_download_url,
)
from x86qw_runtime.catalogs import (
    load_capabilities,
    load_runtimes,
    runtimes_by_id,
)


ID1_PAK0_SHA256 = "eec9a020b6d8b6df73a5b911e19985f6e2539c1c6857b4a9f400553b9599677d"
ID1_PAK1_SHA256 = "94e355836ec42bc464e4cbe794cfb7b5163c6efa1bcc575622bb36475bf1cf30"
CATALOG_URL = "https://x86qw.x86.com.br/api/v1/catalog.json"
CATALOG_URLS = (
    CATALOG_URL,
    "https://raw.githubusercontent.com/x86dx2/x86qw/main/site/public/api/v1/catalog.json",
    "https://gitlab.com/x86dx2/x86qw/-/raw/main/site/public/api/v1/catalog.json",
)
CATALOG_TIMEOUT = 10.0
CATALOG_MAX_BYTES = 2 * 1024 * 1024
HUB_MAX_BYTES = 1024 * 1024
PUBLIC_UNIX_BOOTSTRAP_COMMAND = (
    """/bin/bash -c 'umask 077; """
    """d=$(mktemp -d "${TMPDIR:-/tmp}/x86qw-bootstrap.XXXXXXXX") || exit 1; """
    """f="$d/install.sh"; cleanup() { rm -f -- "$f"; rmdir "$d" 2>/dev/null || :; }; """
    """abort() { exit 130; }; trap cleanup EXIT; trap abort HUP INT TERM; """
    """set -o pipefail; curl --disable --proto "=https" --proto-redir "=https" """
    """--connect-timeout 15 --max-time 60 --max-filesize 262144 -fsSL """
    """https://x86qw.x86.com.br/install.sh | head -c 262145 >"$f"; s=$?; """
    """n=$(wc -c <"$f") || exit 1; if [ "$n" -gt 262144 ]; then """
    """printf "%s\\n" "x86QW: bootstrap excedeu 262144 bytes." >&2; exit 1; fi; """
    """[ "$s" -eq 0 ] || exit "$s"; /bin/bash "$f" "$@"' x86qw"""
)
PUBLIC_POWERSHELL_BOOTSTRAP_COMMAND = (
    "& { Add-Type -AssemblyName System.Net.Http; $h = [System.Net.Http.HttpClientHandler]::new(); "
    "$h.AllowAutoRedirect = $false; $c = [System.Net.Http.HttpClient]::new($h); "
    "$c.Timeout = [TimeSpan]::FromSeconds(60); $c.MaxResponseContentBufferSize = 262144; "
    "$r = $null; try { $r = $c.GetAsync('https://x86qw.x86.com.br/install.ps1')."
    "GetAwaiter().GetResult(); if (-not $r.IsSuccessStatusCode) { throw \"x86QW: HTTP "
    "$([int]$r.StatusCode).\" }; if ($r.Content.Headers.ContentLength -gt 262144) { "
    "throw 'x86QW: bootstrap excedeu 262144 bytes.' }; $s = $r.Content.ReadAsStringAsync()."
    "GetAwaiter().GetResult(); & ([scriptblock]::Create($s)) @args } finally { if ($null -ne $r) "
    "{ $r.Dispose() }; $c.Dispose(); $h.Dispose() } }"
)
METADATA_DIR = ".x86qw"
COMPONENT_METADATA_DIR = ".x86qw/components"
EZQUAKE_METADATA_DIR = ".x86qw/clients/ezquake"
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
LEGACY_COMPONENT_REPLACEMENTS = {"nquake-ktx": "ktx"}
LEGACY_COMPONENT_REMOVALS = {
    "nquake-sounds": "sons de Clan Arena incorporados ao KTX",
}
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
    if not isinstance(version, str) or not STABLE_VERSION.fullmatch(version):
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
class UpdatePlanRow:
    kind: str
    item: str
    installed: str
    available: str
    action: str
    size: int | None = None


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


class Console:
    def __init__(self) -> None:
        self.verbose = False
        self.color = False

    def configure(self, *, verbose: bool, no_color: bool) -> None:
        self.verbose = verbose
        self.color = sys.stdout.isatty() and not no_color and "NO_COLOR" not in os.environ

    def paint(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def banner(self, action: str, target: Path) -> None:
        title = self.paint(f"x86-qw {application_version()}", "1;36")
        print(f"\n{title} · instalador QuakeWorld", flush=True)
        print(f"Ação: {action}  |  Destino: {target}", flush=True)

    def section(self, title: str) -> None:
        print(f"\n{self.paint(title, '1;36')}", flush=True)

    def heading(self, title: str) -> None:
        print(f"\n{self.paint('==>', '1;36')} {self.paint(title, '1')}", flush=True)

    def info(self, message: str) -> None:
        print(f"{self.paint('[INFO]', '36')} {message}", flush=True)

    def success(self, message: str) -> None:
        print(f"{self.paint('[OK]', '32')} {message}", flush=True)

    def warning(self, message: str) -> None:
        print(f"{self.paint('[ATENÇÃO]', '33')} {message}", flush=True)

    def detail(self, message: str) -> None:
        if self.verbose:
            print(self.paint(f"       {message}", "2"), flush=True)

    def error(self, message: str) -> None:
        label = self.paint("[ERRO]", "31") if self.color and sys.stderr.isatty() else "[ERRO]"
        print(f"{label} {message}", file=sys.stderr, flush=True)

    def update_plan(self, rows: list[UpdatePlanRow], action: str) -> None:
        noun = "pacote" if len(rows) == 1 else "pacotes"
        adjective = "desatualizado" if len(rows) == 1 else "desatualizados"
        action_label = {
            "update": "atualizar", "upgrade": "incorporar", "repair": "reparar",
        }[action]
        self.heading(f"Plano: {action_label} {len(rows)} {noun} {adjective}")
        names = [row.item for row in rows]
        installed = [row.installed for row in rows]
        available = [row.available for row in rows]
        name_width = max(map(len, names))
        installed_width = max(map(len, installed))
        available_width = max(map(len, available))
        terminal_width = max(40, min(shutil.get_terminal_size((100, 24)).columns, 120))
        for row in rows:
            size = f" ({format_bytes_compact(row.size)})" if row.size is not None else ""
            line = (
                f"{row.item.ljust(name_width)}  "
                f"{row.installed.ljust(installed_width)} -> "
                f"{row.available.ljust(available_width)}{size}"
            )
            if len(line) <= terminal_width:
                print(line, flush=True)
                continue
            print("\n".join(textwrap.wrap(
                row.item, width=terminal_width,
                initial_indent="  ", subsequent_indent="    ",
                break_long_words=False, break_on_hyphens=False,
            )), flush=True)
            print(f"    Instalado  | {row.installed}", flush=True)
            print(f"    Disponível | {row.available}", flush=True)
            if row.size is not None:
                print(f"    Download   | {format_bytes_compact(row.size)}", flush=True)

    def download_result(
        self, label: str, *, size: int, status: str = "Baixado",
    ) -> None:
        amount = format_bytes_compact(size)
        check = self.paint("✔︎", "32")
        line = f"{check} {label:<48} {status:>10}  {amount:>9}/{amount}"
        terminal_width = max(40, min(shutil.get_terminal_size((100, 24)).columns, 120))
        if len(line) <= terminal_width:
            print(line, flush=True)
        else:
            print(f"{check} {label}", flush=True)
            print(f"    {status} | {amount}/{amount}", flush=True)

    def download_progress(self, received: int, total: int | None, *, done: bool = False) -> None:
        if not sys.stdout.isatty():
            return
        if total:
            width = 24
            ratio = min(received / total, 1)
            filled = int(width * ratio)
            bar = "#" * filled + "-" * (width - filled)
            status = f"[{bar}] {ratio:6.1%}  {format_bytes(received)} / {format_bytes(total)}"
        else:
            status = f"Recebidos {format_bytes(received)}"
        print(f"\r       {status}", end="\n" if done else "", flush=True)


console = Console()


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GiB"


def format_bytes_compact(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1000 or unit == "GB":
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1000
    return f"{value:.1f}GB"


def package_size(package: dict[str, object]) -> int | None:
    size = package.get("size")
    return size if isinstance(size, int) and size > 0 else None


def file_count(count: int) -> str:
    return f"{count} {'arquivo' if count == 1 else 'arquivos'}"


def lexists(path: Path) -> bool:
    return os.path.lexists(path)


def file_hash(path: Path, algorithm: str = "sha256") -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_hex(value: str, pattern: re.Pattern[str], label: str) -> None:
    if not pattern.fullmatch(value):
        raise InstallerError(f"invalid {label}")


def validate_https_url(url: object, label: str) -> urllib.parse.SplitResult:
    try:
        return validate_download_url(url, label)
    except DownloadPolicyError as error:
        raise InstallerError(str(error)) from error


def https_url_filename(url: object, label: str) -> str:
    parsed = validate_https_url(url, label)
    filename = PurePosixPath(urllib.parse.unquote(parsed.path)).name
    if not filename or filename in (".", ".."):
        raise InstallerError(f"{label} não identifica um arquivo")
    return filename


def ensure_no_symlink(path: Path, label: str) -> None:
    if path.is_symlink():
        raise InstallerError(f"{label} must not be a symlink: {path}")


def remove_path(path: Path, root_device: int | None = None) -> None:
    """Delete without following symlinks or crossing filesystem boundaries."""
    if not lexists(path):
        return
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        path.unlink()
        return
    device = info.st_dev if root_device is None else root_device
    if info.st_dev != device:
        raise InstallerError(f"refusing to cross filesystem boundary: {path}")
    with os.scandir(path) as entries:
        for entry in entries:
            child = Path(entry.path)
            child_info = child.lstat()
            if not stat.S_ISLNK(child_info.st_mode) and child_info.st_dev != device:
                raise InstallerError(f"refusing to cross filesystem boundary: {child}")
            remove_path(child, device)
    path.rmdir()


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


@dataclass
class CorePakRollback:
    installed: list[tuple[Path, tuple[int, int]]]


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
        self._public_catalog: dict[str, object] | None = None
        self._component_source_context: object | None = None
        self.component_source_provider = (
            component_source_provider or _development_component_source_provider
        )
        self.selected_component_profile = "none"
        self.requested_components: list[str] = []
        runtime_catalog_path = INSTALLER_ROOT / RUNTIME_COMPONENT_CATALOG
        development_catalog_path = INSTALLER_ROOT / DEVELOPMENT_COMPONENT_CATALOG
        try:
            if ZIPAPP_PATH is not None:
                self.component_catalog = read_zipapp_json(
                    ZIPAPP_PATH, RUNTIME_COMPONENT_CATALOG, "Catálogo runtime da CLI",
                )
                validate_runtime_catalog(self.component_catalog)
            elif runtime_catalog_path.is_file() and not runtime_catalog_path.is_symlink():
                self.component_catalog = load_runtime_catalog(runtime_catalog_path)
            else:
                self.component_catalog = load_component_catalog(development_catalog_path)
        except (InstallerError, ValueError) as error:
            raise InstallerError(str(error)) from error
        self.components = components_by_id(self.component_catalog)
        self.content_component_namespaces = set(self.component_catalog["content_namespaces"])

    def run_command(self, arguments: list[str], *, capture: bool = False) -> str:
        console.detail("$ " + " ".join(arguments))
        quiet = capture or not console.verbose
        try:
            result = subprocess.run(
                arguments, check=True, text=True,
                stdout=subprocess.PIPE if quiet else None,
                stderr=subprocess.PIPE if quiet else None,
            )
        except FileNotFoundError as error:
            raise InstallerError(f"Comando obrigatório não encontrado: {arguments[0]}") from error
        except subprocess.CalledProcessError as error:
            detail = (error.stderr or error.stdout or "").strip()
            suffix = f": {detail}" if detail else ""
            raise InstallerError(f"O comando {arguments[0]} falhou{suffix}") from error
        return (result.stdout or "").strip()

    def is_native_macos_install(self) -> bool:
        return host_platform.system() == "Darwin" and self.spec is not None and self.spec.key == "macos"

    def ensure_macos_ezquake_closed(self) -> None:
        if not self.is_native_macos_install():
            return
        try:
            result = subprocess.run(
                ["pgrep", "-x", "ezQuake"], check=False, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        except FileNotFoundError as error:
            raise InstallerError("O utilitário nativo pgrep não foi encontrado no macOS.") from error
        if result.returncode == 0:
            raise InstallerError(
                "Feche o ezQuake antes de continuar. O macOS mantém a autorização do diretório "
                "do jogo enquanto o aplicativo está aberto."
            )
        if result.returncode != 1:
            detail = (result.stderr or result.stdout or "").strip()
            suffix = f": {detail}" if detail else ""
            raise InstallerError(f"Não foi possível verificar se o ezQuake está aberto{suffix}")

    def reset_macos_game_directory(self) -> None:
        if not self.is_native_macos_install():
            return
        self.clear_macos_game_directory()

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

    def clear_macos_game_directory(self) -> None:
        self.ensure_macos_ezquake_closed()
        for key in MACOS_DIRECTORY_KEYS:
            try:
                result = subprocess.run(
                    ["defaults", "delete", MACOS_PREFERENCES_DOMAIN, key],
                    check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
            except FileNotFoundError as error:
                raise InstallerError("O utilitário nativo defaults não foi encontrado no macOS.") from error
            detail = (result.stderr or result.stdout or "").strip()
            missing = "not found" in detail.casefold() or "does not exist" in detail.casefold()
            if result.returncode != 0 and not missing:
                suffix = f": {detail}" if detail else ""
                raise InstallerError(f"Não foi possível limpar a seleção antiga do ezQuake{suffix}")
        console.success("Seleção antiga do diretório do ezQuake removida do macOS.")

    def macos_app_is_sandboxed(self, app: Path) -> bool:
        if host_platform.system() != "Darwin":
            return False
        try:
            result = subprocess.run(
                ["codesign", "-d", "--entitlements", "-", str(app)],
                check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        except FileNotFoundError as error:
            raise InstallerError("O utilitário nativo codesign não foi encontrado no macOS.") from error
        detail = f"{result.stdout}\n{result.stderr}"
        if result.returncode != 0:
            raise InstallerError(f"Não foi possível ler os entitlements do ezQuake: {detail.strip()}")
        return "com.apple.security.app-sandbox" in detail

    def macos_app_uses_full_display(self, app: Path) -> bool:
        plist = app / "Contents/Info.plist"
        if not plist.is_file() or plist.is_symlink():
            raise InstallerError(f"Info.plist inválido no bundle macOS: {app}")
        try:
            with plist.open("rb") as source:
                metadata = plistlib.load(source)
        except (OSError, plistlib.InvalidFileException) as error:
            raise InstallerError(f"Info.plist inválido no bundle macOS: {app}") from error
        return metadata.get(MACOS_SAFE_AREA_KEY) is False

    def prepare_macos_nightly_app(self, app: Path) -> bool:
        if self.channel != "nightly":
            raise InstallerError(
                "A preparação local do bundle macOS é permitida somente no canal nightly."
            )
        if host_platform.system() != "Darwin":
            return False
        sandboxed = self.macos_app_is_sandboxed(app)
        full_display = self.macos_app_uses_full_display(app)
        if not full_display:
            plist = app / "Contents/Info.plist"
            original = plist.read_bytes()
            file_format = plistlib.FMT_BINARY if original.startswith(b"bplist00") else plistlib.FMT_XML
            metadata = plistlib.loads(original)
            metadata[MACOS_SAFE_AREA_KEY] = False
            descriptor, temporary_name = tempfile.mkstemp(prefix=".Info.plist.", dir=plist.parent)
            os.close(descriptor)
            temporary = Path(temporary_name)
            try:
                with temporary.open("wb") as destination:
                    plistlib.dump(metadata, destination, fmt=file_format, sort_keys=False)
                temporary.chmod(stat.S_IMODE(plist.stat().st_mode))
                temporary.replace(plist)
            finally:
                if lexists(temporary):
                    remove_path(temporary)
        if sandboxed:
            console.info("Ajustando o bundle macOS para acessar diretamente o diretório x86QW...")
        if not full_display:
            console.info("Preparando o fullscreen do ezQuake para utilizar toda a tela no macOS...")
        self.run_command(["codesign", "--force", "--deep", "--sign", "-", str(app)])
        self.run_command(["codesign", "--verify", "--deep", "--strict", str(app)])
        if self.macos_app_is_sandboxed(app):
            raise InstallerError(f"Não foi possível remover o sandbox incompatível de {app}.")
        if not self.macos_app_uses_full_display(app):
            raise InstallerError(f"Não foi possível habilitar o fullscreen integral em {app}.")
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
        for component in self.components:
            for relative in self.component_metadata(component):
                ensure_no_symlink(self.target / relative, "managed path")

    def resolve_cache_root(self) -> Path:
        if self._cache_root is not None:
            root = self._cache_root.absolute()
        else:
            system = host_platform.system()
            if system == "Darwin":
                base = self.run_command(["getconf", "DARWIN_USER_CACHE_DIR"], capture=True)
                if not base:
                    raise InstallerError("could not resolve the native macOS user cache directory")
                root = Path(base) / CACHE_DIR_NAME
            elif system == "Windows":
                base = os.environ.get("LOCALAPPDATA")
                if not base:
                    raise InstallerError("LOCALAPPDATA is not defined")
                root = Path(base) / CACHE_DIR_NAME
            else:
                base = os.environ.get("XDG_CACHE_HOME")
                root = (Path(base) if base else Path.home() / ".cache") / CACHE_DIR_NAME
        if not root.is_absolute() or root == Path(root.anchor):
            raise InstallerError(f"unsafe cache path: {root}")
        parent = root.parent
        if not parent.is_dir():
            parent.mkdir(parents=True, exist_ok=True)
        parent = parent.resolve()
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

    def validate_cache_marker_at(self, root: Path, marker_name: str, marker_value: str) -> None:
        marker = root / marker_name
        if not marker.is_file() or marker.is_symlink():
            raise InstallerError(f"O diretório de cache não pertence a este instalador e foi preservado: {root}")
        first_line = marker.read_text(encoding="utf-8").splitlines()[:1]
        if first_line != [marker_value]:
            raise InstallerError(f"O marcador de propriedade do cache é inválido: {marker}")

    def validate_cache_marker(self) -> None:
        assert self.cache_root is not None
        self.validate_cache_marker_at(self.cache_root, CACHE_MARKER_NAME, CACHE_MARKER_VALUE)

    def owned_cache_roots(self, *, include_legacy: bool) -> list[Path]:
        current = self.resolve_cache_root()
        candidates = [(current, CACHE_MARKER_NAME, CACHE_MARKER_VALUE)]
        if include_legacy and self._cache_root is None:
            legacy_name, legacy_marker, legacy_value = LEGACY_CACHE
            candidates.append((current.parent / legacy_name, legacy_marker, legacy_value))
        owned = []
        for root, marker_name, marker_value in candidates:
            if not lexists(root):
                continue
            ensure_no_symlink(root, "cache root")
            if not root.is_dir():
                raise InstallerError(f"O caminho reservado ao cache não é um diretório: {root}")
            self.validate_cache_marker_at(root, marker_name, marker_value)
            owned.append(root)
        return owned

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
        if lexists(root):
            if not root.is_dir():
                raise InstallerError(f"O caminho reservado ao cache não é um diretório: {root}")
            marker = root / CACHE_MARKER_NAME
            if lexists(marker):
                self.validate_cache_marker()
            elif any(root.iterdir()):
                raise InstallerError(f"O diretório de cache contém arquivos que não pertencem ao instalador e foi preservado: {root}")
        else:
            root.mkdir()
        (root / CACHE_MARKER_NAME).write_text(CACHE_MARKER_VALUE + "\n", encoding="utf-8")
        assert self.cache_bin is not None
        ensure_no_symlink(self.cache_bin, "cache directory")

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
        roots = self.owned_cache_roots(include_legacy=True)
        if not roots:
            console.info(f"Nenhum cache do instalador foi encontrado em {self.cache_root}.")
            return
        for root in roots:
            remove_path(root)
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

    def cleanup_runtime_data(self, *, downloads: bool, personal_data: bool) -> tuple[int, int]:
        if not self.target.is_dir() or self.target.is_symlink():
            console.info(f"Nenhuma instalação local foi encontrada em {self.target}.")
            return 0, 0
        removed_cache = 0
        removed_personal = 0

        for relative in ("ezquake/sb/cache", "ezquake/temp"):
            path = self.target / relative
            if lexists(path):
                remove_path(path, self.target.stat().st_dev)
                removed_cache += 1

        fortress = self.target / "fortress"
        if fortress.is_dir() and not fortress.is_symlink():
            for temporary in sorted(fortress.rglob("*.tmp")):
                if temporary.is_file() and not temporary.is_symlink():
                    remove_path(temporary)
                    removed_cache += 1

        demos = self.target / "td2/demos"
        if demos.is_dir() and not demos.is_symlink():
            for artifact in sorted(demos.iterdir()):
                if artifact.is_file() and not artifact.is_symlink() and artifact.stat().st_size == 0:
                    remove_path(artifact)
                    removed_cache += 1

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
                        remove_path(artifact)
                        removed_cache += 1

        if personal_data:
            personal = (
                "ezquake/.ezquake_history",
                "qw/qconsole.log",
                "logs",
                "td2/demos",
            )
            for relative in personal:
                path = self.target / relative
                if lexists(path):
                    remove_path(path, self.target.stat().st_dev)
                    removed_personal += 1

        for relative in (
            "ezquake/sb", "ezquake", "fortress/progs", "fortress/sound",
            "fortress", "td2/demos", "td2", "logs",
        ):
            remove_empty_directories(self.target / relative)
        return removed_cache, removed_personal

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

    def prepare_install_target(self) -> None:
        ensure_no_symlink(self.target, "installation target")
        if lexists(self.target) and not self.target.is_dir():
            raise InstallerError(f"O destino não é um diretório: {self.target}")
        self.target.mkdir(parents=True, exist_ok=True)
        id1 = self.target / "id1"
        ensure_no_symlink(id1, "id1 directory")
        if lexists(id1) and not id1.is_dir():
            raise InstallerError(f"O caminho id1 não é um diretório: {id1}")
        id1.mkdir(exist_ok=True)

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
        self.prepare_install_target()
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
                if os.name != "nt":
                    staged.chmod(0o644)
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
            if os.name != "nt" and runtime_platform.get("permissions") == "executable":
                destination.chmod(0o755)
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

    def choose_channel(self) -> str:
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
        return navigation.confirm(
            "Instalar também os componentes x86QW?",
            breadcrumb="x86QW › Instalação › Conteúdo",
            description="KTX, mapas, recursos visuais e componentes do perfil escolhido.",
            default=False,
            invalid_message="Resposta inválida. Digite s para sim ou n para não.",
        )

    def http_get(
        self,
        url: str,
        destination: Path | None = None,
        headers: dict[str, str] | None = None,
        *,
        expected_size: int | None = None,
        expected_sha256: str | None = None,
        maximum_size: int | None = None,
        timeout: float = 60.0,
        attempts: int = 3,
    ) -> bytes:
        try:
            validate_download_url(url, "URL de download")
        except DownloadPolicyError as error:
            raise InstallerError(str(error)) from error
        display_url = safe_url_for_log(url)
        console.detail(f"GET {display_url}")
        retry = RetryPolicy(attempts=attempts)
        request_headers = {"User-Agent": "x86-qw-installer/1", **(headers or {})}
        if destination is None:
            if maximum_size is None:
                raise InstallerError("O download de metadados exige um limite máximo explícito.")
            contract: PinnedArtifact | BoundedMetadata = BoundedMetadata(
                url=url,
                maximum_size=maximum_size,
                deadline_seconds=timeout,
                retry=retry,
                headers=request_headers,
                label="metadados x86QW",
            )
        else:
            if expected_size is None or expected_sha256 is None:
                raise InstallerError(
                    "O download de um artefato exige tamanho e SHA-256 esperados."
                )
            contract = PinnedArtifact(
                url=url,
                destination=destination,
                expected_size=expected_size,
                expected_sha256=expected_sha256,
                maximum_size=maximum_size if maximum_size is not None else expected_size,
                deadline_seconds=timeout,
                retry=retry,
                headers=request_headers,
                label=destination.name,
            )

        last_update = 0.0

        def progress(received: int, total: int | None, done: bool) -> None:
            nonlocal last_update
            now = time.monotonic()
            if done or now - last_update >= 0.1:
                console.download_progress(received, total, done=done)
                last_update = now

        def retry_notice(next_attempt: int, error: Exception, delay: float) -> None:
            console.detail(f"Tentativa de download falhou: {error}")
            console.warning(
                "Falha temporária no download. "
                f"Tentando novamente ({next_attempt}/{attempts}) em {delay:.1f}s..."
            )

        try:
            result = bounded_download(
                contract,
                progress=progress if destination is not None else None,
                on_retry=retry_notice,
            )
        except DownloadHTTPError as error:
            rate_limit_remaining = next((
                value for name, value in error.headers.items()
                if name.casefold() == "x-ratelimit-remaining"
            ), None)
            if error.status == 403 and rate_limit_remaining == "0":
                raise InstallerError(
                    "O limite temporário de consultas do GitHub foi atingido. Aguarde a renovação "
                    "ou defina GITHUB_TOKEN para ampliar o limite."
                ) from error
            console.detail(f"Tentativa de download falhou: {error}")
            raise InstallerError(f"Não foi possível baixar {display_url}: {error}") from error
        except DownloadError as error:
            console.detail(f"Tentativa de download falhou: {error}")
            raise InstallerError(f"Não foi possível baixar {display_url}: {error}") from error
        return result.data or b""

    def http_get_mirrors(
        self,
        urls: tuple[str, ...],
        destination: Path | None = None,
        headers: dict[str, str] | None = None,
        *,
        expected_size: int | None = None,
        expected_sha256: str | None = None,
        maximum_size: int | None = None,
        timeout: float = 60.0,
        attempts: int = 3,
        mirror_label: str = "Mirror",
    ) -> tuple[bytes, str]:
        if not urls:
            raise InstallerError("O download exige ao menos um mirror.")
        display_urls: list[str] = []
        for index, url in enumerate(urls, start=1):
            try:
                validate_download_url(url, f"URL do mirror {index}")
            except DownloadPolicyError as error:
                raise InstallerError(str(error)) from error
            display_urls.append(safe_url_for_log(url))
        retry = RetryPolicy(attempts=attempts)
        request_headers = {"User-Agent": "x86-qw-installer/1", **(headers or {})}
        if destination is None:
            if maximum_size is None:
                raise InstallerError("O download de metadados exige um limite máximo explícito.")
            contracts: tuple[PinnedArtifact | BoundedMetadata, ...] = tuple(
                BoundedMetadata(
                    url=url,
                    maximum_size=maximum_size,
                    deadline_seconds=timeout,
                    retry=retry,
                    headers=request_headers,
                    label="metadados x86QW",
                )
                for url in urls
            )
        else:
            if expected_size is None or expected_sha256 is None:
                raise InstallerError(
                    "O download de um artefato exige tamanho e SHA-256 esperados."
                )
            contracts = tuple(
                PinnedArtifact(
                    url=url,
                    destination=destination,
                    expected_size=expected_size,
                    expected_sha256=expected_sha256,
                    maximum_size=maximum_size if maximum_size is not None else expected_size,
                    deadline_seconds=timeout,
                    retry=retry,
                    headers=request_headers,
                    label=destination.name,
                )
                for url in urls
            )

        last_update = 0.0
        selected_index = 0

        def progress(received: int, total: int | None, done: bool) -> None:
            nonlocal last_update
            now = time.monotonic()
            if done or now - last_update >= 0.1:
                console.download_progress(received, total, done=done)
                last_update = now

        def retry_notice(next_attempt: int, error: Exception, delay: float) -> None:
            console.detail(f"Tentativa de download falhou: {error}")
            console.warning(
                "Falha temporária no download. "
                f"Tentando novamente ({next_attempt}/{attempts}) em {delay:.1f}s..."
            )

        def mirror_failure(index: int, contract: object, error: DownloadError) -> None:
            nonlocal selected_index
            del contract
            selected_index = index
            console.detail(str(error))
            if index < len(urls):
                host = urllib.parse.urlsplit(urls[index - 1]).hostname or urls[index - 1]
                console.warning(
                    f"{mirror_label} indisponível ou inválido em {host}; "
                    "tentando a próxima cópia..."
                )

        for display_url in display_urls:
            console.detail(f"GET {display_url}")
        try:
            result = bounded_download_mirrors(
                contracts,
                progress=progress if destination is not None else None,
                on_retry=retry_notice,
                on_mirror_failure=mirror_failure,
            )
        except DownloadHTTPError as error:
            rate_limit_remaining = next((
                value for name, value in error.headers.items()
                if name.casefold() == "x-ratelimit-remaining"
            ), None)
            if error.status == 403 and rate_limit_remaining == "0":
                raise InstallerError(
                    "O limite temporário de consultas do GitHub foi atingido. Aguarde a renovação "
                    "ou defina GITHUB_TOKEN para ampliar o limite."
                ) from error
            console.detail(f"Tentativa de download falhou: {error}")
            raise InstallerError(f"O download por mirrors falhou: {error}") from error
        except DownloadError as error:
            console.detail(f"Tentativa de download falhou: {error}")
            raise InstallerError(f"O download por mirrors falhou: {error}") from error
        return result.data or b"", urls[selected_index]

    def catalog_records(
        self,
        component: str,
        channel: str,
        version_pattern: re.Pattern[str],
        expected_filename: Callable[[str], str],
        architecture: str,
    ) -> list[ReleaseRecord]:
        assert self.spec is not None
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

    def choose_release(self) -> None:
        assert self.spec is not None
        catalog = self.stable_catalog() if self.channel == "stable" else self.nightly_catalog()
        self.configure_release(self.prompt_catalog(self.channel, catalog))
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
            if file_hash(archive, self.app_checksum_kind) != self.app_expected_checksum:
                raise InstallerError(f"O arquivo em cache falhou na verificação: {archive}. Execute cleanup e tente novamente.")
            if self.update_ui:
                console.download_result(
                    f"ezQuake {self.selected_version}", size=archive.stat().st_size, status="Cached",
                )
            else:
                console.info(f"Usando arquivo já disponível no cache: {self.app_archive_name}")
                console.success("Arquivo do cache validado.")
        else:
            local = self.distribution_artifact(
                self.app_distribution_path, self.app_archive_name,
                expected_size=self.app_expected_size or None, expected_sha256=self.app_expected_checksum,
            ) if self.app_distribution_path else None
            if local is not None:
                shutil.copy2(local, archive)
                if self.update_ui:
                    console.download_result(
                        f"ezQuake {self.selected_version}", size=archive.stat().st_size, status="Loaded",
                    )
                else:
                    console.success(f"Artefato carregado da distribuição local: {self.app_distribution_path}")
                self.app_archive_sha256 = file_hash(archive)
                return archive
            download = self.stage / f"{self.app_archive_name}.download"
            if not self.update_ui:
                console.info(f"Baixando {self.app_archive_name}...")
            _, self.app_url = self.http_get_mirrors(
                self.app_urls or (self.app_url,),
                download,
                expected_size=self.app_expected_size,
                expected_sha256=self.app_expected_checksum,
                maximum_size=MAX_ARTIFACT_BYTES,
            )
            if file_hash(download, self.app_checksum_kind) != self.app_expected_checksum:
                raise InstallerError(
                    "O arquivo baixado falhou na verificação: "
                    f"{safe_url_for_log(self.app_url)}"
                )
            download.replace(archive)
            if self.update_ui:
                console.download_result(
                    f"ezQuake {self.selected_version}", size=archive.stat().st_size,
                )
            else:
                console.success(f"Download concluído e validado ({format_bytes(archive.stat().st_size)}).")
        self.app_archive_sha256 = file_hash(archive)
        console.detail(f"SHA-256 local: {self.app_archive_sha256}")
        return archive

    def inspect_macos_app(self, app: Path) -> tuple[str, str]:
        binary = app / "Contents/MacOS/ezQuake"
        plist = app / "Contents/Info.plist"
        code_resources = app / "Contents/_CodeSignature/CodeResources"
        if not app.is_dir() or not binary.is_file() or binary.is_symlink() or not plist.is_file():
            raise InstallerError(f"invalid ezQuake app bundle: {app}")
        if not code_resources.is_file():
            raise InstallerError(f"missing app code signature resources: {app}")
        with plist.open("rb") as source:
            metadata = plistlib.load(source)
        version = metadata.get("CFBundleShortVersionString")
        if not isinstance(version, str) or version != metadata.get("CFBundleVersion"):
            raise InstallerError(f"bundle version fields disagree in {app}")
        data = binary.read_bytes()[:4096]
        if len(data) < 8:
            raise InstallerError(f"invalid Mach-O executable: {binary}")
        magic, count = struct.unpack_from(">II", data)
        if magic not in (0xCAFEBABE, 0xCAFEBABF) or count < 2 or count > 32:
            raise InstallerError(f"expected universal Mach-O executable: {binary}")
        entry_size = 20 if magic == 0xCAFEBABE else 32
        if len(data) < 8 + count * entry_size:
            raise InstallerError(f"invalid universal Mach-O header: {binary}")
        architectures = set()
        for index in range(count):
            cpu_type = struct.unpack_from(">I", data, 8 + index * entry_size)[0]
            architectures.add(cpu_type)
        if 0x01000007 not in architectures or 0x0100000C not in architectures:
            raise InstallerError(f"{app} does not contain arm64 and x86_64")
        if host_platform.system() == "Darwin":
            self.run_command(["codesign", "--verify", "--deep", "--strict", str(app)])
        return version, file_hash(binary)

    def inspect_portable_binary(self, spec: PlatformSpec, binary: Path) -> str:
        if not binary.is_file() or binary.is_symlink() or binary.stat().st_size == 0:
            raise InstallerError(f"invalid ezQuake binary: {binary}")
        with binary.open("rb") as source:
            header = source.read(512)
        if spec.key == "linux":
            if len(header) < 20 or header[:5] != b"\x7fELF\x02" or struct.unpack_from("<H", header, 18)[0] != 62:
                raise InstallerError(f"unexpected Linux binary format: {binary}")
            if os.name != "nt" and not os.access(binary, os.X_OK):
                raise InstallerError(f"Linux AppImage is not executable: {binary}")
        elif spec.key == "windows":
            if len(header) < 64 or header[:2] != b"MZ":
                raise InstallerError(f"unexpected Windows binary format: {binary}")
            pe_offset = struct.unpack_from("<I", header, 0x3C)[0]
            with binary.open("rb") as source:
                source.seek(pe_offset)
                pe = source.read(26)
            if len(pe) < 26 or pe[:4] != b"PE\0\0" or struct.unpack_from("<H", pe, 4)[0] != 0x8664 or struct.unpack_from("<H", pe, 24)[0] != 0x20B:
                raise InstallerError(f"unexpected Windows binary format: {binary}")
        else:
            raise InstallerError(f"unsupported portable binary platform: {spec.key}")
        return file_hash(binary)

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
            if self.spec.key == "linux" and os.name != "nt":
                prepared.chmod(0o755)
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
    ) -> dict[str, str]:
        runtime = self.target / spec.runtime(channel)
        if channel == "stable":
            raise InstallerError(
                "O ezQuake stable deve ser restaurado do artefato upstream integral pelo bootstrap."
            )
        if spec.key != "macos" or not self.macos_app_needs_preparation(runtime):
            return receipt
        self.spec = spec
        self.channel = channel
        self.ensure_macos_ezquake_closed()
        self.prepare_macos_nightly_app(runtime)
        _, binary_hash = self.inspect_macos_app(runtime)
        updated = dict(receipt)
        updated["binary_sha256"] = binary_hash
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{receipt_path.name}.", dir=receipt_path.parent)
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            self.write_ezquake_receipt_record(temporary, updated)
            self.validate_ezquake_receipt(temporary, spec, channel)
            temporary.replace(receipt_path)
        finally:
            if lexists(temporary):
                remove_path(temporary)
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
        token = RuntimeReceiptRollback(destination, None, [])
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
        receipt.parent.mkdir(parents=True, exist_ok=True)
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

    def validate_managed_path(self, value: str) -> None:
        if not value or "\\" in value or ":" in value:
            raise InstallerError(f"unsafe path in managed inventory: {value}")
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
            raise InstallerError(f"unsafe path in managed inventory: {value}")
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

    def _read_inventory(self, path: Path) -> tuple[bytes, list[tuple[str, str]]]:
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
        for entry in parsed:
            self.validate_managed_path(entry.path)
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
        self, destination: Path, entries: Iterable[tuple[str, str]],
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
        self.validate_inventory(destination)

    def component_metadata(self, component: str) -> tuple[str, str]:
        known = {
            *self.components, *LEGACY_COMPONENTS,
            "maps", "presets", "play-support", "package-order",
        }
        if component not in known:
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
        self, component: str, receipt_path: Path, inventory_path: Path,
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
        inventory_payload, entries = self._read_inventory(inventory_path)
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
        if errors:
            raise InstallerError(
                "Rollback dos metadados ficou incompleto: " + "; ".join(errors)
            )

    def commit_component_metadata(
        self, component: str, inventory: Path, receipt: Path,
    ) -> ComponentMetadataRollback:
        assert self.stage is not None
        self.ensure_metadata_directory()
        metadata = self.target / METADATA_DIR
        destination = self.metadata_path(metadata, self.component_metadata(component)[0]).parent
        destination.parent.mkdir(parents=True, exist_ok=True)
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
        self.validate_component_paths(component, prepared / "receipt", prepared / "inventory")
        token = ComponentMetadataRollback(
            destination=destination,
            installed_identity=None,
            previous=previous,
            previous_identity=None,
            legacy_backups=[],
        )
        try:
            if lexists(destination):
                destination.replace(previous)
                token.previous_identity = self._directory_identity(previous)
            prepared.replace(destination)
            token.installed_identity = self._directory_identity(destination)
            for index, legacy in enumerate(
                self.component_pair_paths(component, metadata, legacy=True), 1,
            ):
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
            target.write_text("".join(f"{name}\n" for name in packages), encoding="utf-8")
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

    def verify_qw_package_order(self) -> None:
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
        self.verify_component("package-order")
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

    def _prune_component_directories(self) -> None:
        for name in (
            "qw/maps", "ezquake/configs", "arena", "prox", "fortress", "td2",
            "qtv", "qwfwd", "docs/licenses", "docs",
        ):
            remove_empty_directories(self.target / name)
        remove_empty_directories(self.target / COMPONENT_METADATA_DIR)
        remove_empty_directories(self.target / METADATA_DIR)

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
            self._prune_component_directories()
            return removed
        except MutationRollbackError:
            cleanup = False
            raise
        finally:
            if owned_stage and cleanup:
                self.cleanup_stage()
                self.stage = None

    def verify_component(self, component: str) -> int:
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
        console.success(f"Componente {component} íntegro ({file_count(len(entries))}; seleção {receipt['selection']}).")
        return len(entries)

    def manage_presets(self) -> None:
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
        if action == "remove":
            removed = self.remove_component("presets")
            console.success(f"Presets gerenciados removidos ({file_count(removed)}); configurações pessoais preservadas.")
            return
        self.check_paks()
        self._create_stage(".quake-install.")
        managed = self.stage / "presets-managed"
        configs = managed / "ezquake/configs"
        configs.mkdir(parents=True)
        for name, contents in PRESETS.items():
            (configs / name).write_text(contents, encoding="utf-8")
        count = self.install_component_overlay("presets", managed, "v1", "x86-qw built-in presets")
        console.success(f"Presets instalados ({file_count(count)}). Carregue um deles com cfg_load x86-qw-modern.")

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
        return frozenset({"none", "custom", *self.component_catalog["profiles"]})

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
            return parse_install_state(
                state,
                allowed_profiles=self._install_state_profiles(),
                allowed_capabilities=INSTALLATION_CAPABILITIES,
            )
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
        state = self.validate_install_state({
            "format": 2,
            "project": "x86qw",
            "profile": profile,
            "requested_components": list(requested),
            "recorded_components": recorded,
            "known_components": list(self.components) if known is None else list(known),
            "capabilities": [] if capabilities is None else list(capabilities),
            "component_fingerprint": profile_fingerprint(recorded),
        })
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
                    committed=error.committed,
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

    def load_install_state(self, *, persist_migration: bool) -> dict[str, object]:
        path = self.target / INSTALL_STATE
        if path.is_file() and not path.is_symlink():
            state = self.read_install_state_document(path)
            original = state
            state = self.current_install_state(state)
            migrated = self.migrate_stale_custom_profile(state)
            if migrated != original:
                profile = str(migrated["profile"])
                if persist_migration:
                    migrated = self.write_install_state(
                        profile, list(migrated["requested_components"]),
                        known=list(migrated["known_components"]),
                        capabilities=list(migrated["capabilities"]),
                    )
                    console.success(f"Estado histórico da instalação migrado para o formato 2: {profile}.")
                else:
                    console.info(f"Migração do estado para o formato 2 prevista na simulação: {profile}.")
            return migrated
        if lexists(path):
            raise InstallerError(f"Estado da instalação inválido: {path}")
        state = self.infer_install_state()
        if persist_migration:
            state = self.write_install_state(
                str(state["profile"]), list(state["requested_components"]),
                known=list(state["known_components"]),
                capabilities=list(state["capabilities"]),
            )
            console.success(f"Perfil da instalação registrado: {state['profile']}.")
        else:
            console.info(f"Perfil inferido para a simulação: {state['profile']}.")
        return state

    def read_install_state_document(self, path: Path) -> dict[str, object]:
        try:
            return read_install_state(
                path,
                allowed_profiles=self._install_state_profiles(),
                allowed_capabilities=INSTALLATION_CAPABILITIES,
            ).to_document()
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
        console.success(f"{len(selected)} componente(s) selecionado(s).")
        print("\nVersões que serão instaladas ou atualizadas:")
        for identifier in selected:
            package = self.component_package_record(identifier)
            print(f"  - {self.components[identifier]['label']}: {package['version']}")
            if package.get("release_url"):
                print(f"    novidades: {safe_url_for_log(package['release_url'])}")
        return selected

    def component_package_record(self, identifier: str) -> dict[str, object]:
        catalog = self.public_catalog("Consultando o catálogo atual de componentes x86QW...")
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
        if not isinstance(version, str) or not STABLE_VERSION.fullmatch(version):
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
            or not STABLE_VERSION.fullmatch(version)
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
            command = [
                sys.executable, str(application), "--online-only", "--installed-cli", "--skip-cli-update",
            ]
            if console.verbose:
                command.append("--verbose")
            if not console.color:
                command.append("--no-color")
            if dry_run:
                command.append("--dry-run")
            if assume_yes:
                command.append("--yes")
            command.extend([action, str(self.target)])
            result = subprocess.run(command, check=False)
            if result.returncode:
                raise InstallerError(f"A atualização x86QW terminou com código {result.returncode}.")
            return True
        finally:
            self.cleanup_stage()

    @staticmethod
    def confirm_update_plan(action: str, *, assume_yes: bool) -> bool:
        if assume_yes:
            console.info("Plano confirmado automaticamente por --yes.")
            return True
        if not navigation.supports_navigation():
            while True:
                try:
                    answer = input(
                        f"\n==> Deseja executar o plano de {action}? [y/n] "
                    ).strip().lower()
                except EOFError as error:
                    raise InstallerError(
                        "A confirmação não pôde ser lida. Execute em um terminal interativo "
                        "ou use --yes para confirmar o plano automaticamente."
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
                description="aplicar as alterações apresentadas",
                default=False,
            )
        except navigation.MenuCancelled as error:
            raise InstallerError(
                "A confirmação não pôde ser lida. Execute em um terminal interativo "
                "ou use --yes para confirmar o plano automaticamente."
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
                    catalog_payload = self.http_get(
                        catalog_url,
                        maximum_size=CATALOG_MAX_BYTES,
                        timeout=CATALOG_TIMEOUT,
                        attempts=2,
                    )
                    catalog = json.loads(catalog_payload)
                    catalog_status = "Baixado"
                    console.detail(f"Catálogo remoto explícito: {safe_url_for_log(catalog_url)}")
                elif not self.online_only and local_catalog.is_file() and not local_catalog.is_symlink():
                    catalog_payload = local_catalog.read_bytes()
                    catalog = json.loads(catalog_payload)
                    console.detail(f"Catálogo da distribuição local: {local_catalog}")
                else:
                    catalog_payload, selected_url = self.http_get_mirrors(
                        CATALOG_URLS,
                        maximum_size=CATALOG_MAX_BYTES,
                        timeout=CATALOG_TIMEOUT,
                        attempts=1,
                        mirror_label="Catálogo",
                    )
                    catalog = json.loads(catalog_payload)
                    catalog_status = "Baixado"
                    console.detail(f"Catálogo público: {safe_url_for_log(selected_url)}")
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
        if file_hash(candidate) != expected_sha256:
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
            if artifact.stat().st_size != package["size"] or file_hash(artifact) != digest:
                raise InstallerError(f"O pacote {identifier} em cache é inválido. Execute cleanup e tente novamente.")
            if self.update_ui:
                console.download_result(
                    f"{identifier} {package['version']}", size=artifact.stat().st_size, status="Cached",
                )
            else:
                console.detail(f"Pacote validado no cache: {artifact}")
            return artifact
        distribution_path = package.get("distribution_path")
        if isinstance(distribution_path, str) and distribution_path:
            local = self.distribution_artifact(
                distribution_path, filename,
                expected_size=int(package["size"]), expected_sha256=digest,
            )
            if local is not None:
                shutil.copy2(local, artifact)
                if self.update_ui:
                    console.download_result(
                        f"{identifier} {package['version']}", size=artifact.stat().st_size, status="Loaded",
                    )
                else:
                    console.success(f"Pacote carregado da distribuição local: {distribution_path}")
                return artifact
        temporary = self.stage / f"{identifier}.download"
        self.http_get_mirrors(
            tuple(str(url) for url in package["urls"]),
            temporary,
            expected_size=int(package["size"]),
            expected_sha256=digest,
            maximum_size=MAX_ARTIFACT_BYTES,
        )
        if temporary.stat().st_size != package["size"] or file_hash(temporary) != digest:
            raise InstallerError(f"Um mirror entregou um pacote inválido: {identifier}")
        temporary.replace(artifact)
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
            if os.name != "nt":
                destination.chmod(0o644)
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

    def migrate_legacy_nquake(self) -> None:
        present, entries, _ = self.validate_nquake_pair()
        if not present:
            return
        console.info("Migrando o recibo nQuake antigo para componentes independentes...")
        for name, expected in entries:
            managed = self.target.joinpath(*PurePosixPath(name).parts)
            if not lexists(managed):
                continue
            if not managed.is_file() or managed.is_symlink():
                raise InstallerError(f"Caminho legado gerenciado inválido: {managed}")
            if file_hash(managed) == expected:
                remove_path(managed)
            else:
                console.warning(f"Arquivo modificado preservado durante a migração: {managed}")
        remove_path(self.target / NQUAKE_RECEIPT)
        remove_path(self.target / NQUAKE_INVENTORY)

    def migrate_legacy_clan_arena(self, selected: list[str]) -> None:
        if not {"final-arena", "pro-x"} & set(selected):
            return
        present, _, _ = self.validate_component_pair("clan-arena")
        if not present:
            return
        console.info("Separando o componente antigo Clan Arena e Pro-X...")
        removed = self.remove_component("clan-arena")
        console.success(
            f"Recibo combinado removido ({file_count(removed)}); arquivos modificados foram preservados."
        )

    def migrate_legacy_component_replacements(self, selected: list[str]) -> None:
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
            removed = self.remove_component(legacy)
            console.success(
                f"Componente legado {legacy} removido ({file_count(removed)}); "
                "arquivos modificados foram preservados."
            )

    def release_play_support_profiles(self, selected: list[str]) -> None:
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
                remove_path(managed)
            else:
                console.warning(f"Perfil modificado preservado durante a migração: {managed}")
        if not changed:
            return
        if not remaining:
            metadata = self.target / METADATA_DIR
            canonical = self.component_pair_paths("play-support", metadata)
            remove_path(canonical[0].parent)
            for path in self.component_pair_paths("play-support", metadata, legacy=True):
                remove_path(path)
            return
        assert self.stage is not None and receipt is not None
        inventory = self.stage / "play-support-migrated.inventory"
        self.write_inventory_record(inventory, remaining)
        staged_receipt = self.stage / "play-support-migrated.receipt"
        self.write_component_receipt(
            "play-support", receipt["selection"], receipt["source"], inventory, staged_receipt,
        )
        self.commit_component_metadata("play-support", inventory, staged_receipt)

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
    def component_state_transaction(self) -> Iterator[list[MutationResult]]:
        """Keep managed inverses alive until the parent state commit resolves."""

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
            metadata = destination.lstat()
            identity = (int(metadata.st_dev), int(metadata.st_ino))
            if (
                destination.is_file()
                and not destination.is_symlink()
                and identity == token.identity
                and file_hash(destination) == token.digest
            ):
                if os.name == "nt":
                    private_fs.unlink_private_file(
                        destination, expected_identity=token.identity,
                    )
                else:
                    destination.unlink()
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
                metadata = directory.lstat()
                if (
                    directory.is_symlink()
                    or not directory.is_dir()
                    or (int(metadata.st_dev), int(metadata.st_ino)) != identity
                ):
                    continue
                try:
                    directory.rmdir()
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
                metadata = directory.lstat()
                created.append((directory, (int(metadata.st_dev), int(metadata.st_ino))))
            descriptor = private_fs.create_private_file(destination)
            identity_metadata = os.fstat(descriptor)
            identity = (int(identity_metadata.st_dev), int(identity_metadata.st_ino))
            copied = hashlib.sha256()
            with source.open("rb") as input_file, os.fdopen(
                descriptor, "wb", closefd=False,
            ) as output:
                for block in iter(lambda: input_file.read(1024 * 1024), b""):
                    output.write(block)
                    copied.update(block)
                output.flush()
                if os.name != "nt":
                    os.fchmod(descriptor, 0o644)
                os.fsync(descriptor)
            if copied.hexdigest() != digest:
                raise InstallerError(f"Configuração inicial copiada divergiu: {destination}")
            token = CreatedDefaultRollback(destination, digest, identity, tuple(created))
            return token
        except BaseException:
            if token is None and descriptor >= 0:
                metadata = os.fstat(descriptor)
                identity = (int(metadata.st_dev), int(metadata.st_ino))
                os.close(descriptor)
                descriptor = -1
                try:
                    if os.name == "nt":
                        private_fs.unlink_private_file(
                            destination, expected_identity=identity,
                        )
                    else:
                        destination.unlink()
                except OSError:
                    pass
            for directory, identity in reversed(created):
                try:
                    metadata = directory.lstat()
                    if (int(metadata.st_dev), int(metadata.st_ino)) == identity:
                        directory.rmdir()
                except OSError:
                    pass
            raise
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def install_component_default_transaction(
        self, source: Path, destination: Path,
    ) -> MutationResult | None:
        if lexists(destination):
            return None
        digest = file_hash(source)
        plan = MutationPlan(
            identifier=f"component-default:{destination.relative_to(self.target).as_posix()}",
            summary=f"Criar configuração inicial {destination}",
            steps=(MutationStep(
                key="default",
                description=f"Criar configuração inicial {destination.name}",
                observe=lambda: (
                    self._mutation_path_observation(source),
                    file_hash(source),
                    self._mutation_path_observation(destination),
                ),
                apply=lambda: self._apply_created_default(source, destination, digest),
                rollback=self._rollback_created_default,
            ),),
        )
        return execute_mutation(prepare_mutation(plan))

    def install_components(self, selected: list[str]) -> tuple[MutationResult, ...]:
        assert self.stage is not None
        results: list[MutationResult] = []
        try:
            self.migrate_legacy_nquake()
            self.migrate_legacy_clan_arena(selected)
            self.migrate_legacy_component_replacements(selected)
            self.release_play_support_profiles(selected)
            for index, identifier in enumerate(selected, 1):
                component = self.components[identifier]
                console.info(f"[{index}/{len(selected)}] Preparando {component['label']}...")
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
                results.append(result)
                for staged, destination in defaults:
                    default_result = self.install_component_default_transaction(
                        staged, destination,
                    )
                    if default_result is not None:
                        results.append(default_result)
                        console.info(f"Configuração inicial criada: {destination}")
                if identifier == "nquake-bootstrap":
                    self.migrate_nquake_texture_limit(results)
                console.success(f"{component['label']} atualizado ({file_count(count)}).")
            if "nquake-bootstrap" in selected:
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
            return tuple(results)
        except BaseException as error:
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
                stat.S_IMODE(destination.lstat().st_mode),
            )
            for destination, payload in replacements
        ]
        counters = {"backup": 0, "config": 0}
        for kind, destination, payload, source_mode in prepared_entries:
            counters[kind] += 1
            index = counters[kind]
            prepared = workspace / f"{kind}-{index}"
            prepared.write_bytes(payload)
            if os.name != "nt":
                prepared.chmod(source_mode)
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

    def write_personal_config(self, path: Path, payload: bytes) -> None:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload)
            if os.name != "nt":
                temporary.chmod(0o644)
            temporary.replace(path)
        finally:
            if lexists(temporary):
                remove_path(temporary)

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
                        backup, contents, stat.S_IMODE(prox.lstat().st_mode),
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
                        backup, original, stat.S_IMODE(config.lstat().st_mode),
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

    def manage_components(self) -> None:
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
            with self.component_state_transaction() as mutation_results:
                if selected and self.stage is None:
                    self._create_stage(".x86qw-components-remove.")
                for identifier in selected:
                    removed, result = self.remove_component_transaction(identifier)
                    mutation_results.append(result)
                    console.success(
                        f"{self.components[identifier]['label']} removido "
                        f"({file_count(removed)})."
                    )
                self.refresh_qw_package_order(mutation_results=mutation_results)
                self.reconcile_play_support_transaction(
                    mutation_results=mutation_results,
                )
                self._prune_component_directories()
                self.write_install_state(
                    "custom" if self.installed_components() else "none",
                    self.installed_components(),
                    mutation_results=mutation_results,
                )
            return
        selected = self.choose_components()
        self._create_stage(".quake-install.")
        results = self.install_components(selected)
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

    def install_component_phase(self) -> tuple[MutationResult, ...]:
        assert self.stage is not None
        console.section("Fase 2/2 · Componentes x86QW")
        selected = self.choose_components()
        results = self.install_components(selected)
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
        return results

    def hub_servers(self) -> list[dict[str, object]]:
        console.info("Consultando servidores ativos no QuakeWorld Hub...")
        try:
            servers = json.loads(self.http_get(
                HUB_SERVERS_API,
                maximum_size=HUB_MAX_BYTES,
                timeout=CATALOG_TIMEOUT,
            ))
        except (json.JSONDecodeError, TypeError) as error:
            raise InstallerError("O Hub retornou um catálogo de servidores inválido.") from error
        if not isinstance(servers, list):
            raise InstallerError("O Hub retornou um catálogo de servidores inválido.")
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
        if not valid:
            raise InstallerError("Nenhum servidor ativo reconhecido foi retornado pelo Hub.")
        return sorted(
            valid,
            key=lambda item: sum(
                1 for player in item.get("players", [])
                if isinstance(player, dict) and not player.get("is_bot")
            ),
            reverse=True,
        )

    def host_runtimes(self) -> list[tuple[str, Path]]:
        platform_key = HOST_PLATFORMS.get(host_platform.system())
        if platform_key is None:
            raise InstallerError(f"A abertura automática não é suportada neste sistema: {host_platform.system()}.")
        choices: list[tuple[str, Path]] = []
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
    ) -> subprocess.Popen[bytes]:
        system = host_platform.system()
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
        if system == "Darwin":
            executable = runtime / "Contents/MacOS/ezQuake"
            if not executable.is_file() or executable.is_symlink():
                raise InstallerError(f"Executável do bundle macOS não encontrado: {executable}")
            # LaunchServices can drop command-line arguments from an already known
            # app bundle, which makes ezQuake reopen its game-directory picker.
            command = [str(executable), *base_arguments, *quake_arguments]
        else:
            command = [str(runtime), *base_arguments, *quake_arguments]
        console.detail("$ " + " ".join(command))
        try:
            options: dict[str, object] = {
                "cwd": self.target,
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            if system != "Windows":
                options["start_new_session"] = True
            return subprocess.Popen(command, **options)
        except OSError as error:
            raise InstallerError(f"Não foi possível abrir {runtime}: {error}") from error

    def browse_hub(self) -> None:
        servers = self.hub_servers()
        interactive_menu = navigation.supports_navigation()
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
        legacy_action = None
        if not interactive_menu:
            print("\nServidores QuakeWorld ativos:")
            for index, option in enumerate(server_options, 1):
                print(f"  {index:3d}) {option.description} · {option.label}")
            print("\nDigite um número para jogar, oN para observar, qN para usar QTV, ou Enter para sair.")
            while True:
                try:
                    answer = input("Escolha: ").strip().lower()
                except EOFError:
                    answer = ""
                if not answer:
                    selected = None
                    break
                match = re.fullmatch(r"([oq]?)([0-9]+)", answer)
                if match and 1 <= int(match.group(2)) <= len(servers):
                    selected = str(int(match.group(2)) - 1)
                    legacy_action = {"": "join", "o": "observe", "q": "qtv"}[match.group(1)]
                    break
                console.warning(f"Escolha inválida. Use 1 a {len(servers)}, oN ou qN.")
        else:
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
        server = servers[int(selected)]
        address = str(server["address"])
        qtv = server.get("qtv_stream")
        qtv_url = qtv.get("url", "") if isinstance(qtv, dict) else ""
        has_qtv = isinstance(qtv_url, str) and re.fullmatch(
            r"[0-9]+@[A-Za-z0-9_.:\[\]-]+:[0-9]{1,5}", qtv_url,
        )
        action = legacy_action or navigation.select_one(
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
            console.info("Hub fechado; nenhum cliente foi aberto.")
            return
        if action == "qtv":
            if not has_qtv:
                qtv = server.get("qtv_stream")
                qtv_url = qtv.get("url", "") if isinstance(qtv, dict) else ""
                raise InstallerError("Este servidor não publicou um stream QTV válido.")
            quake_arguments = ["+qtvplay", qtv_url]
            operation = "QTV"
        elif action == "observe":
            quake_arguments = ["+observe", address]
            operation = "observação"
        else:
            quake_arguments = ["+join", address]
            operation = "conexão"
        while True:
            runtime_choice = self.choose_host_runtime()
            if runtime_choice is None:
                console.info("Conexão cancelada; nenhum cliente foi aberto.")
                return
            label, runtime = runtime_choice
            if not interactive_menu:
                break
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
                continue
            if not confirmed:
                console.info("Conexão cancelada; nenhum cliente foi aberto.")
                return
            break
        self.launch_runtime(runtime, quake_arguments)
        console.success(f"{label} aberto para {operation} em {address}.")

    def verify_ezquake_variants(self) -> int:
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
                console.success(f"ezQuake {spec.label} {channel} {receipt['selection']} íntegro.")
                verified += 1
        return verified

    def verify_installation(self) -> None:
        self.check_paks()
        runtime_count = self.verify_ezquake_variants()
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
            state = self.load_install_state(persist_migration=False)
            recorded = set(state["recorded_components"])
            if recorded != set(installed):
                raise InstallerError(
                    "O estado da instalação diverge dos componentes registrados. Execute update para reconciliar."
                )
        if not installed:
            console.info("Nenhum componente x86QW está instalado.")
        for identifier in installed:
            self.verify_component(identifier)
        if lexists(self.target / "id1/gpl_maps.pk3"):
            raise InstallerError("shareware gpl_maps.pk3 must not be installed with registered PAKs")
        self.verify_component("maps")
        self.verify_component("presets")
        player = self.play_support_player()
        player.verify_local_play_support(player.available_local_games())
        self.verify_qw_package_order()
        self.report_nquake_startup_state(installed)

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
                    and os.name != "nt"
                    and runtime.is_file()
                    and not runtime.is_symlink()
                    and file_hash(runtime) == receipt["binary_sha256"]
                    and not os.access(runtime, os.X_OK)
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
        if os.name == "nt":
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
                    and not os.access(binary, os.X_OK)
                ):
                    seen.add(binary)
                    repairs.append(binary)
        return repairs

    def _rollback_runtime_permission(self, token: RuntimePermissionRollback) -> None:
        if self._regular_identity(token.path) != token.identity:
            raise InstallerError(
                f"Runtime mudou durante o rollback da permissão: {token.path}"
            )
        token.path.chmod(token.mode)

    def _apply_runtime_permission(self, path: Path) -> RuntimePermissionRollback:
        identity = self._regular_identity(path)
        mode = stat.S_IMODE(path.lstat().st_mode)
        token = RuntimePermissionRollback(path, identity, mode)
        try:
            path.chmod(mode | stat.S_IXUSR)
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
                        issue.spec, issue.channel, issue.receipt_path, issue.receipt,
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
            state = assessment.recovered_state or self.load_install_state(persist_migration=False)
            self.write_install_state(
                str(state["profile"]), list(state["requested_components"]),
                known=list(state["known_components"]), capabilities=list(state["capabilities"]),
                mutation_results=mutation_results,
            )
            self.verify_installation()
        return True

    def report_nquake_startup_state(self, installed: list[str] | None = None) -> None:
        installed = self.installed_components() if installed is None else installed
        if "nquake-bootstrap" not in installed:
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
        return tuple(dict.fromkeys((
            *self.components, *LEGACY_COMPONENTS,
            "maps", "presets", "play-support", "package-order",
        )))

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
        canonical.parent.mkdir(parents=True, exist_ok=True)
        prepared = self.stage / f".{key}.next"
        shutil.copy2(legacy, prepared)
        validate(prepared)
        plan = MutationPlan(
            identifier=f"metadata-layout:{key}",
            summary=f"Migrar metadado {key} para o layout contextual",
            steps=(
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
                remove_empty_directories(metadata / "clients")
                remove_empty_directories(metadata / "components")
                if created_stage and mutation_results is None:
                    self.cleanup_stage()
        if mutation_results is not None:
            mutation_results.extend(completed)
        remove_empty_directories(metadata / "clients/ezquake")
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

    def uninstall(self) -> None:
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
        if present:
            for name, expected in entries:
                managed = self.target.joinpath(*PurePosixPath(name).parts)
                if lexists(managed):
                    if file_hash(managed) == expected:
                        remove_path(managed)
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
                remove_path(self.target / spec.runtime(channel))
                remove_path(receipt_path)
        for component in (
            "package-order", "play-support", "presets", "maps",
            *reversed(tuple(self.components)), *LEGACY_COMPONENTS,
        ):
            self.remove_component(component)
        remove_path(self.target / NQUAKE_RECEIPT)
        remove_path(self.target / NQUAKE_INVENTORY)
        remove_path(self.target / INSTALL_STATE)
        for name in ("arena", "prox", "fortress", "qw", "ezquake"):
            remove_empty_directories(self.target / name)
        self.remove_installed_cli()
        remove_empty_directories(self.target / METADATA_DIR)
        for relative, expected in preserved.items():
            if file_hash(self.target / relative) != expected:
                raise InstallerError(f"{relative} changed during uninstall")
        console.success(f"Componentes gerenciados removidos de {self.target}.")
        console.info("PAKs registrados e arquivos pessoais foram preservados.")

    def remove_installed_cli(self) -> None:
        removed = False
        for path in (
            self.target / METADATA_DIR / "cli",
            self.target / LEGACY_CLI_RECEIPT,
            self.target / "x86qw.sh",
        ):
            if lexists(path):
                remove_path(path)
                removed = True
        windows_launcher = self.target / "x86qw.cmd"
        if os.name != "nt" and lexists(windows_launcher):
            remove_path(windows_launcher)
            removed = True
        if removed:
            console.success("CLI permanente x86QW removida.")

    def purge(self, *, preserve_operation_lock: bool = False) -> None:
        caches = self.owned_cache_roots(include_legacy=True)
        if lexists(self.target):
            identity = (
                self.target / METADATA_DIR,
                self.target / "x86qw.sh",
                self.target / "x86qw.cmd",
            )
            if not any(lexists(path) for path in identity):
                raise InstallerError(
                    f"A remoção total recusou um diretório sem identidade x86QW: {self.target}"
                )
            current = Path.cwd().resolve()
            if current == self.target or self.target in current.parents:
                os.chdir(self.target.parent)
            if preserve_operation_lock:
                metadata = self.target / METADATA_DIR
                sessions = metadata / "sessions"
                for child in tuple(self.target.iterdir()):
                    if child != metadata:
                        remove_path(child)
                if metadata.is_dir() and not metadata.is_symlink():
                    for child in tuple(metadata.iterdir()):
                        if child != sessions:
                            remove_path(child)
                if sessions.is_dir() and not sessions.is_symlink():
                    for child in tuple(sessions.iterdir()):
                        if child.name != "active.lock":
                            remove_path(child)
                console.info(
                    "Conteúdo da instalação removido; o diretório do lock será "
                    "finalizado ao encerrar a operação."
                )
            else:
                remove_path(self.target)
                console.success(f"Diretório da instalação removido: {self.target}")
        else:
            console.info(f"Nenhum diretório de instalação foi encontrado em {self.target}.")
        for root in caches:
            remove_path(root)
            console.success(f"Cache removido: {root}")
        if not caches:
            console.info(f"Nenhum cache do instalador foi encontrado em {self.cache_root}.")
        console.success("Remoção total concluída; nenhum dado gerenciado pelo x86QW foi preservado.")

    def install(
        self,
        *,
        platform: str | None = None,
        before_mutation: Callable[[], None] | None = None,
    ) -> None:
        console.section("Fase 1/2 · ezQuake")
        self.select_platform(platform)
        self.choose_channel()
        self.choose_release()
        if before_mutation is not None:
            before_mutation()
        reset_macos_game_directory = self.macos_game_directory_reset_required()
        self.ensure_macos_ezquake_closed()
        self.check_runtime_destination_ownership()
        self.prepare_install_target()
        self.reject_target_symlinks()
        self._create_stage(".quake-install.")
        installation_results: list[MutationResult] = []
        try:
            pak_result = self.provision_install_target()
            if pak_result is not None:
                installation_results.append(pak_result)
            self.check_paks()
            pak0_before = file_hash(self.target / "id1/pak0.pak")
            pak1_before = file_hash(self.target / "id1/pak1.pak")
            self.prepare_cache()
            archive = self.ensure_archive()
            assert self.spec is not None and self.stage is not None
            console.info(
                f"Preparando ezQuake {self.spec.label} {self.channel} "
                f"{self.selected_version}..."
            )
            prepared = self.prepare_runtime(archive)
            staged_receipt = self.stage / "ezquake-receipt"
            self.write_ezquake_receipt(staged_receipt)
            self.ensure_metadata_directory()
            installation_results.append(
                self.commit_runtime(prepared, staged_receipt)
            )
            if reset_macos_game_directory:
                self.reset_macos_game_directory()
            console.success("ezQuake instalado e recibo registrado.")
            if reset_macos_game_directory:
                console.info(f"Na primeira abertura, selecione este diretório quando o macOS solicitar: {self.target}")
            if self.confirm_components():
                installation_results.extend(self.install_component_phase())
            else:
                console.info("Dados nQuake não solicitados; esta etapa foi ignorada.")
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
            console.section("Verificação final")
            self.verify_installation()
        except BaseException as error:
            if not isinstance(error, PersistenceError) or not error.committed:
                self.rollback_component_transactions(installation_results, error)
            raise
        console.section("Resumo")
        print(f"  Sistema: {self.spec.label}")
        print(f"  Canal:   {self.channel}")
        print(f"  Versão:  {self.selected_version}")
        print(f"  Destino: {self.target}")
        if self.installed_components():
            console.success("Instalação completa e pronta para uso.")
        else:
            console.success(f"ezQuake pronto em {self.target / self.spec.runtime(self.channel)}")

    @staticmethod
    def release_is_newer(candidate: str, installed: str, channel: str) -> bool:
        if channel == "stable":
            return tuple(map(int, candidate.split("."))) > tuple(map(int, installed.split(".")))
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
            self.repair_installed_macos_runtime(spec, channel, receipt_path, receipt)
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
            archive = self.ensure_archive()
            if restore_stable_bundle:
                console.info(
                    "Restaurando o ezQuake stable a partir do bundle upstream integral..."
                )
            else:
                console.info(f"Atualizando ezQuake {spec.label} {channel}: {installed} → {available}...")
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
            console.success(f"ezQuake {spec.label} {channel} atualizado para {available}.")
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
            self.load_install_state(persist_migration=False)
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
        with self.component_state_transaction() as mutation_results:
            if not dry_run and layout_change:
                self.migrate_metadata_layout(mutation_results)
            if not dry_run:
                console.section("Clientes ezQuake instalados")
            for spec, channel, receipt in runtimes:
                changed = self.update_runtime(
                    spec, channel, receipt,
                    dry_run=dry_run,
                    preview=preview,
                    plan_rows=plan_rows,
                    mutation_results=mutation_results,
                ) or changed

            if not dry_run:
                console.section("Componentes instalados")
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
                console.section("Verificação final")
                self.verify_installation()
        if not dry_run and changed and not profile_upgrade:
            console.success("Conteúdo instalado atualizado e validado.")
        return changed

    def upgrade(
        self, *, dry_run: bool = False, preview: bool = False,
        plan_rows: list[UpdatePlanRow] | None = None,
    ) -> bool:
        changed = self.update(
            dry_run=dry_run, profile_upgrade=True, preview=preview, plan_rows=plan_rows,
        )
        state = self.current_install_state(
            self.load_install_state(persist_migration=False)
        )
        desired = self.desired_components(state)
        installed = self.installed_components()
        legacy_replacements = self.installed_legacy_component_replacements()
        installed_or_planned = {*installed, *legacy_replacements.values()}
        missing = [identifier for identifier in desired if identifier not in installed_or_planned]
        extras = [identifier for identifier in installed if identifier not in desired]

        if not dry_run:
            console.section(f"Convergência do perfil {state['profile']}")
        if extras and not dry_run:
            console.warning(
                "Componentes fora do perfil foram preservados: " + ", ".join(extras) + "."
            )
        with self.component_state_transaction() as component_results:
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
                console.section("Verificação final do perfil")
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

    def install_online_cli(self) -> None:
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

        parent_managed = self.stage is not None
        with self.runtime_mutation_stage(
            ".x86qw-cli.", parent_managed=parent_managed,
        ):
            assert self.stage is not None
            prepared_cli = self.stage / "cli"
            prepared_cli.mkdir()
            prepared_application = prepared_cli / CLI_ARCHIVE_NAME
            shutil.copyfile(application, prepared_application)
            if os.name != "nt":
                prepared_application.chmod(0o644)
            if file_hash(prepared_application) != application_digest:
                raise InstallerError("A cópia preparada da CLI diverge do bundle validado.")
            self.write_cli_receipt_record(prepared_cli / "receipt", identity)

            prepared_launchers: dict[str, Path] = {}
            launcher_stage = self.stage / "launchers"
            launcher_stage.mkdir()
            for name, (rendered, mode) in rendered_launchers.items():
                prepared = launcher_stage / name
                prepared.write_text(rendered, encoding="utf-8", newline="\n")
                if os.name != "nt":
                    prepared.chmod(mode)
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
                if self.validate_cli_receipt(cli_root / "receipt") != identity:
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

        shell_launcher = self.target / "x86qw.sh"
        console.success(f"CLI permanente instalada: {shell_launcher} (versão {cli_version})")


class FriendlyArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: erro: {message}\n")


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
        help="simula update, upgrade ou repair sem alterar arquivos",
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
        "action", nargs="?", default="install",
        help=(
            "install, menu, play, host, proxy, qtv, status, version, update, upgrade, repair, components, presets, hub, "
            "verify, uninstall ou cleanup"
        ),
    )
    parser.add_argument(
        "target", nargs="?", type=Path,
        help="diretório de instalação (o instalador público pergunta antes de iniciar)",
    )
    namespace = parser.parse_args(arguments)
    valid_actions = (
        "install", "menu", "play", "host", "proxy", "qtv", "status", "version", "update", "upgrade", "repair", "components",
        "presets", "hub", "verify", "uninstall", "cleanup",
    )
    if namespace.action not in valid_actions:
        parser.error(f"ação desconhecida: {namespace.action}. Use {', '.join(valid_actions)}")
    if namespace.action != "cleanup" and (namespace.downloads or namespace.personal_data):
        parser.error("--downloads e --personal-data só podem ser usados com cleanup")
    if namespace.purge and namespace.action != "uninstall":
        parser.error("--purge só pode ser usado com uninstall")
    if namespace.installed_cli and namespace.action in {"install", "components", "presets"}:
        parser.error(
            f"{namespace.action} não está disponível na CLI instalada; use install.sh para instalar ou adicionar conteúdo"
        )
    if namespace.skip_cli_update and not (namespace.installed_cli and namespace.action in {"update", "upgrade"}):
        parser.error("--skip-cli-update é reservado ao processo interno de atualização da CLI")
    if namespace.dry_run and namespace.action not in {"update", "upgrade", "repair"}:
        parser.error("--dry-run só pode ser usado com update, upgrade ou repair")
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
                "QuakeWorld moderno",
                (
                    navigation.MenuOption("play", "Jogar", "mods locais e modos KTX", "Escolha jogo, modo, mapa e regras."),
                    navigation.MenuOption("hub", "Encontrar servidor", "jogar, observar ou assistir QTV", "Servidores públicos com busca."),
                    navigation.MenuOption("host", "Hospedar partida", "MVDSV com QTV e QWFWD opcionais", "Servidor dedicado em primeiro plano."),
                    navigation.MenuOption("services", "Serviços", "visualizar, transmitir ou usar proxy", "Estado da stack, QTV e QWFWD isolados."),
                    navigation.MenuOption("manage", "Gerenciar instalação", "atualizar, reparar ou limpar", "Operações seguras sobre conteúdo instalado."),
                    navigation.MenuOption("info", "Ajuda e informações", "versão, caminhos e comandos", "A CLI por argumentos continua disponível."),
                    navigation.MenuOption("exit", "Sair", "encerrar o menu"),
                ),
                breadcrumb=breadcrumb,
                searchable=True,
                allow_back=True,
            )
        except navigation.MenuCancelled:
            if sys.stdin.isatty():
                raise
            launcher = "x86qw.cmd" if os.name == "nt" else "./x86qw.sh"
            print(f"\nUso: {launcher} <comando> [opções]")
            print(f"Exemplo: {launcher} play")
            print("Comandos: play, host, proxy, qtv, status, hub, update, upgrade, verify, repair, cleanup, uninstall e version.")
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
                if os.name == "nt":
                    print(f"\n  {PUBLIC_POWERSHELL_BOOTSTRAP_COMMAND}")
                else:
                    print(f"\n  {PUBLIC_UNIX_BOOTSTRAP_COMMAND}")
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
            print("Comandos: play, host, hub, qtv, proxy, status, update, upgrade, verify, repair, cleanup, uninstall e version.")
            launcher = "x86qw.cmd" if os.name == "nt" else "./x86qw.sh"
            print(f"Use {launcher} <comando> --help para ver todas as opções avançadas.")
            print("No menu: ↑↓ navega, →/Enter seleciona, ← volta e Esc sai; / busca quando aparecer na legenda.")
            try:
                input("\nPressione Enter para voltar ao menu...")
            except EOFError:
                return 0


def execute_manager_action(options: argparse.Namespace, project_root: Path) -> int:
    """Execute a parsed manager action under the installation operation contract."""
    action_labels = {
        "install": "instalar ezQuake + componentes x86QW", "components": "gerenciar componentes x86QW",
        "presets": "gerenciar presets",
        "hub": "navegar servidores", "verify": "verificar", "uninstall": "desinstalar",
        "cleanup": "limpar caches e dados locais", "update": "atualizar o conteúdo instalado",
        "upgrade": "incorporar novidades da distribuição", "repair": "reparar conteúdo gerenciado",
    }
    action_label = "desinstalar e remover todos os dados" if options.purge else action_labels[options.action]
    console.banner(action_label, options.target)
    installer = Installer(project_root, options.target, online_only=options.online_only)
    operation_lock: session_control.InstallationLock | None = None
    recovery_confirmed = False

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
            acquire_operation_lock()
            console.section("Limpeza segura")
            installer.cleanup_cache()
            cache_count, personal_count = installer.cleanup_runtime_data(
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
        if options.action == "verify":
            console.section("Verificação da instalação")
            installer.verify_installation()
            console.success("Verificação concluída sem problemas.")
        elif options.action == "repair":
            acquire_operation_lock()
            plan_rows: list[UpdatePlanRow] = []
            needs_repair = installer.repair(dry_run=True, plan_rows=plan_rows)
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
            acquire_operation_lock()
            if options.purge:
                console.section("Desinstalação completa")
                installer.purge(preserve_operation_lock=True)
            else:
                console.section("Desinstalação")
                installer.uninstall()
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
            if not plan_rows:
                message = (
                    "Nenhuma novidade disponível; a instalação já corresponde ao perfil atual."
                    if options.action == "upgrade"
                    else "Nenhuma atualização disponível; o conteúdo instalado já está atualizado."
                )
                console.heading("Já está atualizado")
                console.success(message)
                return 0
            console.update_plan(plan_rows, options.action)
            if options.dry_run:
                console.heading("Simulação concluída; nenhum arquivo foi alterado")
                return 0
            if not installer.confirm_update_plan(options.action, assume_yes=options.yes):
                return 0
            console.heading(
                "Atualizando pacotes" if options.action == "update" else "Incorporando novidades"
            )
            if content_changed:
                operation(dry_run=False)
            if options.skip_cli_update:
                installer.install_online_cli()
        else:
            if options.action in {"components", "presets"}:
                acquire_operation_lock()
            if options.action == "components":
                installer.manage_components()
            elif options.action == "presets":
                installer.manage_presets()
            else:
                installer.install(
                    platform=options.platform, before_mutation=acquire_operation_lock,
                )
            installer.install_online_cli()
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
        if options.purge and lock_released and lexists(installer.target):
            try:
                remove_empty_directories(installer.target / METADATA_DIR)
                installer.target.rmdir()
                console.success(f"Diretório da instalação removido: {installer.target}")
            except OSError as error:
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
        if raw_arguments[:1] and raw_arguments[0] in {"host", "proxy", "qtv", "status"}:
            services = load_services_module()
            return services.main(raw_arguments)
        options = parse_arguments(raw_arguments, project_root)
        console.configure(verbose=options.verbose, no_color=options.no_color)
        navigation.configure(no_color=options.no_color)
        if options.action == "version":
            print(f"x86QW {application_version()}")
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
