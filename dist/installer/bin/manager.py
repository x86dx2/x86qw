#!/usr/bin/env python3
"""Cross-platform ezQuake + x86QW component installer."""

from __future__ import annotations

import argparse
import errno
import hashlib
import http.client
import importlib
import json
import os
import platform as host_platform
import plistlib
import re
import selectors
import shutil
import socket
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath

sys.dont_write_bytecode = True
sys.modules.setdefault("manager", sys.modules[__name__])

_argv0 = Path(sys.argv[0]).expanduser().resolve()
ZIPAPP_PATH = _argv0 if _argv0.suffix.casefold() == ".pyz" and _argv0.is_file() else None
PROJECT_ROOT = ZIPAPP_PATH.parent if ZIPAPP_PATH is not None else Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
INSTALLER_BIN = Path(__file__).resolve().parent
if str(INSTALLER_BIN) not in sys.path:
    sys.path.insert(0, str(INSTALLER_BIN))

session_control = importlib.import_module("session_control")

from maintenance.tools.components import (
    components_by_id,
    load_catalog as load_component_catalog,
    load_runtime_catalog,
    profile_fingerprint,
    resolve_dependencies,
    validate_runtime_catalog,
)
from maintenance.tools.runtime_catalog import (
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
METADATA_DIR = ".install"
COMPONENT_METADATA_DIR = ".install/components"
EZQUAKE_METADATA_DIR = ".install/clients/ezquake"
# Legacy aggregate receipt names kept only for one-way migration and uninstall.
NQUAKE_RECEIPT = ".install/nquake.receipt"
NQUAKE_INVENTORY = ".install/nquake.inventory"
DEVELOPMENT_COMPONENT_CATALOG = "maintenance/inventory/components.json"
COMPONENT_RELEASES = "maintenance/inventory/component-releases.json"
RUNTIME_COMPONENT_CATALOG = "_x86qw/components.json"
DEVELOPMENT_CAPABILITY_CATALOG = Path("maintenance/inventory/capabilities.json")
DEVELOPMENT_RUNTIME_CATALOG = Path("maintenance/inventory/runtimes.json")
RUNTIME_CAPABILITY_CATALOG = "_x86qw/capabilities.json"
RUNTIME_RUNTIME_CATALOG = "_x86qw/runtimes.json"
CLI_ARCHIVE_NAME = "x86qw.pyz"
OUTER_INSTALLER_METADATA = "installer.json"
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
STABLE_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
NIGHTLY_VERSION = re.compile(r"^[0-9]{8}-[0-9]{6}_[0-9a-f]{7}$")
COMPONENT_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HUB_SERVERS_API = "https://hubapi.quakeworld.nu/v2/servers/mvdsv?empty=exclude&limit=20"
MAPS_RECEIPT = ".install/components/maps/receipt"
MAPS_INVENTORY = ".install/components/maps/inventory"
PRESETS_RECEIPT = ".install/components/presets/receipt"
PRESETS_INVENTORY = ".install/components/presets/inventory"
PLAY_SUPPORT_RECEIPT = ".install/components/play-support/receipt"
PLAY_SUPPORT_INVENTORY = ".install/components/play-support/inventory"
PACKAGE_ORDER_RECEIPT = ".install/components/package-order/receipt"
PACKAGE_ORDER_INVENTORY = ".install/components/package-order/inventory"
CLI_RECEIPT = ".install/cli/receipt"
LEGACY_CLI_RECEIPT = ".install/cli.receipt"
INSTALL_STATE = ".install/state.json"
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
        with zipfile.ZipFile(archive) as package:
            value = json.loads(package.read(member))
    except (OSError, KeyError, UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile) as error:
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


def create_resilient_connection(
    address: tuple[str, int],
    timeout: float | object = socket._GLOBAL_DEFAULT_TIMEOUT,
    source_address: tuple[str, int] | None = None,
) -> socket.socket:
    """Connect to the first reachable DNS address without waiting on a dead first result."""
    host, port = address
    effective_timeout = socket.getdefaulttimeout() if timeout is socket._GLOBAL_DEFAULT_TIMEOUT else timeout
    started = time.monotonic()
    if effective_timeout is None:
        candidates = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    else:
        resolved: list[list[tuple[object, ...]]] = []
        resolution_errors: list[Exception] = []
        finished = threading.Event()

        def resolve() -> None:
            try:
                resolved.append(socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM))
            except Exception as error:  # propagated in the caller thread below
                resolution_errors.append(error)
            finally:
                finished.set()

        threading.Thread(target=resolve, name="x86qw-dns", daemon=True).start()
        if not finished.wait(float(effective_timeout)):
            raise TimeoutError(f"Tempo esgotado ao resolver {host}.")
        if resolution_errors:
            raise resolution_errors[0]
        candidates = resolved[0]
    if not candidates:
        raise OSError(f"Nenhum endereço foi encontrado para {host}.")
    deadline = None if effective_timeout is None else started + float(effective_timeout)
    pending: list[socket.socket] = []
    errors: list[OSError] = []
    selector = selectors.DefaultSelector()
    connected: socket.socket | None = None
    in_progress = {
        errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY, errno.EINTR,
        *(value for name in ("WSAEINPROGRESS", "WSAEWOULDBLOCK", "WSAEALREADY")
          if (value := getattr(errno, name, None)) is not None),
    }
    try:
        for family, socktype, proto, _, sockaddr in candidates:
            connection = socket.socket(family, socktype, proto)
            pending.append(connection)
            try:
                connection.setblocking(False)
                if source_address:
                    connection.bind(source_address)
                result = connection.connect_ex(sockaddr)
                if result in (0, errno.EISCONN):
                    connected = connection
                    break
                if result not in in_progress:
                    raise OSError(result, os.strerror(result))
                selector.register(connection, selectors.EVENT_WRITE)
            except OSError as error:
                errors.append(error)
                connection.close()
                pending.remove(connection)

        while connected is None and selector.get_map():
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            if remaining == 0 or not (events := selector.select(remaining)):
                raise TimeoutError(f"Tempo esgotado ao conectar a {host}:{port}.")
            for key, _ in events:
                connection = key.fileobj
                result = connection.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if result == 0:
                    connected = connection
                    break
                errors.append(OSError(result, os.strerror(result)))
                selector.unregister(connection)
                connection.close()
                pending.remove(connection)
        if connected is None:
            if errors:
                raise errors[-1]
            raise OSError(f"Não foi possível conectar a {host}:{port}.")
        connected.settimeout(effective_timeout)
        return connected
    finally:
        selector.close()
        for connection in pending:
            if connection is not connected:
                connection.close()


class ResilientHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args: object, **kwargs: object):
        super().__init__(*args, **kwargs)
        self._create_connection = create_resilient_connection


class ResilientHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, request: urllib.request.Request):  # type: ignore[no-untyped-def]
        return self.do_open(ResilientHTTPSConnection, request, context=self._context)


HTTPS_OPENER = urllib.request.build_opener(ResilientHTTPSHandler())


@dataclass(frozen=True)
class PlatformSpec:
    key: str
    label: str
    architecture: str
    stable_archive: str
    nightly_suffix: str
    archive_binary: str
    stable_runtime: str
    nightly_runtime: str
    stable_receipt: str
    nightly_receipt: str

    def runtime(self, channel: str) -> str:
        return self.stable_runtime if channel == "stable" else self.nightly_runtime

    def receipt(self, channel: str) -> str:
        return self.stable_receipt if channel == "stable" else self.nightly_receipt

    def legacy_receipt(self, channel: str) -> str:
        return f".install/ezquake-{self.key}-{channel}.receipt"


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


CAPABILITY_CATALOG, RUNTIME_CATALOG = load_launcher_contracts()
RUNTIMES = runtimes_by_id(RUNTIME_CATALOG)


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
            str(stable_platform["runtime_path"]),
            str(nightly_platform["runtime_path"]),
            str(stable_platform["receipt"]),
            str(nightly_platform["receipt"]),
        )
    return result


PLATFORMS = client_platform_specs()
raw_host_platforms = CAPABILITY_CATALOG.get("host_systems")
if not isinstance(raw_host_platforms, dict):
    raise ValueError("catálogo declarativo não informa sistemas hospedeiros")
HOST_PLATFORMS = {str(host): str(system) for host, system in raw_host_platforms.items()}


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

class InstallerError(RuntimeError):
    pass


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
        verb = {"update": "update", "upgrade": "upgrade", "repair": "repair"}[action]
        noun = "package" if len(rows) == 1 else "packages"
        self.heading(f"Would {verb} {len(rows)} outdated {noun}")
        names = [row.item for row in rows]
        installed = [row.installed for row in rows]
        available = [row.available for row in rows]
        name_width = max(map(len, names))
        installed_width = max(map(len, installed))
        available_width = max(map(len, available))
        for row in rows:
            size = f" ({format_bytes_compact(row.size)})" if row.size is not None else ""
            print(
                f"{row.item.ljust(name_width)}  "
                f"{row.installed.ljust(installed_width)} -> "
                f"{row.available.ljust(available_width)}{size}",
                flush=True,
            )

    def download_result(
        self, label: str, *, size: int, status: str = "Downloaded",
    ) -> None:
        amount = format_bytes_compact(size)
        check = self.paint("✔︎", "32")
        print(f"{check} {label:<48} {status:>10}  {amount:>9}/{amount}", flush=True)

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
    if not isinstance(url, str):
        raise InstallerError(f"{label} não é uma URL válida")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise InstallerError(f"{label} deve ser uma URL HTTPS absoluta")
    return parsed


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


def archive_relative_path(name: str) -> PurePosixPath:
    if not name or "\\" in name or ":" in name or name.startswith("/"):
        raise InstallerError(f"unsafe archive path: {name}")
    path = PurePosixPath(name)
    if any(part in ("", ".", "..") for part in path.parts):
        raise InstallerError(f"unsafe archive path: {name}")
    return path


def safe_extract_zip(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as package:
        for member in package.infolist():
            relative = archive_relative_path(member.filename.rstrip("/"))
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise InstallerError(f"archive contains an unsupported symlink: {member.filename}")
            output = destination.joinpath(*relative.parts)
            if member.is_dir():
                output.mkdir(parents=True, exist_ok=True)
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            with package.open(member) as source, output.open("wb") as target:
                shutil.copyfileobj(source, target)
            permissions = stat.S_IMODE(mode)
            if permissions and os.name != "nt":
                output.chmod(permissions)


def copy_overlay(source: Path, destination: Path) -> None:
    if not lexists(source):
        raise InstallerError(f"missing distribution component: {source}")
    if source.is_symlink():
        raise InstallerError(f"distribution component must not be a symlink: {source}")
    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True, copy_function=shutil.copy2)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def read_table(path: Path, keys: set[str], label: str) -> dict[str, str]:
    if not path.is_file() or path.is_symlink():
        raise InstallerError(f"invalid {label}: {path}")
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        fields = raw_line.split("\t")
        if len(fields) != 2 or fields[0] not in keys or fields[0] in result:
            raise InstallerError(f"invalid {label}: {path}")
        result[fields[0]] = fields[1]
    if set(result) != keys:
        raise InstallerError(f"invalid {label}: {path}")
    return result


def write_table(path: Path, rows: list[tuple[str, str]]) -> None:
    path.write_text("".join(f"{key}\t{value}\n" for key, value in rows), encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o644)


class Installer:
    def __init__(
        self,
        project_root: Path,
        target: Path,
        cache_root: Path | None = None,
        *,
        online_only: bool = False,
    ):
        self.project_root = project_root.resolve()
        self.target = target
        self.online_only = online_only
        self._cache_root = cache_root
        self.cache_root: Path | None = None
        self.cache_bin: Path | None = None
        self.stage: Path | None = None
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

    def prepare_macos_app(self, app: Path) -> bool:
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
        managed_roots = ("id1", "ezquake", "qw", "arena", "prox", "fortress", "td2", METADATA_DIR)
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

    def provision_install_target(self) -> None:
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
            return

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

        copied = 0
        for name, expected in requirements:
            destination = self.target / "id1" / name
            if name not in missing:
                continue
            temporary = self.target / "id1" / f".{name}.x86qw-part"
            ensure_no_symlink(temporary, "temporary PAK")
            try:
                shutil.copyfile(sources[name], temporary)
                if os.name != "nt":
                    temporary.chmod(0o644)
                self.validate_pak_file(temporary, expected, "Cópia temporária do PAK")
                os.replace(temporary, destination)
            finally:
                if lexists(temporary):
                    remove_path(temporary)
            copied += 1
        if copied:
            console.success(
                f"Dados base preparados em {self.target / 'id1'} ({file_count(copied)} copiados)."
            )
        else:
            console.detail("PAKs registrados já estavam presentes e foram preservados.")

    def ensure_metadata_directory(self) -> None:
        metadata = self.target / METADATA_DIR
        ensure_no_symlink(metadata, "metadata directory")
        if lexists(metadata) and not metadata.is_dir():
            raise InstallerError(f"metadata path is not a directory: {metadata}")
        metadata.mkdir(exist_ok=True)
        if os.name != "nt":
            metadata.chmod(0o755)

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

    def prompt_catalog(self, label: str, catalog: list[ReleaseRecord]) -> ReleaseRecord:
        preview_size = 12

        def show_catalog(show_all: bool = False) -> None:
            visible = catalog if show_all else catalog[:preview_size]
            print(f"\nVersões {label} disponíveis (mais recente primeiro):")
            for index, record in enumerate(visible, 1):
                print(f"  {index:3d}) {record[0]}")
            hidden = len(catalog) - len(visible)
            if hidden:
                print(f"       ... mais {hidden} versões. Digite t para mostrar todas.")

        show_catalog()
        expanded = len(catalog) <= preview_size
        while True:
            try:
                prompt = "Escolha o número ou a versão exata"
                if not expanded:
                    prompt += ", ou t para listar todas"
                answer = input(prompt + ": ").strip()
            except EOFError as error:
                raise InstallerError("Nenhuma versão foi selecionada.") from error
            if not expanded and answer.lower() in ("t", "todas", "all"):
                show_catalog(show_all=True)
                expanded = True
                continue
            if answer.isdigit():
                index = int(answer)
                if 1 <= index <= len(catalog):
                    return catalog[index - 1]
                console.warning(f"Número inválido. Escolha um valor entre 1 e {len(catalog)}.")
                continue
            matches = [record for record in catalog if record[0] == answer]
            if len(matches) == 1:
                return matches[0]
            console.warning("Versão não encontrada. Use um número da lista ou informe o identificador completo.")

    def choose_channel(self) -> str:
        print("\nQual canal deseja instalar?")
        print("  1) stable  - releases oficiais")
        print("  2) nightly - snapshots de desenvolvimento")
        aliases = {"1": "stable", "stable": "stable", "s": "stable", "2": "nightly", "nightly": "nightly", "n": "nightly"}
        while True:
            try:
                answer = input("Escolha [1/2]: ").strip().lower()
            except EOFError as error:
                raise InstallerError("Nenhum canal foi selecionado.") from error
            channel = aliases.get(answer)
            if channel is not None:
                self.channel = channel
                console.success(f"Canal selecionado: {channel}")
                return channel
            console.warning("Opção inválida. Digite 1 para stable ou 2 para nightly.")

    def confirm_components(self) -> bool:
        while True:
            try:
                answer = input("\nDeseja instalar/atualizar também os componentes x86QW? [s/N]: ").strip().lower()
            except EOFError:
                answer = ""
            if answer in ("s", "sim", "y", "yes"):
                return True
            if answer in ("", "n", "nao", "não", "no"):
                return False
            console.warning("Resposta inválida. Digite s para sim ou n para não.")

    def http_get(
        self,
        url: str,
        destination: Path | None = None,
        headers: dict[str, str] | None = None,
        *,
        timeout: float = 60.0,
        attempts: int = 3,
    ) -> bytes:
        validate_https_url(url, "URL de download")
        request_headers = {"User-Agent": "x86-qw-installer/1", **(headers or {})}
        request = urllib.request.Request(url, headers=request_headers)
        console.detail(f"GET {url}")
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                with HTTPS_OPENER.open(request, timeout=timeout) as response:
                    validate_https_url(response.geturl(), "redirecionamento de download")
                    if destination is None:
                        return response.read()
                    total_header = response.headers.get("Content-Length")
                    total = int(total_header) if total_header and total_header.isdigit() else None
                    received = 0
                    last_update = 0.0
                    with destination.open("wb") as target:
                        while block := response.read(1024 * 1024):
                            target.write(block)
                            received += len(block)
                            now = time.monotonic()
                            if now - last_update >= 0.1:
                                console.download_progress(received, total)
                                last_update = now
                    console.download_progress(received, total, done=True)
                    return b""
            except urllib.error.HTTPError as error:
                if error.code == 403 and error.headers.get("X-RateLimit-Remaining") == "0":
                    raise InstallerError(
                        "O limite temporário de consultas do GitHub foi atingido. Aguarde a renovação "
                        "ou defina GITHUB_TOKEN para ampliar o limite."
                    ) from error
                last_error = error
                console.detail(f"Tentativa de download falhou: {last_error}")
                if attempt < attempts:
                    console.warning(
                        f"Falha temporária no download. Tentando novamente ({attempt + 1}/{attempts})..."
                    )
            except (OSError, urllib.error.URLError) as error:
                last_error = error
                console.detail(f"Tentativa de download falhou: {last_error}")
                if attempt < attempts:
                    console.warning(
                        f"Falha temporária no download. Tentando novamente ({attempt + 1}/{attempts})..."
                    )
        raise InstallerError(f"Não foi possível baixar {url}: {last_error}")

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
        console.detail(f"Artefato: {self.app_url}")
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
            last_error: InstallerError | None = None
            for index, mirror_url in enumerate(self.app_urls or (self.app_url,)):
                if lexists(download):
                    remove_path(download)
                try:
                    self.http_get(mirror_url, download)
                    if file_hash(download, self.app_checksum_kind) != self.app_expected_checksum:
                        raise InstallerError(f"O arquivo baixado falhou na verificação: {mirror_url}")
                except InstallerError as error:
                    last_error = error
                    console.detail(str(error))
                    if index + 1 < len(self.app_urls):
                        console.warning("Mirror indisponível ou inválido; tentando a próxima cópia...")
                    continue
                self.app_url = mirror_url
                break
            else:
                if lexists(download):
                    remove_path(download)
                raise InstallerError(f"Nenhum mirror entregou um pacote válido: {last_error}")
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
            extract.mkdir()
            safe_extract_zip(archive, extract)
            source = extract / self.spec.archive_binary
            version, binary_hash = self.inspect_macos_app(source)
            if self.channel == "stable" and version != self.selected_version:
                raise InstallerError(f"stable bundle version is {version}, expected {self.selected_version}")
            if self.channel == "nightly" and f"-g{self.selected_version.rsplit('_', 1)[-1]}" not in version:
                raise InstallerError(f"nightly bundle {version} does not match {self.selected_version}")
            if self.prepare_macos_app(source):
                version, binary_hash = self.inspect_macos_app(source)
            source.replace(prepared)
            self.app_bundle_version = version
            self.app_binary_sha256 = binary_hash
        else:
            if self.channel == "stable":
                extract = self.stage / "runtime"
                extract.mkdir()
                safe_extract_zip(archive, extract)
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
        keys = {"format", "platform", "architecture", "channel", "selection", "install_name", "bundle_version", "artifact_name", "artifact_url", "artifact_sha256", "binary_sha256"}
        receipt = read_table(path, keys, "ezQuake receipt")
        if receipt["format"] != "1" or receipt["platform"] != spec.key or receipt["architecture"] != spec.architecture:
            raise InstallerError(f"invalid platform metadata in ezQuake receipt: {path}")
        if receipt["channel"] != channel or receipt["install_name"] != spec.runtime(channel):
            raise InstallerError(f"invalid target metadata in ezQuake receipt: {path}")
        validate_hex(receipt["artifact_sha256"], HEX64, "artifact SHA-256 in ezQuake receipt")
        validate_hex(receipt["binary_sha256"], HEX64, "binary SHA-256 in ezQuake receipt")
        selection = receipt["selection"]
        if channel == "stable":
            if not STABLE_VERSION.fullmatch(selection) or receipt["bundle_version"] != selection:
                raise InstallerError(f"invalid stable selection in ezQuake receipt: {selection}")
            expected_name = spec.stable_archive
        else:
            if not NIGHTLY_VERSION.fullmatch(selection):
                raise InstallerError(f"invalid nightly selection in ezQuake receipt: {selection}")
            if spec.key == "macos":
                if f"-g{selection.rsplit('_', 1)[-1]}" not in receipt["bundle_version"]:
                    raise InstallerError("nightly bundle version differs from ezQuake selection")
            elif receipt["bundle_version"] != selection:
                raise InstallerError("nightly version differs from ezQuake selection")
            expected_name = selection + spec.nightly_suffix
        if (
            receipt["artifact_name"] != expected_name
            or https_url_filename(receipt["artifact_url"], "URL do artefato no recibo") != expected_name
        ):
            raise InstallerError(f"unexpected artifact in ezQuake receipt: {path}")
        return receipt

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
        write_table(path, [
            ("format", receipt["format"]), ("platform", receipt["platform"]),
            ("architecture", receipt["architecture"]), ("channel", receipt["channel"]),
            ("selection", receipt["selection"]), ("install_name", receipt["install_name"]),
            ("bundle_version", receipt["bundle_version"]), ("artifact_name", receipt["artifact_name"]),
            ("artifact_url", receipt["artifact_url"]), ("artifact_sha256", receipt["artifact_sha256"]),
            ("binary_sha256", receipt["binary_sha256"]),
        ])

    def repair_installed_macos_runtime(
        self,
        spec: PlatformSpec,
        channel: str,
        receipt_path: Path,
        receipt: dict[str, str],
    ) -> dict[str, str]:
        runtime = self.target / spec.runtime(channel)
        if spec.key != "macos" or not self.macos_app_needs_preparation(runtime):
            return receipt
        self.ensure_macos_ezquake_closed()
        self.prepare_macos_app(runtime)
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
        self.clear_macos_game_directory()
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

    def commit_runtime(self, prepared: Path, staged_receipt: Path) -> None:
        assert self.spec is not None and self.stage is not None
        runtime = self.target / self.spec.runtime(self.channel)
        receipt = self.target / self.spec.receipt(self.channel)
        receipt.parent.mkdir(parents=True, exist_ok=True)
        existing_receipt = self.ezquake_receipt_path(self.spec, self.channel)
        previous_runtime = self.stage / "previous-runtime"
        previous_receipt = self.stage / "previous-receipt"
        moved_runtime = moved_receipt = installed_runtime = installed_receipt = False
        try:
            if lexists(runtime):
                runtime.replace(previous_runtime)
                moved_runtime = True
            if existing_receipt is not None:
                existing_receipt.replace(previous_receipt)
                moved_receipt = True
            prepared.replace(runtime)
            installed_runtime = True
            shutil.copy2(staged_receipt, receipt)
            installed_receipt = True
            legacy_receipt = self.target / self.spec.legacy_receipt(self.channel)
            if legacy_receipt != receipt and lexists(legacy_receipt):
                remove_path(legacy_receipt)
        except Exception as error:
            try:
                if installed_receipt and lexists(receipt):
                    remove_path(receipt)
                if installed_runtime and lexists(runtime):
                    remove_path(runtime)
                if moved_runtime:
                    previous_runtime.replace(runtime)
                if moved_receipt and existing_receipt is not None:
                    existing_receipt.parent.mkdir(parents=True, exist_ok=True)
                    previous_receipt.replace(existing_receipt)
            except Exception as rollback_error:
                raise InstallerError(f"automatic rollback failed; recovery files kept in {self.stage}: {rollback_error}") from error
            raise InstallerError(f"could not commit {runtime} and its receipt") from error

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
        if path.parts[0] == "_x86qw":
            if len(path.parts) >= 3 and path.parts[1] in {"licenses", "runtimes", "services"}:
                return
            raise InstallerError(f"unexpected path in managed inventory: {value}")
        if value not in ("LICENSE", "readme.txt", "README-X86QW.txt") and path.parts[0] not in (
            "ezquake", "qw", "arena", "prox", "fortress", "td2",
        ):
            raise InstallerError(f"unexpected path in managed inventory: {value}")

    def validate_inventory(self, path: Path) -> list[tuple[str, str]]:
        if not path.is_file() or path.is_symlink():
            raise InstallerError(f"missing managed inventory: {path}")
        entries: list[tuple[str, str]] = []
        seen = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            fields = line.split("\t")
            if len(fields) != 2 or fields[0] in seen:
                raise InstallerError(f"invalid managed inventory entry: {line}")
            self.validate_managed_path(fields[0])
            validate_hex(fields[1], HEX64, f"hash in managed inventory: {fields[0]}")
            entries.append((fields[0], fields[1]))
            seen.add(fields[0])
        return entries

    def create_inventory(self, managed: Path, destination: Path) -> list[tuple[str, str]]:
        entries = []
        for path in sorted((path for path in managed.rglob("*") if path.is_file()), key=lambda item: item.relative_to(managed).as_posix()):
            if path.is_symlink():
                raise InstallerError(f"distribution contains an unsupported symlink: {path}")
            relative = path.relative_to(managed).as_posix()
            self.validate_managed_path(relative)
            entries.append((relative, file_hash(path)))
        destination.write_text("".join(f"{name}\t{digest}\n" for name, digest in entries), encoding="utf-8")
        if os.name != "nt":
            destination.chmod(0o644)
        self.validate_inventory(destination)
        return entries

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
        return f".install/{component}.receipt", f".install/{component}.inventory"

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
        receipt = read_table(
            receipt_path, {"format", "component", "selection", "source", "inventory_sha256"},
            f"recibo do componente {component}",
        )
        if receipt["format"] != "1" or receipt["component"] != component:
            raise InstallerError(f"Recibo inválido do componente {component}.")
        if not receipt["selection"] or "\n" in receipt["selection"] or "\t" in receipt["selection"]:
            raise InstallerError(f"Seleção inválida no recibo do componente {component}.")
        if not receipt["source"] or "\n" in receipt["source"] or "\t" in receipt["source"]:
            raise InstallerError(f"Origem inválida no recibo do componente {component}.")
        validate_hex(receipt["inventory_sha256"], HEX64, f"SHA-256 do inventário {component}")
        entries = self.validate_inventory(inventory_path)
        if file_hash(inventory_path) != receipt["inventory_sha256"]:
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
        write_table(destination, [
            ("format", "1"), ("component", component), ("selection", selection),
            ("source", source), ("inventory_sha256", file_hash(inventory)),
        ])

    def remove_stale_component_files(self, component: str, new_entries: list[tuple[str, str]]) -> None:
        present, old_entries, _ = self.validate_component_pair(component)
        if not present:
            return
        new_names = {name for name, _ in new_entries}
        for name, digest in old_entries:
            if name in new_names:
                continue
            stale = self.target.joinpath(*PurePosixPath(name).parts)
            if not lexists(stale):
                continue
            if not stale.is_file() or stale.is_symlink():
                raise InstallerError(f"Caminho gerenciado inválido: {stale}")
            if file_hash(stale) == digest:
                remove_path(stale)
            else:
                console.warning(f"Arquivo modificado preservado: {stale}")

    def commit_component_metadata(self, component: str, inventory: Path, receipt: Path) -> None:
        assert self.stage is not None
        self.ensure_metadata_directory()
        metadata = self.target / METADATA_DIR
        destination = self.metadata_path(metadata, self.component_metadata(component)[0]).parent
        destination.parent.mkdir(parents=True, exist_ok=True)
        if lexists(destination) and (not destination.is_dir() or destination.is_symlink()):
            raise InstallerError(f"Diretório de metadados inválido para {component}: {destination}")
        prepared = self.stage / f"{component}-metadata.next"
        previous = self.stage / f"{component}-metadata.previous"
        prepared.mkdir()
        shutil.copy2(receipt, prepared / "receipt")
        shutil.copy2(inventory, prepared / "inventory")
        self.validate_component_paths(component, prepared / "receipt", prepared / "inventory")
        moved_previous = installed = False
        try:
            if lexists(destination):
                destination.replace(previous)
                moved_previous = True
            prepared.replace(destination)
            installed = True
            for legacy in self.component_pair_paths(component, metadata, legacy=True):
                if lexists(legacy):
                    remove_path(legacy)
            if lexists(previous):
                remove_path(previous)
        except Exception as error:
            try:
                if installed and lexists(destination):
                    remove_path(destination)
                if moved_previous:
                    previous.replace(destination)
            except Exception as rollback_error:
                raise InstallerError(f"Rollback dos metadados falhou; recuperação mantida em {self.stage}: {rollback_error}") from error
            raise InstallerError(f"Não foi possível registrar o componente {component}.") from error

    def install_component_overlay(self, component: str, managed: Path, selection: str, source: str) -> int:
        assert self.stage is not None
        self.filter_component_conflicts(component, managed)
        inventory = self.stage / f"{component}.inventory"
        entries = self.create_inventory(managed, inventory)
        if not entries:
            raise InstallerError(f"Nenhum arquivo novo do componente {component} pôde ser instalado.")
        receipt = self.stage / f"{component}.receipt"
        self.write_component_receipt(component, selection, source, inventory, receipt)
        copy_overlay(managed, self.target)
        self.remove_stale_component_files(component, entries)
        self.commit_component_metadata(component, inventory, receipt)
        return len(entries)

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

    def refresh_qw_package_order(self) -> None:
        packages = self.expected_qw_package_order()
        if not packages:
            present, _, _ = self.validate_component_pair("package-order")
            if present:
                self.remove_component("package-order")
            return
        previous_stage = self.stage
        owned_stage = previous_stage is None
        if owned_stage:
            self.stage = Path(tempfile.mkdtemp(prefix=".quake-order.", dir=self.target))
        assert self.stage is not None
        try:
            managed = self.stage / "package-order-managed"
            if lexists(managed):
                remove_path(managed, self.target.stat().st_dev)
            target = managed / "qw/pak.lst"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("".join(f"{name}\n" for name in packages), encoding="utf-8")
            count = self.install_component_overlay(
                "package-order", managed, "1", "x86QW deterministic PK3 order",
            )
            console.detail(
                f"Ordem determinística registrada para {len(packages)} PK3 em qw/pak.lst "
                f"({file_count(count)})."
            )
        finally:
            if owned_stage:
                self.cleanup_stage()
                self.stage = previous_stage

    def verify_qw_package_order(self) -> None:
        packages = self.expected_qw_package_order()
        present, _, _ = self.validate_component_pair("package-order")
        if not packages:
            if present:
                raise InstallerError("pak.lst gerenciado existe sem pacotes PK3 em qw.")
            return
        if not present:
            raise InstallerError("Ordem de PK3 não registrada. Execute components para gerar qw/pak.lst.")
        self.verify_component("package-order")
        path = self.target / "qw/pak.lst"
        expected = "".join(f"{name}\n" for name in packages)
        if path.read_text(encoding="utf-8") != expected:
            raise InstallerError("qw/pak.lst não representa os PK3 instalados. Execute components novamente.")

    def remove_component(self, component: str) -> int:
        present, entries, _ = self.validate_component_pair(component)
        if not present:
            return 0
        removed = 0
        for name, digest in entries:
            managed = self.target.joinpath(*PurePosixPath(name).parts)
            if not lexists(managed):
                continue
            if not managed.is_file() or managed.is_symlink():
                raise InstallerError(f"Caminho gerenciado inválido: {managed}")
            if file_hash(managed) == digest:
                remove_path(managed)
                removed += 1
            else:
                console.warning(f"Arquivo modificado preservado: {managed}")
        metadata = self.target / METADATA_DIR
        canonical = self.component_pair_paths(component, metadata)
        remove_path(canonical[0].parent)
        for path in self.component_pair_paths(component, metadata, legacy=True):
            remove_path(path)
        for name in (
            "qw/maps", "ezquake/configs", "arena", "prox", "fortress", "td2",
            "_x86qw/licenses", "_x86qw/runtimes", "_x86qw/services", "_x86qw",
        ):
            remove_empty_directories(self.target / name)
        remove_empty_directories(self.target / COMPONENT_METADATA_DIR)
        remove_empty_directories(self.target / METADATA_DIR)
        return removed

    def verify_component(self, component: str) -> int:
        present, entries, receipt = self.validate_component_pair(component)
        if not present:
            return 0
        for name, expected in entries:
            managed = self.target.joinpath(*PurePosixPath(name).parts)
            if not managed.is_file() or managed.is_symlink():
                raise InstallerError(f"Arquivo gerenciado ausente do componente {component}: {managed}")
            if file_hash(managed) != expected:
                raise InstallerError(f"Arquivo gerenciado foi alterado no componente {component}: {managed}")
            if managed.suffix.casefold() == ".bsp":
                with managed.open("rb") as source:
                    header = source.read(4)
                if len(header) != 4 or struct.unpack("<I", header)[0] != 29:
                    raise InstallerError(f"BSP gerenciado inválido: {managed}")
            if managed.suffix.casefold() == ".pak":
                with managed.open("rb") as source:
                    if source.read(4) != b"PACK":
                        raise InstallerError(f"PAK gerenciado inválido: {managed}")
            if managed.suffix.casefold() == ".pk3":
                try:
                    with zipfile.ZipFile(managed) as package:
                        bad_member = package.testzip()
                except zipfile.BadZipFile as error:
                    raise InstallerError(f"PK3 gerenciado inválido: {managed}") from error
                if bad_member:
                    raise InstallerError(f"Membro inválido {bad_member} no PK3: {managed}")
        assert receipt is not None
        console.success(f"Componente {component} íntegro ({file_count(len(entries))}; seleção {receipt['selection']}).")
        return len(entries)

    def manage_presets(self) -> None:
        print("\nO que deseja fazer com os presets modernos?")
        print("  1) instalar ou atualizar")
        print("  2) remover somente os presets gerenciados")
        while True:
            try:
                answer = input("Escolha [1/2]: ").strip()
            except EOFError as error:
                raise InstallerError("Nenhuma operação de presets foi selecionada.") from error
            if answer in ("1", "2"):
                break
            console.warning("Opção inválida. Digite 1 para instalar/atualizar ou 2 para remover.")
        if answer == "2":
            removed = self.remove_component("presets")
            console.success(f"Presets gerenciados removidos ({file_count(removed)}); configurações pessoais preservadas.")
            return
        self.check_paks()
        self.stage = Path(tempfile.mkdtemp(prefix=".quake-install.", dir=self.target))
        managed = self.stage / "presets-managed"
        configs = managed / "ezquake/configs"
        configs.mkdir(parents=True)
        for name, contents in PRESETS.items():
            (configs / name).write_text(contents, encoding="utf-8")
        count = self.install_component_overlay("presets", managed, "v1", "x86-qw built-in presets")
        console.success(f"Presets instalados ({file_count(count)}). Carregue um deles com cfg_load x86-qw-modern.")

    def validate_nquake_receipt(self, path: Path) -> dict[str, str]:
        receipt = read_table(path, {"format", "distfiles_commit", "inventory_sha256"}, "installation receipt")
        if receipt["format"] != "1":
            raise InstallerError(f"unsupported receipt format: {receipt['format']}")
        validate_hex(receipt["distfiles_commit"], HEX40, "distfiles commit in receipt")
        validate_hex(receipt["inventory_sha256"], HEX64, "inventory SHA-256 in receipt")
        return receipt

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
        migrated = dict(state)
        for field in ("requested_components", "recorded_components", "known_components"):
            migrated[field] = self.replace_legacy_component_ids(list(state[field]))
        migrated["format"] = 2
        migrated.setdefault("capabilities", [])
        migrated["component_fingerprint"] = profile_fingerprint(
            list(migrated["recorded_components"]),
        )
        return self.validate_install_state(migrated)

    def validate_install_state(self, state: object) -> dict[str, object]:
        path = self.target / INSTALL_STATE
        if not isinstance(state, dict):
            raise InstallerError(f"Estado da instalação inválido: {path}")
        profiles = {"none", "custom", *self.component_catalog["profiles"]}
        profile = state.get("profile")
        if state.get("format") not in {1, 2} or state.get("project") != "x86qw" or profile not in profiles:
            raise InstallerError(f"Estado da instalação inválido: {path}")
        for field in ("requested_components", "recorded_components", "known_components"):
            values = state.get(field)
            if (
                not isinstance(values, list)
                or len(values) != len(set(values))
                or not all(isinstance(value, str) and COMPONENT_VERSION.fullmatch(value) for value in values)
            ):
                raise InstallerError(f"Campo {field} inválido no estado da instalação: {path}")
        requested = state["requested_components"]
        if profile != "custom" and requested:
            raise InstallerError(f"Somente o perfil custom pode registrar escolhas explícitas: {path}")
        if state["format"] == 2:
            capabilities = state.get("capabilities")
            fingerprint = state.get("component_fingerprint")
            if (
                not isinstance(capabilities, list)
                or len(capabilities) != len(set(capabilities))
                or not all(isinstance(value, str) and COMPONENT_VERSION.fullmatch(value) for value in capabilities)
                or set(capabilities) - INSTALLATION_CAPABILITIES
                or fingerprint != profile_fingerprint(list(state["recorded_components"]))
            ):
                raise InstallerError(f"Capacidades ou fingerprint inválidos no estado da instalação: {path}")
        return state

    def write_install_state(
        self,
        profile: str,
        requested: list[str],
        *,
        known: list[str] | None = None,
        capabilities: list[str] | None = None,
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
        descriptor, temporary_name = tempfile.mkstemp(prefix=".state-", suffix=".json", dir=destination.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(state, output, ensure_ascii=False, indent=2, sort_keys=True)
                output.write("\n")
            if os.name != "nt":
                temporary.chmod(0o644)
            temporary.replace(destination)
        finally:
            if lexists(temporary):
                remove_path(temporary)
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
            try:
                state = self.validate_install_state(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError) as error:
                raise InstallerError(f"Estado da instalação inválido: {path}") from error
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
                console.detail(f"Novidades: {package['release_url']}")

    def choose_components(self) -> list[str]:
        print("\nQual conjunto de componentes x86QW deseja instalar ou atualizar?")
        print("  1) recomendado - experiência nQuake atualizada sem addons grandes (padrão)")
        print("  2) essencial   - configuração, interface principal e KTX")
        print("  3) completo    - nQuake, QRP, Final Arena, Pro-X, Team Fortress e TD2")
        print("  4) personalizado - escolha cada componente ou addon")
        aliases = {"": "recommended", "1": "recommended", "2": "essential", "3": "complete", "4": "custom"}
        while True:
            try:
                profile = aliases.get(input("Escolha [1/2/3/4] (padrão: 1): ").strip().lower())
            except EOFError:
                profile = "recommended"
            if profile:
                break
            console.warning("Opção inválida. Digite 1, 2, 3 ou 4.")
        if profile != "custom":
            selected = list(self.component_catalog["profiles"][profile])
            requested: list[str] = []
        else:
            self.show_components()
            try:
                answer = input("Informe números ou identificadores separados por vírgula: ").strip()
            except EOFError as error:
                raise InstallerError("Nenhum componente x86QW foi selecionado.") from error
            selected = []
            ordered = list(self.components)
            for token in (item.strip() for item in answer.split(",")):
                if token.isdigit() and 1 <= int(token) <= len(ordered):
                    identifier = ordered[int(token) - 1]
                elif token in self.components:
                    identifier = token
                else:
                    raise InstallerError(f"Componente x86QW desconhecido: {token or '(vazio)'}")
                if identifier not in selected:
                    selected.append(identifier)
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
                print(f"    novidades: {package['release_url']}")
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
        if not receipt.is_file() or receipt.is_symlink():
            raise InstallerError(f"Recibo da CLI x86QW inválido: {receipt}")
        try:
            metadata = json.loads(receipt.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise InstallerError(f"Recibo da CLI x86QW inválido: {receipt}") from error
        if not isinstance(metadata, dict):
            raise InstallerError(f"Recibo da CLI x86QW inválido: {receipt}")
        version = metadata.get("version")
        if metadata.get("format") != 1 or metadata.get("project") != "x86qw" or not isinstance(version, str):
            raise InstallerError(f"Recibo da CLI x86QW inválido: {receipt}")
        if not STABLE_VERSION.fullmatch(version):
            raise InstallerError(f"Versão inválida no recibo da CLI x86QW: {version}")
        return metadata

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
        self.stage = Path(tempfile.mkdtemp(prefix=".x86qw-update.", dir=self.target))
        try:
            if self.update_ui:
                console.heading("Downloading x86QW installer")
            artifact = self.download_component_package(package)
            extracted = self.stage / "installer"
            extracted.mkdir()
            safe_extract_zip(artifact, extracted)
            bundle = extracted / f"x86qw-installer-{available}"
            application = bundle / CLI_ARCHIVE_NAME
            metadata = bundle / OUTER_INSTALLER_METADATA
            if not application.is_file() or application.is_symlink() or not metadata.is_file() or metadata.is_symlink():
                raise InstallerError("O bundle de atualização x86QW está incompleto.")
            try:
                identity = json.loads(metadata.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise InstallerError("Metadados inválidos no bundle de atualização x86QW.") from error
            if identity != {"format": 1, "project": "x86qw", "version": available}:
                raise InstallerError("A versão interna do bundle de atualização x86QW é inválida.")
            embedded = read_zipapp_json(
                application, INSTALLER_BUNDLE_METADATA, "Identidade interna da CLI",
            )
            if embedded != identity:
                raise InstallerError("A identidade do aplicativo x86QW diverge do bundle.")
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
        while True:
            try:
                answer = input(
                    f"\n==> Do you want to proceed with the {action}? [y/n] "
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
                console.heading("Downloading package manifests")
            else:
                console.info(message)
            catalog_url = os.environ.get("X86_QW_CATALOG_URL")
            local_catalog = self.project_root / PUBLIC_CATALOG
            catalog_payload: bytes | None = None
            catalog_status = "Loaded"
            try:
                if catalog_url:
                    catalog_payload = self.http_get(
                        catalog_url, timeout=CATALOG_TIMEOUT, attempts=2,
                    )
                    catalog = json.loads(catalog_payload)
                    catalog_status = "Downloaded"
                    console.detail(f"Catálogo remoto explícito: {catalog_url}")
                elif not self.online_only and local_catalog.is_file() and not local_catalog.is_symlink():
                    catalog_payload = local_catalog.read_bytes()
                    catalog = json.loads(catalog_payload)
                    console.detail(f"Catálogo da distribuição local: {local_catalog}")
                else:
                    last_error: InstallerError | None = None
                    selected_url: str | None = None
                    for index, url in enumerate(CATALOG_URLS):
                        try:
                            catalog_payload = self.http_get(
                                url, timeout=CATALOG_TIMEOUT, attempts=1,
                            )
                            selected_url = url
                            break
                        except InstallerError as error:
                            last_error = error
                            console.detail(str(error))
                            if index + 1 < len(CATALOG_URLS):
                                host = urllib.parse.urlsplit(url).hostname or url
                                console.warning(
                                    f"Catálogo indisponível em {host}; tentando o próximo mirror..."
                                )
                    if catalog_payload is None or selected_url is None:
                        raise InstallerError(
                            f"Nenhum mirror do catálogo x86QW respondeu: {last_error}"
                        )
                    catalog = json.loads(catalog_payload)
                    catalog_status = "Downloaded"
                    console.detail(f"Catálogo público: {selected_url}")
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
        last_error: InstallerError | None = None
        for index, url in enumerate(package["urls"]):
            try:
                self.http_get(url, temporary)
                if temporary.stat().st_size != package["size"] or file_hash(temporary) != digest:
                    raise InstallerError(f"O mirror entregou um pacote inválido: {url}")
                temporary.replace(artifact)
                if self.update_ui:
                    console.download_result(
                        f"{identifier} {package['version']}", size=artifact.stat().st_size,
                    )
                else:
                    console.success(f"Pacote baixado e validado: {filename}")
                return artifact
            except InstallerError as error:
                last_error = error
                if lexists(temporary):
                    remove_path(temporary)
                if index + 1 < len(package["urls"]):
                    console.warning("Mirror indisponível ou inválido; tentando a próxima cópia...")
        raise InstallerError(f"Nenhum mirror entregou o pacote {identifier}: {last_error}")

    def component_source_context(self) -> object | None:
        if self.online_only:
            return None
        distribution = self.project_root / "dist"
        if not (distribution / "distributions/nquake").is_dir():
            return None
        if self._component_source_context is None:
            try:
                from maintenance.tools.component_sources import load_source_context

                self._component_source_context = load_source_context(
                    distribution,
                    self.project_root / DEVELOPMENT_COMPONENT_CATALOG,
                    self.project_root / COMPONENT_RELEASES,
                )
            except (OSError, ValueError, json.JSONDecodeError, tarfile.TarError, zipfile.BadZipFile) as error:
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
        identifier = str(package["package"])
        try:
            from maintenance.tools.component_sources import resolve_component_payloads

            release, source_revision, payloads = resolve_component_payloads(context, identifier)
        except (OSError, ValueError, json.JSONDecodeError, tarfile.TarError, zipfile.BadZipFile) as error:
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
        root.mkdir()
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
        self.stage = Path(tempfile.mkdtemp(prefix=".quake-migrate.", dir=self.target))
        try:
            inventory = self.stage / f"{component}.inventory"
            inventory.write_text(
                "".join(f"{name}\t{digest}\n" for name, digest in entries if name not in mutable),
                encoding="utf-8",
            )
            if os.name != "nt":
                inventory.chmod(0o644)
            self.validate_inventory(inventory)
            staged_receipt = self.stage / f"{component}.receipt"
            self.write_component_receipt(
                component, receipt["selection"], receipt["source"], inventory, staged_receipt,
            )
            self.commit_component_metadata(component, inventory, staged_receipt)
        finally:
            self.cleanup_stage()
            self.stage = previous_stage
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
        inventory.write_text(
            "".join(f"{name}\t{digest}\n" for name, digest in remaining),
            encoding="utf-8",
        )
        if os.name != "nt":
            inventory.chmod(0o644)
        self.validate_inventory(inventory)
        staged_receipt = self.stage / "play-support-migrated.receipt"
        self.write_component_receipt(
            "play-support", receipt["selection"], receipt["source"], inventory, staged_receipt,
        )
        self.commit_component_metadata("play-support", inventory, staged_receipt)

    def install_components(self, selected: list[str]) -> None:
        assert self.stage is not None
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
            count = self.install_component_overlay(
                identifier, managed, str(package["version"]), source,
            )
            for staged, destination in defaults:
                if not lexists(destination):
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(staged, destination)
                    console.info(f"Configuração inicial criada: {destination}")
            if identifier == "nquake-bootstrap":
                self.migrate_nquake_texture_limit()
            console.success(f"{component['label']} atualizado ({file_count(count)}).")
        if "nquake-bootstrap" in selected:
            preset = self.target / "ezquake/configs/preset.cfg"
            if not preset.is_file():
                preset.parent.mkdir(parents=True, exist_ok=True)
                preset.write_text(DEFAULT_PRESET, encoding="utf-8")
        self.migrate_saved_configs()
        self.refresh_qw_package_order()
        self.reconcile_play_support()

    def play_support_player(self):
        gameplay = importlib.import_module("gameplay")
        return gameplay.Player(
            self.project_root, self.target, online_only=self.online_only,
        )

    def reconcile_play_support(
        self,
        *,
        dry_run: bool = False,
        plan_rows: list[UpdatePlanRow] | None = None,
    ) -> bool:
        player = self.play_support_player()
        games = player.available_local_games()
        issues = player.local_play_support_issues(games)
        if not issues:
            return False
        if dry_run:
            if plan_rows is not None:
                plan_rows.append(UpdatePlanRow(
                    "Gerado", "Suporte de execução dos mods",
                    "ausente ou divergente", "derivado dos componentes instalados", "Reparar",
                ))
            return True
        player.ensure_local_play_support(games)
        player.verify_local_play_support(games)
        console.success("Suporte de execução derivado foi reconciliado.")
        return True

    def migrate_nquake_texture_limit(self) -> None:
        migrated = 0
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
            descriptor, temporary_name = tempfile.mkstemp(prefix=f".{config.name}.", dir=config.parent)
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as output:
                    output.write(updated)
                if os.name != "nt":
                    temporary.chmod(0o644)
                temporary.replace(config)
            finally:
                if lexists(temporary):
                    remove_path(temporary)
            migrated += 1
        if migrated:
            console.info(
                f"Limite de textura nQuake ajustado de 32768 para 16384 em {file_count(migrated)}; "
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

    def migrate_saved_configs(self) -> None:
        aliases = self.managed_temporary_aliases()
        configs = sorted(set(self.target.glob("*/configs/config.cfg")))
        prox = self.target / "prox/configs/config.cfg"
        base = self.target / "ezquake/configs/config.cfg"
        prox_migrated = False
        if prox.is_file() and not prox.is_symlink():
            contents = prox.read_bytes()
            if b"// Niclas's config" in contents:
                backup = prox.with_name("config.pre-x86qw.cfg")
                if not lexists(backup):
                    shutil.copy2(prox, backup)
                if not base.is_file() or base.is_symlink():
                    raise InstallerError("A migração do Pro-X exige ezquake/configs/config.cfg válido.")
                modern = (
                    b"// x86QW: base Pro-X migrada; original preservado em config.pre-x86qw.cfg\n"
                    + base.read_bytes()
                )
                self.write_personal_config(prox, modern)
                prox_migrated = True

        changed = 0
        alias_pattern = re.compile(rb'^\s*alias\s+([^\s]+).*(?:\r?\n|$)', re.MULTILINE | re.IGNORECASE)
        broken_remote_capabilities = re.compile(
            rb'^\s*cl_remote_capabilities\s+"\$cl_remote_capabilities,[^"]*"\s*(?:\r?\n|$)',
            re.MULTILINE | re.IGNORECASE,
        )
        for config in configs:
            if not config.is_file() or config.is_symlink():
                raise InstallerError(f"Configuração pessoal inválida: {config}")
            original = config.read_bytes()

            def keep_personal_alias(match: re.Match[bytes]) -> bytes:
                name = match.group(1).decode("utf-8", errors="replace").casefold()
                return b"" if name in aliases else match.group(0)

            updated = alias_pattern.sub(keep_personal_alias, original)
            updated = broken_remote_capabilities.sub(b"", updated)
            if updated != original:
                backup = config.with_name("config.aliases-pre-x86qw.cfg")
                if not lexists(backup):
                    shutil.copy2(config, backup)
                self.write_personal_config(config, updated)
                changed += 1
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
        print("\nComponentes x86QW instalados:")
        for index, identifier in enumerate(installed, 1):
            print(f"  {index:2d}) {self.components[identifier]['label']}")
        try:
            answer = input("Informe os números a remover, separados por vírgula: ").strip()
        except EOFError as error:
            raise InstallerError("Nenhum componente foi selecionado para remoção.") from error
        selected: list[str] = []
        for token in (item.strip() for item in answer.split(",")):
            if not token.isdigit() or not 1 <= int(token) <= len(installed):
                raise InstallerError(f"Número de componente inválido: {token or '(vazio)'}")
            identifier = installed[int(token) - 1]
            if identifier not in selected:
                selected.append(identifier)
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
        print("\nO que deseja fazer com os componentes x86QW?")
        print("  1) instalar ou atualizar")
        print("  2) remover componentes instalados")
        while True:
            try:
                answer = input("Escolha [1/2]: ").strip()
            except EOFError as error:
                raise InstallerError("Nenhuma operação foi selecionada.") from error
            if answer in ("1", "2"):
                break
            console.warning("Opção inválida. Digite 1 para instalar/atualizar ou 2 para remover.")
        self.check_paks()
        if answer == "2":
            selected = self.choose_components_to_remove()
            for identifier in selected:
                removed = self.remove_component(identifier)
                console.success(f"{self.components[identifier]['label']} removido ({file_count(removed)}).")
            self.refresh_qw_package_order()
            self.reconcile_play_support()
            self.write_install_state("custom" if self.installed_components() else "none", self.installed_components())
            return
        selected = self.choose_components()
        self.stage = Path(tempfile.mkdtemp(prefix=".quake-install.", dir=self.target))
        self.install_components(selected)
        self.write_install_state(self.selected_component_profile, self.requested_components)

    def install_component_phase(self) -> None:
        assert self.stage is not None
        console.section("Fase 2/2 · Componentes x86QW")
        selected = self.choose_components()
        self.install_components(selected)
        self.write_install_state(self.selected_component_profile, self.requested_components)

    def hub_servers(self) -> list[dict[str, object]]:
        console.info("Consultando servidores ativos no QuakeWorld Hub...")
        try:
            servers = json.loads(self.http_get(HUB_SERVERS_API))
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
            if spec.key == "macos" and self.macos_app_needs_preparation(runtime):
                raise InstallerError(
                    f"O runtime macOS requer reparo antes da execução: {runtime}. Execute update."
                )
            choices.append((f"ezQuake {channel} {receipt['selection']}", runtime))
        return choices

    def choose_host_runtime(self) -> tuple[str, Path]:
        choices = self.host_runtimes()
        if not choices:
            raise InstallerError("Nenhum ezQuake gerenciado para este sistema está instalado. Execute install primeiro.")
        if len(choices) == 1:
            return choices[0]
        print("\nQual cliente deseja abrir?")
        for index, (label, _) in enumerate(choices, 1):
            print(f"  {index:3d}) {label}")
        while True:
            try:
                answer = input("Escolha o número: ").strip()
            except EOFError as error:
                raise InstallerError("Nenhum cliente foi selecionado.") from error
            if answer.isdigit() and 1 <= int(answer) <= len(choices):
                return choices[int(answer) - 1]
            console.warning(f"Número inválido. Escolha um valor entre 1 e {len(choices)}.")

    def launch_runtime(self, runtime: Path, quake_arguments: list[str]) -> None:
        system = host_platform.system()
        # A distribuição é autocontida: ~/.ezquake não pode sobrepor configs ou assets.
        base_arguments = ["-nohome", "-basedir", str(self.target)]
        if os.environ.get("X86QW_TEST_WINDOWED") == "1":
            # Smoke tests must not capture the user's display. Fullscreen is
            # exercised only by tests that explicitly clear this environment.
            # Disable nQuake's automatic config save before any test command so
            # -window can never leak into the player's persistent config.cfg.
            base_arguments.extend([
                "-window", "-width", "1280", "-height", "720",
                "+cfg_save_onquit", "0",
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
            subprocess.Popen(command, **options)
        except OSError as error:
            raise InstallerError(f"Não foi possível abrir {runtime}: {error}") from error

    def browse_hub(self) -> None:
        servers = self.hub_servers()
        print("\nServidores ativos (jogadores humanos primeiro):")
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
            print(f"  {index:3d}) {humans:2d} {human_label:7} + {bots:2d} {bot_label:4}  {mode:12} {map_name:18} {hostname}")
        print("\nDigite um número para jogar, oN para observar, qN para usar QTV, ou Enter para sair.")
        while True:
            try:
                answer = input("Escolha: ").strip().lower()
            except EOFError:
                answer = ""
            if not answer:
                console.info("Hub fechado; nenhum cliente foi aberto.")
                return
            match = re.fullmatch(r"([oq]?)([0-9]+)", answer)
            if not match or not 1 <= int(match.group(2)) <= len(servers):
                console.warning(f"Escolha inválida. Use 1 a {len(servers)}, oN ou qN.")
                continue
            mode, number = match.group(1), int(match.group(2))
            server = servers[number - 1]
            address = str(server["address"])
            if mode == "q":
                qtv = server.get("qtv_stream")
                qtv_url = qtv.get("url", "") if isinstance(qtv, dict) else ""
                if not isinstance(qtv_url, str) or not re.fullmatch(r"[0-9]+@[A-Za-z0-9_.:\[\]-]+:[0-9]{1,5}", qtv_url):
                    console.warning("Este servidor não publicou um stream QTV; escolha jogar ou observar diretamente.")
                    continue
                quake_arguments = ["+qtvplay", qtv_url]
                operation = "QTV"
            elif mode == "o":
                quake_arguments = ["+observe", address]
                operation = "observação"
            else:
                quake_arguments = ["+join", address]
                operation = "conexão"
            label, runtime = self.choose_host_runtime()
            self.launch_runtime(runtime, quake_arguments)
            console.success(f"{label} aberto para {operation} em {address}.")
            return

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
                if spec.key == "macos" and self.macos_app_needs_preparation(runtime):
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
            raise InstallerError("Metadados nQuake antigos encontrados. Execute components para migrar a instalação.")
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
                if spec.key == "macos" and self.macos_app_needs_preparation(runtime):
                    issues.append(ClientRepairIssue(
                        spec, channel, receipt_path, receipt,
                        "preparação macOS ausente", "macos-preparation", None, "local-repair",
                    ))
        return issues, diagnostics

    def runtime_permission_repairs(self, installed: set[str] | None = None) -> list[Path]:
        if os.name == "nt":
            return []
        repairs: list[Path] = []
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
                if binary.is_file() and not binary.is_symlink() and not os.access(binary, os.X_OK):
                    repairs.append(binary)
        return repairs

    def repair_plan(self) -> RepairAssessment:
        self.check_paks()
        valid_metadata, metadata_diagnostics = self.component_metadata_assessment()
        state_path = self.target / INSTALL_STATE
        recovered_state: dict[str, object] | None = None
        try:
            if state_path.is_file() and not state_path.is_symlink():
                state = json.loads(state_path.read_text(encoding="utf-8"))
                state = self.current_install_state(self.validate_install_state(state))
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
        except (OSError, json.JSONDecodeError, InstallerError) as error:
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

    def repair_client_runtime(self, issue: ClientRepairIssue) -> None:
        if issue.release is None:
            raise InstallerError(
                f"A versão registrada do ezQuake {issue.channel} não está disponível para reparo."
            )
        self.spec = issue.spec
        self.channel = issue.channel
        self.configure_release(issue.release)
        self.ensure_macos_ezquake_closed()
        self.stage = Path(tempfile.mkdtemp(prefix=".x86qw-client-repair.", dir=self.target))
        try:
            self.prepare_cache()
            archive = self.ensure_archive()
            prepared = self.prepare_runtime(archive)
            staged_receipt = self.stage / "ezquake-receipt"
            self.write_ezquake_receipt(staged_receipt)
            self.commit_runtime(prepared, staged_receipt)
            self.reset_macos_game_directory()
        finally:
            self.cleanup_stage()
            self.stage = None
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
        for issue in assessment.client_issues:
            runtime = self.target / issue.spec.runtime(issue.channel)
            if issue.mode == "permission":
                runtime.chmod(runtime.stat().st_mode | 0o100)
                console.success(f"Permissão de execução restaurada em {runtime}.")
            elif issue.mode == "macos-preparation":
                self.repair_installed_macos_runtime(
                    issue.spec, issue.channel, issue.receipt_path, issue.receipt,
                )
        for binary in assessment.permission_repairs:
            binary.chmod(binary.stat().st_mode | 0o100)
            console.success(f"Permissão de execução restaurada em {binary}.")
        unavailable_clients = [issue for issue in payload_clients if issue.release is None]
        if unavailable_clients or (
            not allow_download and (payload_clients or assessment.invalid_components)
        ):
            raise InstallerError(
                "O plano exige payload validado. A CLI instalada não baixa conteúdo durante repair; "
                "reexecute o bootstrap install.sh no mesmo destino."
            )
        for issue in payload_clients:
            self.repair_client_runtime(issue)
        if assessment.invalid_components:
            self.stage = Path(tempfile.mkdtemp(prefix=".x86qw-repair.", dir=self.target))
            try:
                self.install_components(list(assessment.invalid_components))
            finally:
                self.cleanup_stage()
                self.stage = None
        elif assessment.support_invalid:
            self.reconcile_play_support()
        if assessment.package_order_invalid:
            self.refresh_qw_package_order()
        state = assessment.recovered_state or self.load_install_state(persist_migration=True)
        self.write_install_state(
            str(state["profile"]), list(state["requested_components"]),
            known=list(state["known_components"]), capabilities=list(state["capabilities"]),
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

    def migrate_metadata_layout(self) -> bool:
        if not self.legacy_metadata_present():
            return False
        metadata = self.target / METADATA_DIR
        self.ensure_metadata_directory()

        legacy_cli = self.target / LEGACY_CLI_RECEIPT
        if lexists(legacy_cli):
            self.cli_receipt_path()
            canonical_cli = self.target / CLI_RECEIPT
            canonical_cli.parent.mkdir(parents=True, exist_ok=True)
            if not lexists(canonical_cli):
                temporary = canonical_cli.with_name(".receipt.new")
                shutil.copy2(legacy_cli, temporary)
                self.validate_cli_receipt(temporary)
                temporary.replace(canonical_cli)
            remove_path(legacy_cli)

        for spec in PLATFORMS.values():
            for channel in ("stable", "nightly"):
                legacy = self.target / spec.legacy_receipt(channel)
                if not lexists(legacy):
                    continue
                self.ezquake_receipt_path(spec, channel)
                canonical = self.target / spec.receipt(channel)
                canonical.parent.mkdir(parents=True, exist_ok=True)
                if not lexists(canonical):
                    temporary = canonical.with_name(f".{channel}.receipt.new")
                    shutil.copy2(legacy, temporary)
                    self.validate_ezquake_receipt(temporary, spec, channel)
                    temporary.replace(canonical)
                remove_path(legacy)

        previous_stage = self.stage
        migration_stage = Path(tempfile.mkdtemp(prefix=".x86qw-metadata.", dir=self.target))
        self.stage = migration_stage
        try:
            for component in self.metadata_component_ids():
                canonical = self.component_pair_paths(component, metadata)
                legacy = self.component_pair_paths(component, metadata, legacy=True)
                if not any(lexists(path) for path in legacy):
                    continue
                self.validate_component_pair(component)
                if not all(lexists(path) for path in canonical):
                    self.commit_component_metadata(component, legacy[1], legacy[0])
                else:
                    for path in legacy:
                        remove_path(path)
        finally:
            self.cleanup_stage()
            self.stage = previous_stage
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
        self.ensure_macos_ezquake_closed()
        self.check_runtime_destination_ownership()
        self.prepare_install_target()
        self.reject_target_symlinks()
        self.stage = Path(tempfile.mkdtemp(prefix=".quake-install.", dir=self.target))
        self.provision_install_target()
        self.check_paks()
        pak0_before = file_hash(self.target / "id1/pak0.pak")
        pak1_before = file_hash(self.target / "id1/pak1.pak")
        self.prepare_cache()
        archive = self.ensure_archive()
        assert self.spec is not None
        console.info(f"Preparando ezQuake {self.spec.label} {self.channel} {self.selected_version}...")
        prepared = self.prepare_runtime(archive)
        staged_receipt = self.stage / "ezquake-receipt"
        self.write_ezquake_receipt(staged_receipt)
        self.ensure_metadata_directory()
        self.commit_runtime(prepared, staged_receipt)
        self.reset_macos_game_directory()
        console.success("ezQuake instalado e recibo registrado.")
        if self.is_native_macos_install():
            console.info(f"Na primeira abertura, selecione este diretório quando o macOS solicitar: {self.target}")
        if self.confirm_components():
            self.install_component_phase()
        else:
            console.info("Dados nQuake não solicitados; esta etapa foi ignorada.")
            self.write_install_state("none", [])
        if file_hash(self.target / "id1/pak0.pak") != pak0_before or file_hash(self.target / "id1/pak1.pak") != pak1_before:
            raise InstallerError("Um PAK registrado foi alterado durante a instalação; a operação foi interrompida.")
        console.section("Verificação final")
        self.verify_installation()
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
    ) -> bool:
        self.spec = spec
        self.channel = channel
        selected = self.latest_release()
        available = selected[0]
        installed = receipt["selection"]
        runtime = self.target / spec.runtime(channel)
        needs_macos_repair = spec.key == "macos" and self.macos_app_needs_preparation(runtime)
        if available == installed and not needs_macos_repair:
            return False
        if available == installed:
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
        if not self.release_is_newer(available, installed, channel):
            console.warning(
                f"ezQuake {spec.label} {channel} instalado ({installed}) é mais novo que o catálogo ({available}); preservado."
            )
            return False

        self.configure_release(selected)

        if dry_run:
            if plan_rows is not None:
                plan_rows.append(UpdatePlanRow(
                    "Cliente", f"ezQuake {spec.label} {channel}", installed, available, "Atualizar",
                    self.app_expected_size or None,
                ))
            return True

        self.ensure_macos_ezquake_closed()
        self.check_runtime_destination_ownership()
        self.stage = Path(tempfile.mkdtemp(prefix=".x86qw-runtime-update.", dir=self.target))
        try:
            self.prepare_cache()
            archive = self.ensure_archive()
            console.info(f"Atualizando ezQuake {spec.label} {channel}: {installed} → {available}...")
            prepared = self.prepare_runtime(archive)
            staged_receipt = self.stage / "ezquake-receipt"
            self.write_ezquake_receipt(staged_receipt)
            self.commit_runtime(prepared, staged_receipt)
            self.reset_macos_game_directory()
        finally:
            self.cleanup_stage()
            self.stage = None
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
        if not dry_run and layout_change:
            self.migrate_metadata_layout()
        self.check_paks()
        state_path = self.target / INSTALL_STATE
        persisted_state: dict[str, object] | None = None
        if state_path.is_file() and not state_path.is_symlink():
            try:
                persisted_state = self.validate_install_state(
                    json.loads(state_path.read_text(encoding="utf-8"))
                )
            except (OSError, json.JSONDecodeError) as error:
                raise InstallerError(f"Estado da instalação inválido: {state_path}") from error
        state = self.current_install_state(
            self.load_install_state(persist_migration=not dry_run)
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
        if not dry_run:
            console.section("Clientes ezQuake instalados")
        for spec, channel, receipt in runtimes:
            changed = self.update_runtime(
                spec, channel, receipt, dry_run=dry_run, preview=preview, plan_rows=plan_rows,
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
                    removed = self.remove_component(identifier)
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
                self.stage = Path(tempfile.mkdtemp(prefix=".x86qw-components-migrate.", dir=self.target))
                try:
                    self.install_components(replacements)
                finally:
                    self.cleanup_stage()
                    self.stage = None
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
                self.stage = Path(tempfile.mkdtemp(prefix=".x86qw-components-update.", dir=self.target))
                try:
                    self.install_components(outdated)
                finally:
                    self.cleanup_stage()
                    self.stage = None
            changed = True
        elif not dry_run and not self.installed_components():
            console.info("Nenhum componente x86QW está instalado; nenhum componente novo foi adicionado.")

        changed = self.reconcile_play_support(
            dry_run=dry_run, plan_rows=plan_rows,
        ) or changed

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
            )
            if changed and not profile_upgrade:
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
            self.load_install_state(persist_migration=not dry_run)
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
            self.stage = Path(tempfile.mkdtemp(prefix=".x86qw-profile-upgrade.", dir=self.target))
            try:
                self.install_components(missing)
            finally:
                self.cleanup_stage()
                self.stage = None
            changed = True

        if not dry_run:
            self.write_install_state(
                str(state["profile"]), list(state["requested_components"]), known=list(self.components),
                capabilities=list(state["capabilities"]),
            )
            if changed:
                console.section("Verificação final do perfil")
                self.verify_installation()
        if not dry_run and changed:
            console.success("Distribuição atualizada conforme o perfil da instalação.")
        return changed

    def cleanup_stage(self) -> None:
        if self.stage is not None and self.stage.is_dir():
            remove_path(self.stage)

    def install_online_cli(self) -> None:
        if not self.online_only:
            return
        identity = self.installer_bundle_identity()
        cli_version = str(identity["version"])
        metadata_root = self.target / METADATA_DIR
        metadata_root.mkdir(parents=True, exist_ok=True)
        cli_root = metadata_root / "cli"
        replacement = Path(tempfile.mkdtemp(prefix=".cli-new.", dir=metadata_root))
        backup = metadata_root / f".cli-old.{os.getpid()}"
        try:
            application = ZIPAPP_PATH or self.project_root / CLI_ARCHIVE_NAME
            if not application.is_file() or application.is_symlink():
                raise InstallerError(f"Aplicativo da CLI pública ausente ou inválido: {application}")
            embedded = read_zipapp_json(
                application, INSTALLER_BUNDLE_METADATA, "Identidade interna da CLI",
            )
            if embedded != identity:
                raise InstallerError("A identidade do aplicativo x86QW diverge do recibo do bundle.")
            destination = replacement / CLI_ARCHIVE_NAME
            shutil.copyfile(application, destination)
            if os.name != "nt":
                destination.chmod(0o644)
            if lexists(cli_root):
                if not cli_root.is_dir() or cli_root.is_symlink():
                    raise InstallerError(f"Diretório da CLI instalada inválido: {cli_root}")
                if lexists(backup):
                    raise InstallerError(f"Backup temporário inesperado da CLI: {backup}")
                os.replace(cli_root, backup)
            try:
                os.replace(replacement, cli_root)
            except OSError:
                if lexists(backup) and not lexists(cli_root):
                    os.replace(backup, cli_root)
                raise
            if lexists(backup):
                remove_path(backup)
        finally:
            if lexists(replacement):
                remove_path(replacement)

        cli_receipt = self.target / CLI_RECEIPT
        cli_receipt.parent.mkdir(parents=True, exist_ok=True)
        temporary_receipt = cli_receipt.with_name(cli_receipt.name + ".new")
        temporary_receipt.write_text(
            json.dumps(identity, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.validate_cli_receipt(temporary_receipt)
        temporary_receipt.replace(cli_receipt)
        remove_path(self.target / LEGACY_CLI_RECEIPT)

        launchers = (
            ("x86qw.sh", 0o755),
            ("x86qw.cmd", 0o644),
        )
        for name, mode in launchers:
            source = self.project_root / name
            if not source.is_file() and ZIPAPP_PATH is None:
                source = self.project_root / "dist/installer/bin" / name
            if not source.is_file() or source.is_symlink():
                raise InstallerError(f"Launcher público ausente ou inválido: {source}")
            destination = self.target / name
            temporary = destination.with_name(destination.name + ".new")
            temporary.write_bytes(source.read_bytes())
            if os.name != "nt":
                temporary.chmod(mode)
            temporary.replace(destination)

        shell_launcher = self.target / "x86qw.sh"
        console.success(f"CLI permanente instalada: {shell_launcher} (versão {cli_version})")


class FriendlyArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: erro: {message}\n")


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
        "--platform", choices=tuple(PLATFORMS), metavar="SO",
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
            "install, play, host, proxy, qtv, version, update, upgrade, repair, components, presets, hub, "
            "verify, uninstall ou cleanup"
        ),
    )
    parser.add_argument(
        "target", nargs="?", type=Path,
        help="diretório de instalação (o instalador público pergunta antes de iniciar)",
    )
    namespace = parser.parse_args(arguments)
    valid_actions = (
        "install", "play", "host", "proxy", "qtv", "version", "update", "upgrade", "repair", "components",
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
            services = importlib.import_module("services")
            services.recover_sessions(installer.target)
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
                    console.heading("Dry run complete; no files were changed")
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
                console.heading("Already up-to-date")
                console.success(message)
                return 0
            console.update_plan(plan_rows, options.action)
            if options.dry_run:
                console.heading("Dry run complete; no files were changed")
                return 0
            if not installer.confirm_update_plan(options.action, assume_yes=options.yes):
                return 0
            console.heading(
                "Updating packages" if options.action == "update" else "Upgrading packages"
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
            gameplay = importlib.import_module("gameplay")
            return gameplay.main(raw_arguments[1:])
        if raw_arguments[:1] and raw_arguments[0] in {"host", "proxy", "qtv"}:
            services = importlib.import_module("services")
            return services.main(raw_arguments)
        options = parse_arguments(raw_arguments, project_root)
        console.configure(verbose=options.verbose, no_color=options.no_color)
        if options.action == "version":
            print(f"x86QW {application_version()}")
            return 0
        if options.online_only and options.target is None:
            options.target = choose_public_target()
        if options.action == "play":
            gameplay = importlib.import_module("gameplay")
            play_arguments = [str(options.target)]
            if options.verbose:
                play_arguments.insert(0, "--verbose")
            if options.no_color:
                play_arguments.insert(0, "--no-color")
            return gameplay.main(play_arguments)
        return execute_manager_action(options, project_root)
    except KeyboardInterrupt:
        console.error("Operação cancelada. Nenhuma seleção pendente foi aplicada.")
        return 130
    except (InstallerError, session_control.SessionControlError) as error:
        console.error(str(error))
        if options is not None and not options.verbose:
            print("       Execute novamente com --verbose para obter detalhes técnicos.", file=sys.stderr)
        return 1
    except Exception as error:  # pragma: no cover - last-resort CLI protection
        console.error(f"Falha inesperada: {error}")
        if options is not None and options.verbose:
            traceback.print_exc()
        else:
            print("       Execute novamente com --verbose para exibir o diagnóstico completo.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
