#!/usr/bin/env python3
"""Cross-platform ezQuake + x86QW component installer."""

from __future__ import annotations

import argparse
import errno
import hashlib
import http.client
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

from maintenance.tools.components import components_by_id, load_catalog as load_component_catalog, resolve_dependencies
from maintenance.tools.component_sources import (
    ComponentSourceContext,
    load_source_context,
    resolve_component_payloads,
)


ID1_PAK0_SHA256 = "eec9a020b6d8b6df73a5b911e19985f6e2539c1c6857b4a9f400553b9599677d"
ID1_PAK1_SHA256 = "94e355836ec42bc464e4cbe794cfb7b5163c6efa1bcc575622bb36475bf1cf30"
CATALOG_URL = "https://x86qw.x86.com.br/api/v1/catalog.json"
METADATA_DIR = ".install"
# Legacy aggregate receipt names kept only for one-way migration and uninstall.
NQUAKE_RECEIPT = ".install/nquake.receipt"
NQUAKE_INVENTORY = ".install/nquake.inventory"
COMPONENT_CATALOG = "maintenance/inventory/components.json"
COMPONENT_RELEASES = "maintenance/inventory/component-releases.json"
PUBLIC_CATALOG = Path("site/public/api/v1/catalog.json")
BUNDLED_ID1_DIR = Path("dist/id1")
CACHE_DIR_NAME = "x86-qw"
CACHE_MARKER_NAME = ".x86-qw-cache"
CACHE_MARKER_VALUE = "x86-qw-cache-v1"
MACOS_PREFERENCES_DOMAIN = "com.ezquake.ezQuake"
MACOS_DIRECTORY_KEYS = ("basedir", "version", "NSOSPLastRootDirectory")
DEFAULT_PRESET = 's_raw_volume "0.2"\n'
STABLE_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
NIGHTLY_VERSION = re.compile(r"^[0-9]{8}-[0-9]{6}_[0-9a-f]{7}$")
COMPONENT_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HUB_SERVERS_API = "https://hubapi.quakeworld.nu/v2/servers/mvdsv?empty=exclude&limit=20"
MAPS_RECEIPT = ".install/maps.receipt"
MAPS_INVENTORY = ".install/maps.inventory"
PRESETS_RECEIPT = ".install/presets.receipt"
PRESETS_INVENTORY = ".install/presets.inventory"
PLAY_SUPPORT_RECEIPT = ".install/play-support.receipt"
PLAY_SUPPORT_INVENTORY = ".install/play-support.inventory"
PLAY_SUPPORT_VERSION = "4"
MUTABLE_COMPONENT_DEFAULTS = {
    "clan-arena": ("prox/configs/config.cfg",),
}
LEGACY_COMPONENTS = frozenset({"clan-arena"})
PROFILED_LOCAL_GAMES = frozenset({"ktx", "final-arena", "pro-x", "team-fortress", "td2"})
ReleaseRecord = tuple[str, tuple[str, ...], str]
INSTALLER_ROOT = Path(__file__).resolve().parent


def create_resilient_connection(
    address: tuple[str, int],
    timeout: float | object = socket._GLOBAL_DEFAULT_TIMEOUT,
    source_address: tuple[str, int] | None = None,
) -> socket.socket:
    """Connect to the first reachable DNS address without waiting on a dead first result."""
    host, port = address
    candidates = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    if not candidates:
        raise OSError(f"Nenhum endereço foi encontrado para {host}.")
    effective_timeout = socket.getdefaulttimeout() if timeout is socket._GLOBAL_DEFAULT_TIMEOUT else timeout
    deadline = None if effective_timeout is None else time.monotonic() + float(effective_timeout)
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


@dataclass(frozen=True)
class LocalGameSpec:
    key: str
    label: str
    gamedir: str
    profile: str
    component: str
    marker: str
    default_map: str
    suggested_maps: tuple[str, ...]
    description: str
    confirmation: str


PLATFORMS = {
    "macos": PlatformSpec(
        "macos", "macOS", "universal", "ezQuake-macOS-universal.zip",
        "_ezQuake-macOS-universal.zip", "ezQuake.app",
        "ezQuake Stable.app", "ezQuake Nightly.app",
        ".install/ezquake-macos-stable.receipt", ".install/ezquake-macos-nightly.receipt",
    ),
    "linux": PlatformSpec(
        "linux", "Linux x86_64", "x86_64", "ezQuake-linux-x86_64.zip",
        "_ezQuake-x86_64.AppImage", "ezQuake-x86_64.AppImage",
        "ezquake-stable-x86_64.AppImage", "ezquake-nightly-x86_64.AppImage",
        ".install/ezquake-linux-stable.receipt", ".install/ezquake-linux-nightly.receipt",
    ),
    "windows": PlatformSpec(
        "windows", "Windows x64", "x64", "ezQuake-windows-x64.zip",
        "_ezquake.exe", "ezquake.exe",
        "ezquake-stable.exe", "ezquake-nightly.exe",
        ".install/ezquake-windows-stable.receipt", ".install/ezquake-windows-nightly.receipt",
    ),
}


LOCAL_GAMES = (
    LocalGameSpec(
        "ktx", "KTX", "qw", "ktx", "nquake-ktx", "qw/ktx.pk3", "dm6",
        ("dm6", "dm2", "dm4", "aerowalk"),
        "QuakeWorld competitivo com o QVM oficial do KTX.",
        "No console, ktxver deve mostrar a versão carregada.",
    ),
    LocalGameSpec(
        "final-arena", "Final Arena", "arena", "arena", "final-arena", "arena/arena.pk3", "23ar-a",
        ("23ar-a", "arenarg2", "arenarg4", "dm2arena"),
        "Duelos individuais em fila: o vencedor permanece na arena.",
        "No console, gamedir e *gamedir devem mostrar arena.",
    ),
    LocalGameSpec(
        "pro-x", "Pro-X", "prox", "prox", "pro-x", "prox/prox.pk3", "proxmap1",
        ("proxmap1", "proxmap2", "proxmap3", "proxmap4", "proxmap5"),
        "Rounds e equipes com ready, break e votação.",
        "No console, gamedir e *gamedir devem mostrar prox.",
    ),
    LocalGameSpec(
        "team-fortress", "Team Fortress", "fortress", "fortress", "team-fortress", "fortress/misc.pak", "2fort5r",
        ("2fort5r", "well6", "bases", "mbasesr"),
        "Team Fortress clássico para QuakeWorld.",
        "A inicialização deve mostrar Welcome to TeamFortress v2.8.",
    ),
    LocalGameSpec(
        "td2", "Total Destruction 2", "td2", "td2", "total-destruction-2", "td2/qwprogs.dat", "dm6",
        ("dm6", "dm2", "dm4", "e1m2"),
        "TD2 2.22 com armas, magias, runas e poderes.",
        "No serverinfo, *gamedir deve ser td2 e td2qw deve ser 2.22.",
    ),
)

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
        title = self.paint("x86-qw", "1;36")
        print(f"\n{title} · instalador QuakeWorld", flush=True)
        print(f"Ação: {action}  |  Destino: {target}", flush=True)

    def section(self, title: str) -> None:
        print(f"\n{self.paint(title, '1;36')}", flush=True)

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
    def __init__(self, project_root: Path, target: Path, cache_root: Path | None = None):
        self.project_root = project_root.resolve()
        self.target = target
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
        self.app_bundle_version = ""
        self.app_binary_sha256 = ""
        self.app_distribution_path = ""
        self.cache_prefix = ""
        self._public_catalog: dict[str, object] | None = None
        self._component_source_context: ComponentSourceContext | None = None
        try:
            self.component_catalog = load_component_catalog(INSTALLER_ROOT / COMPONENT_CATALOG)
        except ValueError as error:
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

    def remove_macos_app_sandbox(self, app: Path) -> bool:
        if not self.macos_app_is_sandboxed(app):
            return False
        console.info("Ajustando o bundle macOS para acessar diretamente o diretório x86QW...")
        self.run_command(["codesign", "--force", "--deep", "--sign", "-", str(app)])
        self.run_command(["codesign", "--verify", "--deep", "--strict", str(app)])
        if self.macos_app_is_sandboxed(app):
            raise InstallerError(f"Não foi possível remover o sandbox incompatível de {app}.")
        console.success("Bundle macOS preparado sem a limitação de bookmark do sandbox.")
        return True

    def validate_target(self, action: str) -> None:
        target_exists = lexists(self.target)
        if target_exists and self.target.is_symlink():
            raise InstallerError(f"O diretório de destino não pode ser um link simbólico: {self.target}")
        if target_exists and not self.target.is_dir():
            raise InstallerError(f"O destino não é um diretório: {self.target}")
        if not target_exists and action != "install":
            raise InstallerError(f"O diretório de destino não existe: {self.target}")
        self.target = self.target.resolve()
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

    def validate_cache_marker(self) -> None:
        assert self.cache_root is not None
        marker = self.cache_root / CACHE_MARKER_NAME
        if not marker.is_file() or marker.is_symlink():
            raise InstallerError(f"O diretório de cache não pertence a este instalador e foi preservado: {self.cache_root}")
        first_line = marker.read_text(encoding="utf-8").splitlines()[:1]
        if first_line != [CACHE_MARKER_VALUE]:
            raise InstallerError(f"O marcador de propriedade do cache é inválido: {marker}")

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
        if not self.cache_is_present():
            console.info(f"Nenhum cache do instalador foi encontrado em {self.cache_root}.")
            return
        assert self.cache_root is not None
        remove_path(self.cache_root)
        console.success(f"Cache removido: {self.cache_root}")

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

    def provision_install_target(self) -> None:
        bundled = self.project_root / BUNDLED_ID1_DIR
        ensure_no_symlink(bundled, "bundled id1 directory")
        sources = (
            ("pak0.pak", ID1_PAK0_SHA256),
            ("pak1.pak", ID1_PAK1_SHA256),
        )
        for name, expected in sources:
            self.validate_pak_file(bundled / name, expected, "PAK permanente da distribuição")

        ensure_no_symlink(self.target, "installation target")
        if lexists(self.target) and not self.target.is_dir():
            raise InstallerError(f"O destino não é um diretório: {self.target}")
        self.target.mkdir(parents=True, exist_ok=True)
        id1 = self.target / "id1"
        ensure_no_symlink(id1, "id1 directory")
        if lexists(id1) and not id1.is_dir():
            raise InstallerError(f"O caminho id1 não é um diretório: {id1}")
        id1.mkdir(exist_ok=True)

        copied = 0
        for name, expected in sources:
            destination = id1 / name
            if lexists(destination):
                self.validate_pak_file(destination, expected, "PAK existente")
                continue
            temporary = id1 / f".{name}.x86qw-part"
            ensure_no_symlink(temporary, "temporary PAK")
            try:
                shutil.copyfile(bundled / name, temporary)
                if os.name != "nt":
                    temporary.chmod(0o644)
                self.validate_pak_file(temporary, expected, "Cópia temporária do PAK")
                os.replace(temporary, destination)
            finally:
                if lexists(temporary):
                    remove_path(temporary)
            copied += 1
        if copied:
            console.success(f"PAKs registrados preparados em {id1} ({file_count(copied)} copiados).")
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

    def choose_platform(self, product: str = "ezQuake") -> PlatformSpec:
        host = host_platform.system() or "desconhecido"
        machine = host_platform.machine() or "arquitetura desconhecida"
        console.detail(f"Host detectado: {host} {machine}; Python {host_platform.python_version()}")
        print(f"\nPara qual sistema operacional deseja preparar o {product}?")
        print("  1) macOS         - universal arm64 + x86_64 (padrão)")
        print("  2) Linux x86_64  - AppImage")
        print("  3) Windows x64   - executável .exe")
        aliases = {
            "": "macos", "1": "macos", "mac": "macos", "macos": "macos",
            "2": "linux", "linux": "linux", "3": "windows", "windows": "windows", "win": "windows",
        }
        while True:
            try:
                answer = input("Escolha [1/2/3] (padrão: 1): ").strip()
            except EOFError:
                answer = ""
            key = aliases.get(answer.lower())
            if key is not None:
                self.spec = PLATFORMS[key]
                console.success(f"Sistema selecionado: {self.spec.label}")
                return self.spec
            console.warning("Opção inválida. Digite 1, 2 ou 3.")

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

    def http_get(self, url: str, destination: Path | None = None, headers: dict[str, str] | None = None) -> bytes:
        validate_https_url(url, "URL de download")
        request_headers = {"User-Agent": "x86-qw-installer/1", **(headers or {})}
        request = urllib.request.Request(url, headers=request_headers)
        console.detail(f"GET {url}")
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                with HTTPS_OPENER.open(request, timeout=60) as response:
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
                if attempt < 3:
                    console.warning(f"Falha temporária no download. Tentando novamente ({attempt + 1}/3)...")
            except (OSError, urllib.error.URLError) as error:
                last_error = error
                console.detail(f"Tentativa de download falhou: {last_error}")
                if attempt < 3:
                    console.warning(f"Falha temporária no download. Tentando novamente ({attempt + 1}/3)...")
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
        selected = self.prompt_catalog(self.channel, catalog)
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
        console.success(f"Versão selecionada: {self.selected_version}")
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
            console.info(f"Usando arquivo já disponível no cache: {self.app_archive_name}")
            if file_hash(archive, self.app_checksum_kind) != self.app_expected_checksum:
                raise InstallerError(f"O arquivo em cache falhou na verificação: {archive}. Execute cleanup e tente novamente.")
            console.success("Arquivo do cache validado.")
        else:
            local = self.distribution_artifact(
                self.app_distribution_path, self.app_archive_name,
                expected_size=None, expected_sha256=self.app_expected_checksum,
            ) if self.app_distribution_path else None
            if local is not None:
                shutil.copy2(local, archive)
                console.success(f"Artefato carregado da distribuição local: {self.app_distribution_path}")
                self.app_archive_sha256 = file_hash(archive)
                return archive
            download = self.stage / f"{self.app_archive_name}.download"
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
            if self.remove_macos_app_sandbox(source):
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
        if spec.key != "macos" or not self.macos_app_is_sandboxed(runtime):
            return receipt
        self.ensure_macos_ezquake_closed()
        self.remove_macos_app_sandbox(runtime)
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

    def check_runtime_destination_ownership(self) -> None:
        assert self.spec is not None
        runtime = self.target / self.spec.runtime(self.channel)
        receipt_path = self.target / self.spec.receipt(self.channel)
        if lexists(receipt_path) and not receipt_path.is_file():
            raise InstallerError(f"ezQuake receipt is not a regular file: {receipt_path}")
        if lexists(runtime):
            expected_type = runtime.is_dir() if self.spec.key == "macos" else runtime.is_file()
            if not expected_type:
                raise InstallerError(f"invalid managed runtime path: {runtime}")
            if not receipt_path.is_file():
                raise InstallerError(f"refusing to replace an unmanaged {self.spec.label} runtime: {runtime}")
            receipt = self.validate_ezquake_receipt(receipt_path, self.spec, self.channel)
            self.check_runtime(self.spec, self.channel, receipt)
        elif receipt_path.is_file():
            self.validate_ezquake_receipt(receipt_path, self.spec, self.channel)

    def commit_runtime(self, prepared: Path, staged_receipt: Path) -> None:
        assert self.spec is not None and self.stage is not None
        runtime = self.target / self.spec.runtime(self.channel)
        receipt = self.target / self.spec.receipt(self.channel)
        previous_runtime = self.stage / "previous-runtime"
        previous_receipt = self.stage / "previous-receipt"
        moved_runtime = moved_receipt = installed_runtime = installed_receipt = False
        try:
            if lexists(runtime):
                runtime.replace(previous_runtime)
                moved_runtime = True
            if lexists(receipt):
                receipt.replace(previous_receipt)
                moved_receipt = True
            prepared.replace(runtime)
            installed_runtime = True
            shutil.copy2(staged_receipt, receipt)
            installed_receipt = True
        except Exception as error:
            try:
                if installed_receipt and lexists(receipt):
                    remove_path(receipt)
                if installed_runtime and lexists(runtime):
                    remove_path(runtime)
                if moved_runtime:
                    previous_runtime.replace(runtime)
                if moved_receipt:
                    previous_receipt.replace(receipt)
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
        if value not in ("LICENSE", "readme.txt") and path.parts[0] not in (
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
        paths = {
            "maps": (MAPS_RECEIPT, MAPS_INVENTORY),
            "presets": (PRESETS_RECEIPT, PRESETS_INVENTORY),
            "play-support": (PLAY_SUPPORT_RECEIPT, PLAY_SUPPORT_INVENTORY),
        }
        if component in paths:
            return paths[component]
        if component in self.components:
            return f".install/{component}.receipt", f".install/{component}.inventory"
        if component in LEGACY_COMPONENTS:
            return f".install/{component}.receipt", f".install/{component}.inventory"
        raise InstallerError(f"Componente desconhecido: {component}")

    def validate_component_pair(self, component: str, metadata: Path | None = None) -> tuple[bool, list[tuple[str, str]], dict[str, str] | None]:
        receipt_relative, inventory_relative = self.component_metadata(component)
        metadata = metadata or self.target / METADATA_DIR
        receipt_path = metadata / Path(receipt_relative).name
        inventory_path = metadata / Path(inventory_relative).name
        receipt_exists, inventory_exists = lexists(receipt_path), lexists(inventory_path)
        if not receipt_exists and not inventory_exists:
            return False, [], None
        if not receipt_exists or not inventory_exists:
            raise InstallerError(f"Metadados incompletos do componente {component}.")
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
        destination = self.target / METADATA_DIR
        prepared = self.stage / f"{component}-metadata.next"
        previous = self.stage / f"{component}-metadata.previous"
        shutil.copytree(destination, prepared)
        receipt_relative, inventory_relative = self.component_metadata(component)
        for name, source in (
            (Path(receipt_relative).name, receipt),
            (Path(inventory_relative).name, inventory),
        ):
            candidate = prepared / name
            if lexists(candidate):
                remove_path(candidate)
            shutil.copy2(source, candidate)
        self.validate_component_pair(component, prepared)
        moved_previous = installed = False
        try:
            destination.replace(previous)
            moved_previous = True
            prepared.replace(destination)
            installed = True
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
        receipt_relative, inventory_relative = self.component_metadata(component)
        remove_path(self.target / receipt_relative)
        remove_path(self.target / inventory_relative)
        for name in ("qw/maps", "ezquake/configs", "arena", "prox", "fortress", "td2"):
            remove_empty_directories(self.target / name)
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
            try:
                resolved = resolve_dependencies(self.component_catalog, selected)
            except ValueError as error:
                raise InstallerError(str(error)) from error
            added = [identifier for identifier in resolved if identifier not in selected]
            if added:
                console.info("Dependências adicionadas automaticamente: " + ", ".join(added))
            selected = resolved
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

    def public_catalog(self, message: str) -> dict[str, object]:
        if self._public_catalog is None:
            console.info(message)
            catalog_url = os.environ.get("X86_QW_CATALOG_URL")
            local_catalog = self.project_root / PUBLIC_CATALOG
            try:
                if catalog_url:
                    catalog = json.loads(self.http_get(catalog_url))
                    console.detail(f"Catálogo remoto explícito: {catalog_url}")
                elif local_catalog.is_file() and not local_catalog.is_symlink():
                    catalog = json.loads(local_catalog.read_text(encoding="utf-8"))
                    console.detail(f"Catálogo da distribuição local: {local_catalog}")
                else:
                    catalog = json.loads(self.http_get(CATALOG_URL))
                    console.detail(f"Catálogo público: {CATALOG_URL}")
            except (OSError, json.JSONDecodeError, UnicodeDecodeError, TypeError) as error:
                raise InstallerError("O catálogo x86QW recebido é inválido.") from error
            if not isinstance(catalog, dict):
                raise InstallerError("O catálogo x86QW recebido é inválido.")
            if catalog.get("format") != 1 or catalog.get("project") != "x86qw":
                raise InstallerError("O catálogo x86QW usa uma identidade ou formato incompatível.")
            if not isinstance(catalog.get("packages"), list):
                raise InstallerError("A lista de pacotes do catálogo x86QW é inválida.")
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
                console.success(f"Pacote baixado e validado: {filename}")
                return artifact
            except InstallerError as error:
                last_error = error
                if lexists(temporary):
                    remove_path(temporary)
                if index + 1 < len(package["urls"]):
                    console.warning("Mirror indisponível ou inválido; tentando a próxima cópia...")
        raise InstallerError(f"Nenhum mirror entregou o pacote {identifier}: {last_error}")

    def component_source_context(self) -> ComponentSourceContext | None:
        distribution = self.project_root / "dist"
        if not (distribution / "nquake").is_dir():
            return None
        if self._component_source_context is None:
            try:
                self._component_source_context = load_source_context(
                    distribution,
                    self.project_root / COMPONENT_CATALOG,
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

    def release_play_support_profiles(self, selected: list[str]) -> None:
        present, entries, receipt = self.validate_component_pair("play-support")
        if not present:
            return
        released = {
            str(source["destination"])
            for identifier in selected
            for source in self.components[identifier].get("project_sources", [])
            if source.get("mode") == "overlay"
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
            receipt_path, inventory_path = self.component_metadata("play-support")
            remove_path(self.target / receipt_path)
            remove_path(self.target / inventory_path)
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
            console.success(f"{component['label']} atualizado ({file_count(count)}).")
        if "nquake-bootstrap" in selected:
            preset = self.target / "ezquake/configs/preset.cfg"
            if not preset.is_file():
                preset.parent.mkdir(parents=True, exist_ok=True)
                preset.write_text(DEFAULT_PRESET, encoding="utf-8")
        self.ensure_local_play_support(self.available_local_games())

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
            self.ensure_local_play_support(self.available_local_games())
            return
        selected = self.choose_components()
        self.stage = Path(tempfile.mkdtemp(prefix=".quake-install.", dir=self.target))
        self.install_components(selected)

    def install_component_phase(self) -> None:
        assert self.stage is not None
        console.section("Fase 2/2 · Componentes x86QW")
        selected = self.choose_components()
        self.install_components(selected)

    @staticmethod
    def map_name_from_member(member: str) -> str | None:
        normalized = member.replace("\\", "/")
        path = PurePosixPath(normalized)
        if len(path.parts) != 2 or path.parts[0].lower() != "maps" or path.suffix.lower() != ".bsp":
            return None
        name = path.stem
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name):
            return None
        return name

    def maps_from_package(self, package: Path) -> set[str]:
        maps: set[str] = set()
        if package.suffix.lower() == ".pk3":
            try:
                with zipfile.ZipFile(package) as archive:
                    members = archive.namelist()
            except (OSError, zipfile.BadZipFile) as error:
                raise InstallerError(f"Pacote de mapas inválido: {package}") from error
            for member in members:
                if name := self.map_name_from_member(member):
                    maps.add(name)
            return maps
        try:
            size = package.stat().st_size
            with package.open("rb") as archive:
                header = archive.read(12)
                if len(header) != 12 or header[:4] != b"PACK":
                    raise InstallerError(f"PAK de mapas inválido: {package}")
                directory_offset, directory_size = struct.unpack("<II", header[4:])
                if (
                    directory_offset < 12 or directory_size % 64
                    or directory_offset + directory_size > size
                ):
                    raise InstallerError(f"Diretório PAK inválido: {package}")
                archive.seek(directory_offset)
                directory = archive.read(directory_size)
        except OSError as error:
            raise InstallerError(f"Não foi possível ler o PAK de mapas: {package}") from error
        for offset in range(0, len(directory), 64):
            raw_name = directory[offset:offset + 56].split(b"\0", 1)[0]
            try:
                member = raw_name.decode("ascii")
            except UnicodeDecodeError:
                continue
            if name := self.map_name_from_member(member):
                maps.add(name)
        return maps

    def local_map_names(self, gamedir: str) -> list[str]:
        maps: set[str] = set()
        roots = [self.target / "id1"]
        if gamedir != "id1":
            roots.append(self.target / gamedir)
        for root in roots:
            maps_directory = root / "maps"
            if maps_directory.is_dir() and not maps_directory.is_symlink():
                for path in maps_directory.iterdir():
                    if path.is_file() and not path.is_symlink():
                        if name := self.map_name_from_member(f"maps/{path.name}"):
                            maps.add(name)
            if not root.is_dir() or root.is_symlink():
                continue
            for package in root.iterdir():
                if (
                    package.is_file() and not package.is_symlink()
                    and package.suffix.lower() in (".pak", ".pk3")
                ):
                    maps.update(self.maps_from_package(package))
        return sorted(maps, key=str.casefold)

    def available_local_games(self) -> list[LocalGameSpec]:
        available = []
        for game in LOCAL_GAMES:
            component = self.installed_component_for_game(game)
            marker = self.target.joinpath(*PurePosixPath(game.marker).parts)
            if component is not None and marker.is_file() and not marker.is_symlink():
                available.append(game)
        return available

    def installed_component_for_game(self, game: LocalGameSpec) -> str | None:
        present, _, _ = self.validate_component_pair(game.component)
        if present:
            return game.component
        if game.key in {"final-arena", "pro-x"}:
            legacy_present, _, _ = self.validate_component_pair("clan-arena")
            if legacy_present:
                return "clan-arena"
        return None

    def choose_local_game(self, games: list[LocalGameSpec]) -> LocalGameSpec:
        print("\nQual mod deseja jogar localmente?")
        for index, game in enumerate(games, 1):
            default = " (padrão)" if index == 1 else ""
            print(f"  {index}) {game.label}{default} - {game.description}")
        while True:
            try:
                answer = input(f"Escolha [1-{len(games)}] (padrão: 1): ").strip()
            except EOFError as error:
                raise InstallerError("Nenhum mod foi selecionado.") from error
            if not answer:
                return games[0]
            if answer.isdigit() and 1 <= int(answer) <= len(games):
                return games[int(answer) - 1]
            matches = [game for game in games if game.key.casefold() == answer.casefold()]
            if len(matches) == 1:
                return matches[0]
            console.warning(f"Escolha inválida. Use um número entre 1 e {len(games)}.")

    @staticmethod
    def show_map_names(maps: list[str]) -> None:
        print("\nTodos os mapas disponíveis:")
        for offset in range(0, len(maps), 6):
            print("  " + "  ".join(f"{name:<16}" for name in maps[offset:offset + 6]).rstrip())

    def choose_local_map(self, game: LocalGameSpec) -> str:
        maps = self.local_map_names(game.gamedir)
        lookup = {name.casefold(): name for name in maps}
        default = lookup.get(game.default_map.casefold())
        if default is None:
            raise InstallerError(
                f"O mapa padrão {game.default_map} não está disponível para {game.label}. "
                "Execute components para reparar o conteúdo."
            )
        suggestions = [lookup[name.casefold()] for name in game.suggested_maps if name.casefold() in lookup]
        print(f"\nMapas sugeridos para {game.label}:")
        for index, name in enumerate(suggestions, 1):
            suffix = " (padrão)" if name.casefold() == default.casefold() else ""
            print(f"  {index}) {name}{suffix}")
        print(f"  t) mostrar todos os {len(maps)} mapas disponíveis")
        while True:
            try:
                answer = input(f"Escolha o número ou informe o mapa (padrão: {default}): ").strip()
            except EOFError as error:
                raise InstallerError("Nenhum mapa foi selecionado.") from error
            if not answer:
                return default
            if answer.casefold() in ("t", "todos"):
                self.show_map_names(maps)
                continue
            if answer.isdigit() and 1 <= int(answer) <= len(suggestions):
                return suggestions[int(answer) - 1]
            if answer.casefold() in lookup:
                return lookup[answer.casefold()]
            console.warning(f"Mapa não encontrado: {answer}. Digite t para listar os mapas instalados.")

    def play_local(self) -> None:
        self.check_paks()
        games = self.available_local_games()
        if not games:
            raise InstallerError(
                "Nenhum mod local gerenciado está instalado. Execute components e instale ao menos KTX."
            )
        game = self.choose_local_game(games)
        installed_component = self.installed_component_for_game(game)
        if installed_component is None:
            raise InstallerError(f"O componente de {game.label} não está mais instalado.")
        self.migrate_mutable_component_defaults(installed_component)
        self.verify_component(installed_component)
        map_name = self.choose_local_map(game)
        self.ensure_local_play_support(games)
        label, runtime = self.choose_host_runtime()
        arguments = [
            "-game", game.gamedir,
            "+gamedir", game.gamedir,
            "+sv_gamedir", game.gamedir,
        ]
        if game.key != "ktx":
            arguments.extend(["+sv_progtype", "0"])
        arguments.extend(["+map", map_name])
        if game.key in PROFILED_LOCAL_GAMES:
            arguments.extend(["+wait", "+exec", f"x86qw-{game.profile}.cfg"])
        console.info(f"Abrindo {game.label} no mapa {map_name}...")
        self.launch_runtime(runtime, arguments)
        console.success(f"{label} aberto com {game.label}.")
        console.info(game.confirmation)

    def ensure_local_play_support(self, games: list[LocalGameSpec]) -> None:
        profile_sources = {
            game.key: self.game_project_sources(game)
            for game in games if game.key in PROFILED_LOCAL_GAMES
        }
        present, old_entries, _ = self.validate_component_pair("play-support")
        if not games:
            if present:
                removed = self.remove_component("play-support")
                console.detail(f"Suporte a mods locais removido ({file_count(removed)}).")
            return
        old = dict(old_entries) if present else {}
        previous_stage = self.stage
        self.stage = Path(tempfile.mkdtemp(prefix=".quake-play.", dir=self.target))
        try:
            managed = self.stage / "managed"
            prepared = 0
            for game in games:
                sources = profile_sources.get(game.key)
                if sources is not None:
                    component_present, component_entries, _ = self.validate_component_pair(game.component)
                    component_owned = set(dict(component_entries)) if component_present else set()
                    files = {
                        relative: payload
                        for relative, payload in sources.items()
                        if not relative.endswith("-user.cfg") and relative not in component_owned
                    }
                else:
                    files = {}
                if game.key != "ktx":
                    program_name = f"x86qw_{game.gamedir}"
                    files[f"{game.gamedir}/{program_name}.dat"] = self.local_game_program(game)
                for relative, payload in files.items():
                    destination = self.target / relative
                    if lexists(destination):
                        if not destination.is_file() or destination.is_symlink():
                            raise InstallerError(f"Suporte local inválido: {destination}")
                        if old.get(relative) != file_hash(destination):
                            console.warning(f"Arquivo pessoal preservado: {destination}")
                            continue
                    candidate = managed / relative
                    candidate.parent.mkdir(parents=True, exist_ok=True)
                    candidate.write_bytes(payload)
                    prepared += 1
            if prepared:
                count = self.install_component_overlay(
                    "play-support", managed, PLAY_SUPPORT_VERSION, "x86QW local-play layer",
                )
                console.detail(f"Suporte a mods locais preparado ({file_count(count)}).")
            elif present:
                removed = self.remove_component("play-support")
                console.detail(f"Suporte local antigo removido ({file_count(removed)}).")
            for game in games:
                sources = profile_sources.get(game.key)
                if sources is not None:
                    self.ensure_game_user_profile(
                        game,
                        sources[f"{game.gamedir}/x86qw-{game.profile}-user.cfg"],
                    )
        finally:
            self.cleanup_stage()
            self.stage = previous_stage

    def ensure_game_user_profile(self, game: LocalGameSpec, initial: bytes) -> None:
        destination = self.target / game.gamedir / f"x86qw-{game.profile}-user.cfg"
        if lexists(destination):
            if not destination.is_file() or destination.is_symlink():
                raise InstallerError(f"Configuração pessoal de {game.label} inválida: {destination}")
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(initial)
        if os.name != "nt":
            destination.chmod(0o644)
        console.info(f"Configuração pessoal de {game.label} criada: {destination}")

    def game_project_sources(self, game: LocalGameSpec) -> dict[str, bytes]:
        stem = f"x86qw-{game.profile}"
        expected = {
            f"{game.gamedir}/{stem}.cfg": "overlay",
            f"{game.gamedir}/{stem}-user.cfg": "default",
        }
        if game.key != "ktx":
            expected[f"{game.gamedir}/server.cfg"] = "overlay"
        if game.key == "pro-x":
            expected[f"{game.gamedir}/qw_server.cfg"] = "overlay"
        entries = [
            entry for entry in self.components[game.component].get("project_sources", [])
            if str(entry.get("destination", "")).startswith(f"{game.gamedir}/")
        ]
        actual = {entry["destination"]: entry["mode"] for entry in entries}
        if actual != expected:
            raise InstallerError(f"A camada de {game.label} no repositório diverge do contrato x86QW.")
        sources: dict[str, bytes] = {}
        for entry in entries:
            source = self.project_root.joinpath(*PurePosixPath(entry["path"]).parts)
            if not source.is_file() or source.is_symlink():
                raise InstallerError(f"Arquivo-fonte de {game.label} não encontrado no repositório: {source}")
            try:
                payload = source.read_bytes()
            except OSError as error:
                raise InstallerError(f"Não foi possível ler a camada de {game.label}: {source}") from error
            if not payload:
                raise InstallerError(f"Arquivo-fonte de {game.label} vazio: {source}")
            sources[entry["destination"]] = payload
        return sources

    def local_game_program(self, game: LocalGameSpec) -> bytes:
        package = self.target / game.marker
        suffix = package.suffix.casefold()
        if suffix == ".dat":
            return package.read_bytes()
        if suffix == ".pk3":
            try:
                with zipfile.ZipFile(package) as archive:
                    return archive.read("qwprogs.dat")
            except (KeyError, OSError, zipfile.BadZipFile) as error:
                raise InstallerError(f"Gamecode qwprogs.dat não encontrado em {package}.") from error
        if suffix == ".pak":
            return self.pak_member(package, "qwprogs.dat")
        raise InstallerError(f"Formato de gamecode local não suportado: {package}")

    def pak_member(self, package: Path, member_name: str) -> bytes:
        try:
            size = package.stat().st_size
            with package.open("rb") as archive:
                header = archive.read(12)
                if len(header) != 12 or header[:4] != b"PACK":
                    raise InstallerError(f"PAK inválido: {package}")
                directory_offset, directory_size = struct.unpack("<II", header[4:])
                if directory_offset < 12 or directory_size % 64 or directory_offset + directory_size > size:
                    raise InstallerError(f"Diretório PAK inválido: {package}")
                archive.seek(directory_offset)
                directory = archive.read(directory_size)
                for offset in range(0, len(directory), 64):
                    raw_name = directory[offset:offset + 56].split(b"\0", 1)[0]
                    try:
                        name = raw_name.decode("ascii")
                    except UnicodeDecodeError:
                        continue
                    if name.casefold() != member_name.casefold():
                        continue
                    data_offset, data_size = struct.unpack_from("<II", directory, offset + 56)
                    if data_offset < 12 or data_offset + data_size > size:
                        raise InstallerError(f"Membro PAK inválido em {package}: {name}")
                    archive.seek(data_offset)
                    payload = archive.read(data_size)
                    if len(payload) != data_size:
                        raise InstallerError(f"Membro PAK truncado em {package}: {name}")
                    return payload
        except OSError as error:
            raise InstallerError(f"Não foi possível ler o PAK: {package}") from error
        raise InstallerError(f"Gamecode {member_name} não encontrado em {package}.")

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
        platform_key = {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}.get(host_platform.system())
        if platform_key is None:
            raise InstallerError(f"A abertura automática não é suportada neste sistema: {host_platform.system()}.")
        choices: list[tuple[str, Path]] = []
        spec = PLATFORMS[platform_key]
        for channel in ("stable", "nightly"):
            receipt_path = self.target / spec.receipt(channel)
            if not receipt_path.is_file():
                continue
            receipt = self.validate_ezquake_receipt(receipt_path, spec, channel)
            receipt = self.repair_installed_macos_runtime(spec, channel, receipt_path, receipt)
            self.check_runtime(spec, channel, receipt)
            choices.append((f"ezQuake {channel} {receipt['selection']}", self.target / spec.runtime(channel)))
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
        base_arguments = ["-basedir", str(self.target)]
        if system == "Darwin":
            command = ["open", "-n", str(runtime), "--args", *base_arguments, *quake_arguments]
        else:
            command = [str(runtime), *base_arguments, *quake_arguments]
        console.detail("$ " + " ".join(command))
        try:
            subprocess.Popen(command, cwd=self.target, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
                receipt_path = self.target / spec.receipt(channel)
                if not receipt_path.is_file():
                    continue
                receipt = self.validate_ezquake_receipt(receipt_path, spec, channel)
                runtime = self.target / spec.runtime(channel)
                if not lexists(runtime):
                    raise InstallerError(f"missing ezQuake runtime: {runtime}")
                self.check_runtime(spec, channel, receipt)
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
        if not installed:
            console.info("Nenhum componente x86QW está instalado.")
        for identifier in installed:
            self.verify_component(identifier)
        if lexists(self.target / "id1/gpl_maps.pk3"):
            raise InstallerError("shareware gpl_maps.pk3 must not be installed with registered PAKs")
        self.verify_component("maps")
        self.verify_component("presets")
        self.verify_component("play-support")
        self.report_nquake_startup_state(installed)

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

    def preflight_ezquake_receipts(self) -> None:
        for spec in PLATFORMS.values():
            for channel in ("stable", "nightly"):
                receipt_path = self.target / spec.receipt(channel)
                if not lexists(receipt_path):
                    continue
                self.validate_ezquake_receipt(receipt_path, spec, channel)

    def preflight_component_receipts(self) -> None:
        for component in (*self.components, *LEGACY_COMPONENTS, "maps", "presets", "play-support"):
            self.validate_component_pair(component)

    def uninstall(self) -> None:
        metadata_names = [
            NQUAKE_INVENTORY, NQUAKE_RECEIPT,
            MAPS_RECEIPT, MAPS_INVENTORY, PRESETS_RECEIPT, PRESETS_INVENTORY,
            PLAY_SUPPORT_RECEIPT, PLAY_SUPPORT_INVENTORY,
        ]
        for component in self.components:
            metadata_names.extend(self.component_metadata(component))
        for component in LEGACY_COMPONENTS:
            metadata_names.extend(self.component_metadata(component))
        for spec in PLATFORMS.values():
            metadata_names.extend((spec.stable_receipt, spec.nightly_receipt))
        if not any(lexists(self.target / name) for name in metadata_names):
            console.info(f"Nenhum runtime gerenciado está instalado em {self.target}.")
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
                receipt_path = self.target / spec.receipt(channel)
                if not receipt_path.is_file():
                    continue
                self.validate_ezquake_receipt(receipt_path, spec, channel)
                remove_path(self.target / spec.runtime(channel))
                remove_path(receipt_path)
        for component in (*reversed(tuple(self.components)), *LEGACY_COMPONENTS, "maps", "presets", "play-support"):
            self.remove_component(component)
        remove_path(self.target / NQUAKE_RECEIPT)
        remove_path(self.target / NQUAKE_INVENTORY)
        for name in ("arena", "prox", "fortress", "qw", "ezquake"):
            remove_empty_directories(self.target / name)
        remove_empty_directories(self.target / METADATA_DIR)
        for relative, expected in preserved.items():
            if file_hash(self.target / relative) != expected:
                raise InstallerError(f"{relative} changed during uninstall")
        console.success(f"Componentes gerenciados removidos de {self.target}.")
        console.info("PAKs registrados e arquivos pessoais foram preservados.")

    def purge(self) -> None:
        id1 = self.target / "id1"
        if not id1.is_dir() or id1.is_symlink():
            raise InstallerError(f"O purge exige um diretório id1 real: {id1}")
        cache_present = self.cache_is_present()
        root_device = self.target.stat().st_dev
        for child in self.target.iterdir():
            if child.name != "id1":
                remove_path(child, root_device)
        unexpected = [child for child in self.target.iterdir() if child.name != "id1"]
        if unexpected:
            raise InstallerError(f"purge left an unexpected path: {unexpected[0]}")
        if cache_present:
            assert self.cache_root is not None
            remove_path(self.cache_root)
            console.success(f"Cache removido: {self.cache_root}")
        else:
            console.info(f"Nenhum cache do instalador foi encontrado em {self.cache_root}.")
        console.success(f"Instalação removida de {self.target}.")
        console.info("Somente o diretório id1 foi preservado.")

    def install(self) -> None:
        console.section("Fase 1/2 · ezQuake")
        self.choose_platform()
        self.choose_channel()
        self.ensure_macos_ezquake_closed()
        self.check_runtime_destination_ownership()
        self.choose_release()
        self.provision_install_target()
        self.reject_target_symlinks()
        self.stage = Path(tempfile.mkdtemp(prefix=".quake-install.", dir=self.target))
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

    def cleanup_stage(self) -> None:
        if self.stage is not None and self.stage.is_dir():
            remove_path(self.stage)


class FriendlyArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: erro: {message}\n")


def parse_arguments(arguments: list[str], project_root: Path) -> argparse.Namespace:
    parser = FriendlyArgumentParser(
        prog="install-qw.py",
        description="Instala e mantém uma coleção QuakeWorld moderna em um diretório autocontido.",
        epilog="Exemplo: ./install-qw.py install ./quake-world",
        add_help=False,
    )
    parser._positionals.title = "argumentos"
    parser._optionals.title = "opções"
    parser.add_argument("-h", "--help", action="help", help="mostra esta ajuda e encerra")
    parser.add_argument("-v", "--verbose", action="store_true", help="mostra URLs, comandos, hashes e caminhos técnicos")
    parser.add_argument("--no-color", action="store_true", help="desativa cores mesmo em um terminal interativo")
    parser.add_argument(
        "action", nargs="?", default="install",
        help="install, components, presets, play, hub, verify, uninstall, purge ou cleanup",
    )
    parser.add_argument("target", nargs="?", type=Path, help="diretório de instalação (padrão: ./quake-world)")
    namespace = parser.parse_args(arguments)
    valid_actions = ("install", "components", "presets", "play", "hub", "verify", "uninstall", "purge", "cleanup")
    if namespace.action not in valid_actions:
        parser.error(f"ação desconhecida: {namespace.action}. Use {', '.join(valid_actions)}")
    if namespace.action == "cleanup" and namespace.target is not None:
        parser.error("cleanup não aceita um diretório de destino")
    namespace.target = namespace.target or project_root / "quake-world"
    return namespace


def main(arguments: list[str] | None = None) -> int:
    project_root = Path(__file__).resolve().parent
    options = None
    try:
        options = parse_arguments(sys.argv[1:] if arguments is None else arguments, project_root)
        console.configure(verbose=options.verbose, no_color=options.no_color)
        action_labels = {
            "install": "instalar ezQuake + componentes x86QW", "components": "gerenciar componentes x86QW",
            "presets": "gerenciar presets",
            "play": "jogar um mod local", "hub": "navegar servidores", "verify": "verificar", "uninstall": "desinstalar",
            "purge": "remover tudo", "cleanup": "limpar cache",
        }
        console.banner(action_labels[options.action], options.target)
        installer = Installer(project_root, options.target)
        if options.action == "cleanup":
            console.section("Limpeza do cache")
            installer.cleanup_cache()
            return 0
        installer.validate_target(options.action)
        console.detail(f"Destino normalizado: {installer.target}")
        if options.action == "purge":
            console.section("Remoção completa")
            installer.purge()
            return 0
        installer.reject_target_symlinks()
        if options.action == "verify":
            console.section("Verificação da instalação")
            installer.verify_installation()
            console.success("Verificação concluída sem problemas.")
        elif options.action == "uninstall":
            console.section("Desinstalação")
            installer.uninstall()
        elif options.action == "hub":
            console.section("QuakeWorld Hub")
            installer.browse_hub()
        elif options.action == "play":
            console.section("Jogo local")
            installer.play_local()
        else:
            try:
                if options.action == "components":
                    installer.manage_components()
                elif options.action == "presets":
                    installer.manage_presets()
                else:
                    installer.install()
            finally:
                installer.cleanup_stage()
        return 0
    except KeyboardInterrupt:
        console.error("Operação cancelada. Nenhuma seleção pendente foi aplicada.")
        return 130
    except InstallerError as error:
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
