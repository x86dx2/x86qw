#!/usr/bin/env python3
"""Launch the dedicated MVDSV, QWFWD and QTV components installed by x86QW."""

from __future__ import annotations

import argparse
import ctypes
import errno
import getpass
import hashlib
import importlib
import ipaddress
import json
import os
import secrets
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

sys.dont_write_bytecode = True
POPEN_TYPE = subprocess.Popen

core = importlib.import_module("manager")
gameplay = importlib.import_module("gameplay")
session_control = importlib.import_module("session_control")

InstallerError = core.InstallerError
console = core.console
lexists = core.lexists
remove_path = core.remove_path
ProcessIdentity = session_control.ProcessIdentity
ProcessProbe = session_control.ProcessProbe
new_session_id = session_control.new_session_id
process_identity = session_control.process_identity
probe_expected_process = session_control.probe_expected_process


class SessionLock(session_control.InstallationLock):
    """Compatibility facade over the shared installation operation lock."""

    @classmethod
    def acquire(cls, target: Path, command: str) -> SessionLock:
        try:
            return super().acquire(target, command, "service")
        except session_control.SessionControlError as error:
            raise InstallerError(str(error)) from error

MAX_ARCHIVE_MEMBERS = 4096
MAX_ARCHIVE_MEMBER_SIZE = 128 * 1024 * 1024
MAX_ARCHIVE_TOTAL_SIZE = 512 * 1024 * 1024
MAX_ARCHIVE_DEPTH = 16
MAX_ARCHIVE_PATH_LENGTH = 240
MAX_ARCHIVE_COMPRESSION_RATIO = 500
WINDOWS_RESERVED_NAMES = frozenset({
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
})
DEDICATED_MODE_CVARS: dict[str, tuple[tuple[str, str], ...]] = {
    "midair": (("deathmatch", "4"), ("k_midair", "1")),
    "dmm4": (("deathmatch", "4"),),
    "instagib": (("deathmatch", "4"), ("k_instagib", "1")),
    "lgc": (("deathmatch", "4"), ("k_lgcmode", "1")),
    "rocket-arena": (("k_rocketarena", "1"),),
    "race": (
        ("k_race", "1"), ("srv_practice_mode", "1"),
        ("lock_practice", "1"), ("allow_toggle_practice", "0"),
        ("qtv_sayenabled", "1"),
    ),
    "practice": (("srv_practice_mode", "1"),),
}


@dataclass(frozen=True)
class ProcessSpec:
    label: str
    arguments: tuple[str, ...]
    cwd: Path
    startup_rcon: StartupRcon | None = None
    readiness: ServiceReadiness | None = None


@dataclass(frozen=True)
class StartupRcon:
    address: str
    port: int
    password: str
    config_name: str
    expected_map: str
    expected_gamedir: str


@dataclass(frozen=True)
class ServiceReadiness:
    kind: str
    address: str
    port: int
    upstream: str | None = None


@dataclass(frozen=True)
class MaterializedFile:
    path: Path
    expected_hash: str
    origin: str
    created_by_session: bool
    existed: bool


@dataclass(frozen=True)
class MaterializedKtx:
    files: tuple[MaterializedFile, ...]
    directories: tuple[Path, ...]


@dataclass(frozen=True)
class HostedGame:
    game: gameplay.LocalGameSpec
    mode: gameplay.KtxModeSpec | None
    map_name: str
    assets: frozenset[str]
    ktx_options: gameplay.KtxLaunchOptions


@dataclass
class ServiceResources:
    temporary_paths: list[Path]
    materialized_ktx: list[MaterializedKtx]
    installer: core.Installer | None = None
    journal: SessionJournal | None = None
    session_lock: SessionLock | None = None
    recovery_confirmed: bool = False


def bounded_integer(minimum: int, maximum: int):
    def parse(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as error:
            raise argparse.ArgumentTypeError("deve ser um número inteiro") from error
        if not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(f"deve estar entre {minimum} e {maximum}")
        return parsed
    return parse


def bind_address(value: str) -> str:
    try:
        return str(ipaddress.ip_address(value))
    except ValueError as error:
        raise argparse.ArgumentTypeError("use um endereço IPv4 ou IPv6 literal") from error


def safe_text(value: str, label: str, maximum: int = 96) -> str:
    if (
        not value
        or len(value) > maximum
        or any(character in value for character in '\\";\r\n')
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise InstallerError(f"{label} contém caracteres inválidos ou excede {maximum} caracteres.")
    return value


def parse_network_endpoint(value: str) -> str:
    """Validate and normalize host:port or [IPv6]:port without accepting commands."""
    if (
        not value
        or len(value) > 253
        or any(character.isspace() or ord(character) < 32 for character in value)
        or any(character in value for character in '\\";')
    ):
        raise argparse.ArgumentTypeError("use host:porta ou [IPv6]:porta")
    if value.startswith("["):
        closing = value.find("]")
        if closing < 0 or closing + 1 >= len(value) or value[closing + 1] != ":":
            raise argparse.ArgumentTypeError("use [IPv6]:porta")
        host = value[1:closing]
        port_text = value[closing + 2:]
        try:
            address = ipaddress.IPv6Address(host)
        except ValueError as error:
            raise argparse.ArgumentTypeError("endereço IPv6 inválido") from error
        normalized_host = f"[{address}]"
    else:
        if value.count(":") != 1:
            raise argparse.ArgumentTypeError("IPv6 deve usar [IPv6]:porta")
        host, port_text = value.rsplit(":", 1)
        if not host:
            raise argparse.ArgumentTypeError("host ausente")
        try:
            normalized_host = str(ipaddress.IPv4Address(host))
        except ValueError:
            if len(host) > 253 or host.endswith("."):
                raise argparse.ArgumentTypeError("hostname inválido")
            labels = host.split(".")
            if any(
                not label
                or len(label) > 63
                or label.startswith("-")
                or label.endswith("-")
                or any(not (character.isascii() and (character.isalnum() or character == "-")) for character in label)
                for label in labels
            ):
                raise argparse.ArgumentTypeError("hostname inválido")
            normalized_host = host.casefold()
    try:
        port = int(port_text)
    except ValueError as error:
        raise argparse.ArgumentTypeError("porta inválida") from error
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("porta deve estar entre 1 e 65535")
    return f"{normalized_host}:{port}"


def endpoint_parts(value: str) -> tuple[str, int]:
    normalized = parse_network_endpoint(value)
    if normalized.startswith("["):
        closing = normalized.index("]")
        return normalized[1:closing], int(normalized[closing + 2:])
    host, port = normalized.rsplit(":", 1)
    return host, int(port)


def read_password_file(path: Path, label: str) -> str:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise InstallerError(f"Não foi possível ler o arquivo de {label}.") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise InstallerError(f"O arquivo de {label} precisa ser regular e não pode ser symlink.")
    if os.name != "nt" and metadata.st_mode & 0o077:
        raise InstallerError(f"Permissões inseguras no arquivo de {label}; use chmod 600.")
    if metadata.st_size > 4096:
        raise InstallerError(f"O arquivo de {label} excede o limite de 4096 bytes.")
    try:
        value = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise InstallerError(f"Não foi possível ler o arquivo de {label}.") from error
    if value.endswith("\r\n"):
        value = value[:-2]
    elif value.endswith("\n"):
        value = value[:-1]
    if "\n" in value or "\r" in value:
        raise InstallerError(f"O arquivo de {label} deve conter uma única linha.")
    if value:
        safe_text(value, label, 4096)
    return value


def resolve_passwords(options: argparse.Namespace) -> None:
    fields = (
        ("password", "prompt_password", "password_file", "senha de jogador"),
        ("spectator_password", "prompt_spectator_password", "spectator_password_file", "senha de espectador"),
        ("rcon_password", "prompt_rcon_password", "rcon_password_file", "senha RCON"),
        ("qtv_password", "prompt_qtv_password", "qtv_password_file", "senha QTV"),
    )
    for value_name, prompt_name, file_name, label in fields:
        if not hasattr(options, value_name):
            continue
        value = getattr(options, value_name, "")
        password_file = getattr(options, file_name, None)
        if password_file is not None:
            value = read_password_file(password_file, label)
        elif getattr(options, prompt_name, False):
            value = getpass.getpass(f"{label.capitalize()}: ")
            if value:
                safe_text(value, label, 4096)
        setattr(options, value_name, value)


def add_password_source(
    parser: argparse.ArgumentParser,
    *,
    destination: str,
    legacy_flag: str | tuple[str, ...],
    prompt_flag: str,
    file_flag: str,
    help_label: str,
) -> None:
    group = parser.add_mutually_exclusive_group()
    legacy_flags = (legacy_flag,) if isinstance(legacy_flag, str) else legacy_flag
    group.add_argument(
        *legacy_flags, dest=destination, default="", metavar="SENHA",
        help=f"{help_label} (legado; pode aparecer no histórico do shell)",
    )
    group.add_argument(prompt_flag, dest=f"prompt_{destination}", action="store_true", help=f"solicita {help_label} sem eco")
    group.add_argument(file_flag, dest=f"{destination}_file", type=Path, metavar="ARQUIVO", help=f"lê {help_label} de arquivo privado")


def q(value: str) -> str:
    return f'"{value}"'


def endpoint(address: str, port: int) -> str:
    return f"[{address}]:{port}" if ":" in address else f"{address}:{port}"


def local_service_address(address: str) -> str:
    if address == "0.0.0.0":
        return "127.0.0.1"
    if address == "::":
        return "::1"
    return address


def host_qtv_upstream(address: str, port: int) -> str:
    return endpoint(local_service_address(address), port)


def is_external_bind(address: str) -> bool:
    parsed = ipaddress.ip_address(address)
    return parsed.is_unspecified or not parsed.is_loopback


def warn_external_bind(options: argparse.Namespace) -> None:
    def warn_qtv(address: str, password: str, has_upstream: bool) -> None:
        if is_external_bind(address):
            console.warning(
                "A interface HTTP/QTV será exposta à rede. "
                "A senha de upstream não autentica o acesso HTTP."
            )
        if has_upstream and not password:
            console.warning("Upstream QTV sem segredo configurado.")

    if options.action == "host":
        if is_external_bind(options.bind) and not any((
            options.password, options.spectator_password, options.rcon_password,
        )):
            console.warning(
                "Bind externo sem senhas de jogador, espectador ou RCON; "
                "o servidor ficará acessível pela rede."
            )
        if options.with_qtv:
            warn_qtv(options.qtv_bind, options.qtv_password, True)
        if options.with_proxy and is_external_bind(options.proxy_bind):
            console.warning("QWFWD está ligado a uma interface externa.")
    elif options.action == "qtv":
        warn_qtv(options.bind, options.qtv_password, options.upstream is not None)
    elif options.action == "proxy" and is_external_bind(options.proxy_bind):
        console.warning("QWFWD está ligado a uma interface externa.")


def ensure_private_directory(path: Path) -> None:
    if lexists(path):
        if path.is_symlink() or not path.is_dir():
            raise InstallerError(f"Diretório de serviço ausente ou inseguro: {path}")
    else:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            if path.is_symlink() or not path.is_dir():
                raise InstallerError(f"Diretório de serviço ausente ou inseguro: {path}")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class SessionJournal:
    """Private, append-safe description of ephemeral service session state."""

    def __init__(
        self,
        target: Path,
        *,
        session_id: str | None = None,
        controller: dict[str, object] | None = None,
    ) -> None:
        self.target = target.resolve()
        sessions = self.target / ".install" / "sessions"
        ensure_private_directory(sessions.parent)
        ensure_private_directory(sessions)
        if os.name != "nt":
            sessions.parent.chmod(0o700)
            sessions.chmod(0o700)
        self.session_id = session_id or new_session_id()
        self.directory = sessions / self.session_id
        self.directory.mkdir(mode=0o700)
        self.path = self.directory / "session.json"
        self.data: dict[str, object] = {
            "format": 1,
            "project": "x86qw",
            "session_id": self.session_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "starting",
            "controller": None if controller is None else {
                "pid": controller["controller_pid"],
                "creation_token": controller["controller_start_token"],
                "executable": controller["controller_executable"],
                "command": controller["command"],
            },
            "processes": [],
            "temporary_files": [],
            "materialized_files": [],
            "created_directories": [],
        }
        self._write()

    def _relative(self, path: Path) -> str:
        try:
            absolute = path if path.is_absolute() else self.target / path
            canonical_parent = absolute.parent.resolve(strict=False)
            return (canonical_parent / absolute.name).relative_to(self.target).as_posix()
        except ValueError as error:
            raise InstallerError("O journal recusou um caminho fora da instalação.") from error

    def _write(self) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".session-", suffix=".json", dir=self.directory,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
                json.dump(self.data, output, ensure_ascii=False, indent=2, sort_keys=True)
                output.write("\n")
            if os.name != "nt":
                temporary.chmod(0o600)
            temporary.replace(self.path)
            if os.name != "nt":
                self.path.chmod(0o600)
        finally:
            if lexists(temporary):
                remove_path(temporary)

    def set_status(self, status: str) -> None:
        if status not in {"starting", "running", "stopping", "interrupted", "clean"}:
            raise InstallerError(f"Estado inválido do journal: {status}")
        self.data["status"] = status
        self._write()

    def record_process(
        self,
        spec: ProcessSpec,
        process: subprocess.Popen[bytes],
        process_group: int,
    ) -> None:
        probe = process_identity(process.pid)
        if probe.status != "alive" or probe.identity is None:
            raise InstallerError(f"Não foi possível registrar a identidade do processo {spec.label}.")
        identity = probe.identity
        address = None
        port = None
        if spec.startup_rcon is not None:
            address, port = spec.startup_rcon.address, spec.startup_rcon.port
        elif spec.readiness is not None:
            address, port = spec.readiness.address, spec.readiness.port
        processes = self.data["processes"]
        assert isinstance(processes, list)
        processes.append({
            "label": spec.label,
            "runtime": spec.label.casefold(),
            "pid": process.pid,
            "process_group": process_group,
            "executable": identity.executable,
            "creation_token": identity.creation_token,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "address": address,
            "port": port,
        })
        self._write()

    def record_temporary(self, path: Path, origin: str, *, sensitive: bool) -> None:
        entries = self.data["temporary_files"]
        assert isinstance(entries, list)
        entry: dict[str, object] = {
            "path": self._relative(path),
            "origin": origin,
            "created_by_session": True,
            "type": "temporary-config",
            "sensitive": sensitive,
        }
        if not sensitive:
            entry["expected_hash"] = file_sha256(path)
        entries.append(entry)
        self._write()

    def record_materialized(self, entry: MaterializedFile) -> None:
        entries = self.data["materialized_files"]
        assert isinstance(entries, list)
        entries.append({
            "path": self._relative(entry.path),
            "expected_hash": entry.expected_hash,
            "origin": entry.origin,
            "created_by_session": entry.created_by_session,
            "existed": entry.existed,
            "modified_during_session": False,
            "type": "materialized-content",
            "sensitive": False,
        })
        self._write()

    def record_directory(self, path: Path) -> None:
        directories = self.data["created_directories"]
        assert isinstance(directories, list)
        relative = self._relative(path)
        if relative not in directories:
            directories.append(relative)
            self._write()

    def mark_modified(self, relative: str) -> None:
        entries = self.data["materialized_files"]
        assert isinstance(entries, list)
        for entry in entries:
            if isinstance(entry, dict) and entry.get("path") == relative:
                entry["modified_during_session"] = True
        self._write()


def journal_path(target: Path, relative: object) -> Path | None:
    if not isinstance(relative, str):
        return None
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        return None
    candidate = target.joinpath(*pure.parts)
    try:
        candidate.resolve(strict=False).relative_to(target.resolve())
    except ValueError:
        return None
    return candidate


def load_session_journal(path: Path) -> dict[str, object]:
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError("journal inseguro")
        data = json.loads(path.read_text(encoding="utf-8"))
        required = {
            "format", "project", "session_id", "created_at", "status", "controller",
            "processes", "temporary_files", "materialized_files", "created_directories",
        }
        optional = {"recovery_actions", "recovered_at"}
        if (
            not isinstance(data, dict)
            or set(data) < required
            or set(data) - required - optional
            or data.get("format") != 1
            or data.get("project") != "x86qw"
            or not isinstance(data.get("session_id"), str)
            or not data.get("session_id")
            or not isinstance(data.get("created_at"), str)
            or data.get("status") not in {"starting", "running", "stopping", "interrupted", "clean"}
            or not all(isinstance(data.get(field), list) for field in (
                "processes", "temporary_files", "materialized_files", "created_directories",
            ))
        ):
            raise ValueError("identidade inválida")
        controller = data.get("controller")
        if controller is not None and (
            not isinstance(controller, dict)
            or set(controller) != {"pid", "creation_token", "executable", "command"}
            or not isinstance(controller.get("pid"), int)
            or not all(
                isinstance(controller.get(field), str) and controller.get(field)
                for field in ("creation_token", "executable", "command")
            )
        ):
            raise ValueError("controlador inválido")
        for entry in data["processes"]:
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("pid"), int)
                or not isinstance(entry.get("label"), str)
            ):
                raise ValueError("processo inválido")
        for collection in ("temporary_files", "materialized_files"):
            for entry in data[collection]:
                if (
                    not isinstance(entry, dict)
                    or not isinstance(entry.get("path"), str)
                    or not isinstance(entry.get("created_by_session"), bool)
                    or not isinstance(entry.get("sensitive"), bool)
                ):
                    raise ValueError("arquivo inválido")
        if not all(isinstance(relative, str) for relative in data["created_directories"]):
            raise ValueError("diretório inválido")
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise InstallerError(
            f"Journal de sessão inválido preservado para inspeção: {path.parent}"
        ) from error
    return data


def journal_controller_probe(data: dict[str, object]) -> ProcessProbe | None:
    controller = data.get("controller")
    if controller is None:
        return None
    assert isinstance(controller, dict)
    return probe_expected_process(
        int(controller["pid"]),
        str(controller["creation_token"]),
        str(controller["executable"]),
    )


def journal_process_probe(entry: object) -> ProcessProbe:
    if not isinstance(entry, dict) or not isinstance(entry.get("pid"), int):
        return ProcessProbe("inconclusive", detail="registro de processo incompleto")
    pid = int(entry["pid"])
    token = entry.get("creation_token")
    executable = entry.get("executable")
    if not isinstance(token, str) or not token or not isinstance(executable, str) or not executable:
        probe = process_identity(pid)
        if probe.status == "dead":
            return probe
        return ProcessProbe(
            "inconclusive", probe.identity,
            "journal legado não possui token de criação e executável",
        )
    return probe_expected_process(pid, token, executable)


def session_journal_paths(target: Path, session_id: str | None = None) -> list[Path]:
    sessions = target / ".install" / "sessions"
    if not lexists(sessions):
        return []
    if sessions.is_symlink() or not sessions.is_dir():
        raise InstallerError(f"Diretório de sessões ausente ou inseguro: {sessions}")
    directories = [sessions / session_id] if session_id is not None else sorted(sessions.iterdir())
    return [
        directory / "session.json"
        for directory in directories
        if directory.is_dir() and not directory.is_symlink()
    ]


def assert_recovery_processes_confirmable(target: Path, session_id: str | None = None) -> None:
    for path in session_journal_paths(target, session_id):
        data = load_session_journal(path)
        if data.get("status") == "clean":
            continue
        processes = data.get("processes", [])
        assert isinstance(processes, list)
        controller_probe = journal_controller_probe(data)
        if controller_probe is not None and controller_probe.status == "alive":
            controller = data["controller"]
            assert isinstance(controller, dict)
            raise InstallerError(
                "O controlador registrado no journal continua ativo; recuperação bloqueada.\n"
                f"Sessão: {data['session_id']}\nControlador: PID {controller['pid']}"
            )
        if controller_probe is not None and controller_probe.status == "inconclusive":
            raise InstallerError(
                "Não foi possível confirmar o controlador do journal; lock, processos e arquivos "
                f"foram preservados em {path.parent}."
            )
        for entry in processes:
            probe = journal_process_probe(entry)
            if probe.status == "inconclusive":
                label = entry.get("label", "processo") if isinstance(entry, dict) else "processo"
                raise InstallerError(
                    f"Não foi possível confirmar a identidade de {label} na sessão abandonada; "
                    f"lock, journal e arquivos foram preservados em {path.parent}."
                )
            if controller_probe is None and probe.status == "alive":
                raise InstallerError(
                    "Journal legado possui processo vivo e não prova que seu controlador terminou; "
                    f"processos e arquivos foram preservados em {path.parent}."
                )


def process_still_matches(entry: dict[str, object]) -> bool:
    probe = journal_process_probe(entry)
    if probe.status == "alive":
        return True
    if probe.status != "dead" or os.name == "nt":
        return False
    process_group = entry.get("process_group")
    return (
        isinstance(process_group, int)
        and process_group == entry.get("pid")
        and process_group > 1
        and posix_process_group_status(process_group) == "alive"
    )


def signal_recorded_process(entry: dict[str, object], *, force: bool) -> None:
    pid = int(entry["pid"])
    if os.name == "nt":
        session_control.terminate_windows_process(pid, 1 if force else 0)
        return
    process_group = entry.get("process_group")
    selected_signal = signal.SIGKILL if force else signal.SIGTERM
    if isinstance(process_group, int) and process_group == pid and process_group > 1:
        os.killpg(process_group, selected_signal)
    else:
        os.kill(pid, selected_signal)


def terminate_recorded_process(entry: dict[str, object], timeout: float = 4.0) -> str:
    probe = journal_process_probe(entry)
    if probe.status == "dead":
        process_group = entry.get("process_group")
        if (
            os.name == "nt"
            or not isinstance(process_group, int)
            or process_group != entry.get("pid")
            or process_group <= 1
        ):
            return "already_dead"
        group_status = posix_process_group_status(process_group)
        if group_status == "dead":
            return "already_dead"
        if group_status == "inconclusive":
            raise InstallerError(
                f"O líder PID {entry['pid']} terminou, mas seu grupo ficou inconclusivo."
            )
    if probe.status == "identity_mismatch":
        return "identity_mismatch"
    if probe.status not in {"alive", "dead"}:
        raise InstallerError("A identidade de um processo órfão ficou inconclusiva.")
    try:
        signal_recorded_process(entry, force=False)
    except ProcessLookupError:
        return "already_dead"
    except OSError as error:
        raise InstallerError(f"Não foi possível encerrar o processo órfão PID {entry['pid']}.") from error
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_still_matches(entry):
            return "terminated"
        time.sleep(0.05)
    try:
        signal_recorded_process(entry, force=True)
    except ProcessLookupError:
        return "terminated"
    except OSError as error:
        raise InstallerError(f"Não foi possível forçar o processo órfão PID {entry['pid']}.") from error
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if not process_still_matches(entry):
            return "killed"
        time.sleep(0.05)
    raise InstallerError(f"O processo órfão PID {entry['pid']} não encerrou; estado preservado.")


def write_session_journal(path: Path, data: dict[str, object], prefix: str = ".recovery-") -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=prefix, dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            json.dump(data, output, ensure_ascii=False, indent=2, sort_keys=True)
            output.write("\n")
        if os.name != "nt":
            temporary.chmod(0o600)
        temporary.replace(path)
        if os.name != "nt":
            path.chmod(0o600)
    finally:
        if lexists(temporary):
            remove_path(temporary)


def reconcile_journal(target: Path, path: Path) -> None:
    try:
        data = load_session_journal(path)
        if data.get("status") == "clean":
            return
    except InstallerError:
        console.warning(f"Journal de sessão inválido preservado para inspeção: {path.parent}")
        return
    controller_probe = journal_controller_probe(data)
    if controller_probe is not None and controller_probe.status == "alive":
        raise InstallerError(
            f"O controlador da sessão {data['session_id']} continua ativo; recuperação bloqueada."
        )
    if controller_probe is not None and controller_probe.status == "inconclusive":
        raise InstallerError(
            f"O controlador da sessão {data['session_id']} está inconclusivo; estado preservado."
        )
    data["status"] = "interrupted"
    actions = data.setdefault("recovery_actions", [])
    if not isinstance(actions, list):
        raise InstallerError(f"Ações de recuperação inválidas no journal: {path.parent}")
    processes = data.get("processes", [])
    assert isinstance(processes, list)
    if controller_probe is None:
        for entry in processes:
            legacy_probe = journal_process_probe(entry)
            if legacy_probe.status in {"alive", "inconclusive"}:
                raise InstallerError(
                    "Journal legado não prova que seu controlador terminou; "
                    f"processos e arquivos foram preservados em {path.parent}."
                )
    for entry in reversed(processes):
        probe = journal_process_probe(entry)
        if probe.status == "inconclusive":
            raise InstallerError(
                f"Processo possivelmente ativo não pôde ser confirmado; journal preservado: {path.parent}"
            )
        if not isinstance(entry, dict):
            continue
        result = terminate_recorded_process(entry)
        actions.append({
            "at": datetime.now(timezone.utc).isoformat(),
            "label": entry.get("label", "processo"),
            "pid": entry.get("pid"),
            "result": result,
        })
    for collection in ("temporary_files", "materialized_files"):
        entries = data.get(collection, [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("created_by_session"):
                continue
            candidate = journal_path(target, entry.get("path"))
            expected = entry.get("expected_hash")
            sensitive = entry.get("sensitive") is True
            if candidate is None or not lexists(candidate):
                continue
            if sensitive:
                unlink_sensitive_temporary(candidate)
            elif (
                isinstance(expected, str)
                and candidate.is_file()
                and not candidate.is_symlink()
                and file_sha256(candidate) == expected
            ):
                remove_path(candidate)
            else:
                entry["modified_during_session"] = True
                console.warning(f"Arquivo de sessão alterado foi preservado: {candidate}")
    directories = data.get("created_directories", [])
    if isinstance(directories, list):
        for relative in reversed(directories):
            candidate = journal_path(target, relative)
            if candidate is not None and candidate.is_dir() and not candidate.is_symlink():
                try:
                    candidate.rmdir()
                except OSError:
                    pass
    data["status"] = "clean"
    data["recovered_at"] = datetime.now(timezone.utc).isoformat()
    write_session_journal(path, data)


def recover_sessions(target: Path) -> None:
    assert_recovery_processes_confirmable(target)
    for path in session_journal_paths(target):
        reconcile_journal(target, path)


def unlink_sensitive_temporary(path: Path) -> None:
    """Unlink one sensitive temporary without traversing a replacement path."""
    if not lexists(path):
        return
    metadata = path.lstat()
    if stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        path.unlink()
        return
    kind = "diretório" if stat.S_ISDIR(metadata.st_mode) else "arquivo especial"
    raise InstallerError(
        f"Temporário sensível foi substituído por {kind} e foi preservado para inspeção: {path}"
    )


def cleanup_current_session(
    journal: SessionJournal | None,
    temporary_paths: list[Path],
    materialized_packages: list[MaterializedKtx],
) -> None:
    temporary_hashes: dict[str, str] = {}
    sensitive_paths: set[str] = set()
    if journal is not None:
        entries = journal.data.get("temporary_files", [])
        if isinstance(entries, list):
            temporary_hashes = {
                str(entry.get("path")): str(entry.get("expected_hash"))
                for entry in entries
                if isinstance(entry, dict) and isinstance(entry.get("expected_hash"), str)
            }
            sensitive_paths = {
                str(entry.get("path"))
                for entry in entries
                if isinstance(entry, dict) and entry.get("sensitive") is True
            }
    for path in temporary_paths:
        if not lexists(path):
            continue
        relative = journal._relative(path) if journal is not None else ""
        expected = temporary_hashes.get(relative)
        if relative in sensitive_paths:
            unlink_sensitive_temporary(path)
        elif (
            expected is not None
            and path.is_file()
            and not path.is_symlink()
            and file_sha256(path) == expected
        ):
            remove_path(path)
        elif journal is None:
            remove_path(path)
        else:
            console.warning(f"Arquivo temporário alterado foi preservado: {path}")
    for materialized in reversed(materialized_packages):
        if journal is not None:
            for entry in materialized.files:
                if (
                    entry.created_by_session
                    and lexists(entry.path)
                    and not (
                        entry.path.is_file()
                        and not entry.path.is_symlink()
                        and file_sha256(entry.path) == entry.expected_hash
                    )
                ):
                    journal.mark_modified(journal._relative(entry.path))
        cleanup_dedicated_ktx(materialized)


@contextmanager
def finalize_service_operation(resources: ServiceResources):
    """Guarantee independent cleanup attempts before the controller exits."""
    try:
        yield
    finally:
        original_error = sys.exc_info()[0] is not None
        cleanup_errors: list[str] = []
        if resources.journal is not None:
            try:
                resources.journal.set_status("stopping")
            except Exception as error:
                cleanup_errors.append(f"journal em stopping: {error}")
        cleanup_ok = True
        try:
            cleanup_current_session(
                resources.journal, resources.temporary_paths, resources.materialized_ktx,
            )
        except Exception as error:
            cleanup_ok = False
            cleanup_errors.append(f"conteúdo efêmero: {error}")
        if resources.journal is not None:
            try:
                resources.journal.set_status("clean" if cleanup_ok else "interrupted")
            except Exception as error:
                cleanup_errors.append(f"estado final do journal: {error}")
        if resources.installer is not None:
            try:
                resources.installer.cleanup_stage()
            except Exception as error:
                cleanup_errors.append(f"área temporária do instalador: {error}")
        if resources.session_lock is not None:
            try:
                resources.session_lock.release(
                    restore_reclaimed=not resources.recovery_confirmed,
                )
            except Exception as error:
                cleanup_errors.append(f"lock da instalação: {error}")
        for error in cleanup_errors:
            console.warning(f"Falha durante a finalização de {error}")
        if cleanup_errors and not original_error:
            raise InstallerError("A sessão terminou com falha crítica de finalização.")


def runtime_variant(
    system: str | None = None,
    machine: str | None = None,
    runtime_id: str | None = None,
) -> str:
    host_system = system or core.host_platform.system()
    host_machine = (machine or core.host_platform.machine()).casefold()
    system_id = core.HOST_PLATFORMS.get(host_system, host_system.casefold())
    architecture_aliases = core.CAPABILITY_CATALOG.get("architecture_aliases")
    if not isinstance(architecture_aliases, dict):
        raise InstallerError("Catálogo de arquiteturas da CLI está inválido.")
    variants = {
        str(platform_entry["variant"])
        for selected_runtime in ((runtime_id,) if runtime_id is not None else ("mvdsv", "qtv", "qwfwd"))
        for platform_entry in core.RUNTIMES[selected_runtime]["platforms"]
        if (
            isinstance(platform_entry, dict)
            and platform_entry.get("system") == system_id
            and host_machine in architecture_aliases.get(platform_entry.get("architecture"), [])
        )
    }
    if len(variants) == 1:
        return variants.pop()
    raise InstallerError(
        f"Runtime de serviço indisponível para {host_system} {host_machine}. "
        "Os alvos distribuídos são macOS arm64, Linux amd64 e Windows x64."
    )


def runtime_binary(installer: core.Installer, component: str) -> Path:
    runtime = core.RUNTIMES.get(component)
    if runtime is None or runtime.get("kind") not in {"server", "service"}:
        raise InstallerError(f"Runtime desconhecido: {component}")
    if installer.verify_component(component) == 0:
        raise InstallerError(
            f"O componente {component} não está instalado. "
            "Execute install.sh e selecione o perfil completo ou esse componente."
        )
    variant = runtime_variant(runtime_id=component)
    runtime_paths = runtime.get("runtime_path")
    if not isinstance(runtime_paths, dict) or not isinstance(runtime_paths.get(variant), str):
        raise InstallerError(f"Runtime {component} indisponível na variante {variant}.")
    binary = installer.target.joinpath(*PurePosixPath(runtime_paths[variant]).parts)
    if not binary.is_file() or binary.is_symlink():
        raise InstallerError(f"Executável gerenciado ausente ou inseguro: {binary}")
    if os.name != "nt" and not os.access(binary, os.X_OK):
        raise InstallerError(
            f"Executável gerenciado sem permissão de execução: {binary}. Execute repair."
        )
    return binary


def temporary_config(
    directory: Path,
    prefix: str,
    lines: list[str],
    journal: SessionJournal | None = None,
    *,
    sensitive: bool = True,
) -> Path:
    if not directory.is_dir() or directory.is_symlink():
        raise InstallerError(f"Diretório de configuração ausente ou inseguro: {directory}")
    descriptor, name = tempfile.mkstemp(prefix=prefix, suffix=".cfg", dir=directory)
    path = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            output.write("// x86QW: configuração efêmera removida ao encerrar.\n")
            output.write("\n".join(lines))
            output.write("\n")
        if os.name != "nt":
            path.chmod(0o600)
        if journal is not None:
            journal.record_temporary(
                path, "configuração efêmera redigida", sensitive=sensitive,
            )
        return path
    except Exception:
        if lexists(path):
            if sensitive:
                unlink_sensitive_temporary(path)
            else:
                remove_path(path)
        raise


def normalized_zip_member(info: zipfile.ZipInfo) -> tuple[PurePosixPath, str]:
    # ZipInfo.filename is normalized with the host separator by Python.  On
    # Windows that would turn a hostile backslash into a forward slash before
    # we can reject it, while orig_filename preserves the archive spelling.
    name = info.orig_filename
    if (
        not name
        or len(name) > MAX_ARCHIVE_PATH_LENGTH
        or "\\" in name
        or ":" in name
        or name.startswith("/")
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
    ):
        raise InstallerError(f"Membro inseguro no pacote: {name!r}")
    raw_parts = name[:-1].split("/") if info.is_dir() and name.endswith("/") else name.split("/")
    if (
        not raw_parts
        or len(raw_parts) > MAX_ARCHIVE_DEPTH
        or any(part in {"", ".", ".."} for part in raw_parts)
    ):
        raise InstallerError(f"Caminho interno inseguro no pacote: {name!r}")
    for part in raw_parts:
        if part.endswith((".", " ")):
            raise InstallerError(f"Nome incompatível com Windows no pacote: {name!r}")
        base_name = part.split(".", 1)[0].upper()
        if base_name in WINDOWS_RESERVED_NAMES:
            raise InstallerError(f"Nome reservado do Windows no pacote: {name!r}")
    relative = PurePosixPath(*raw_parts)
    if relative.is_absolute() or relative.drive or relative.root:
        raise InstallerError(f"Caminho absoluto no pacote: {name!r}")
    normalized = "/".join(
        unicodedata.normalize("NFC", part).casefold() for part in raw_parts
    )
    return relative, normalized


def validate_zip_members(
    archive: zipfile.ZipFile,
) -> tuple[tuple[zipfile.ZipInfo, PurePosixPath], ...]:
    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_MEMBERS:
        raise InstallerError(
            f"Pacote excede o limite de {MAX_ARCHIVE_MEMBERS} membros."
        )
    total_size = 0
    semantic_names: set[str] = set()
    validated: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
    for info in infos:
        relative, semantic_name = normalized_zip_member(info)
        if semantic_name in semantic_names:
            raise InstallerError(
                f"Colisão semântica de nomes no pacote: {info.filename!r}"
            )
        semantic_names.add(semantic_name)
        unix_mode = info.external_attr >> 16
        file_type = stat.S_IFMT(unix_mode)
        if stat.S_ISLNK(unix_mode) or file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
            raise InstallerError(f"Membro especial não permitido no pacote: {info.filename!r}")
        if info.file_size < 0 or info.file_size > MAX_ARCHIVE_MEMBER_SIZE:
            raise InstallerError(
                f"Membro excede o limite de {MAX_ARCHIVE_MEMBER_SIZE} bytes: {info.filename!r}"
            )
        total_size += info.file_size
        if total_size > MAX_ARCHIVE_TOTAL_SIZE:
            raise InstallerError(
                f"Pacote excede o limite descompactado de {MAX_ARCHIVE_TOTAL_SIZE} bytes."
            )
        if info.file_size and (
            info.compress_size <= 0
            or info.file_size / info.compress_size > MAX_ARCHIVE_COMPRESSION_RATIO
        ):
            raise InstallerError(
                f"Taxa de compressão anormal no pacote: {info.filename!r}"
            )
        validated.append((info, relative))
    return tuple(validated)


def ktx_assets(target: Path) -> frozenset[str]:
    package = target / "qw" / "ktx.pk3"
    if not package.is_file() or package.is_symlink():
        raise InstallerError(f"Pacote KTX ausente ou inseguro: {package}")
    try:
        with zipfile.ZipFile(package) as archive:
            return frozenset(
                relative.as_posix().casefold()
                for info, relative in validate_zip_members(archive)
                if not info.is_dir()
            )
    except (OSError, zipfile.BadZipFile) as error:
        raise InstallerError(f"Pacote KTX inválido: {package}") from error


def materialize_dedicated_pk3(
    package: Path,
    destination_root: Path,
    label: str,
    journal: SessionJournal | None = None,
) -> MaterializedKtx:
    """Expose verified PK3 members to MVDSV, which does not implement PK3 loading."""
    if not package.is_file() or package.is_symlink():
        raise InstallerError(f"Pacote {label} ausente ou inseguro: {package}")
    if not destination_root.is_dir() or destination_root.is_symlink():
        raise InstallerError(f"Diretório de {label} ausente ou inseguro: {destination_root}")
    materialized_files: list[MaterializedFile] = []
    created_directories: list[Path] = []
    try:
        with zipfile.ZipFile(package) as archive:
            for info, relative in validate_zip_members(archive):
                if info.is_dir():
                    continue
                destination = destination_root.joinpath(*relative.parts)
                parent = destination.parent
                missing_parents: list[Path] = []
                cursor = parent
                while cursor != destination_root and not lexists(cursor):
                    missing_parents.append(cursor)
                    cursor = cursor.parent
                if lexists(cursor) and (not cursor.is_dir() or cursor.is_symlink()):
                    raise InstallerError(f"Diretório inseguro ao preparar {label}: {cursor}")
                for directory in reversed(missing_parents):
                    directory.mkdir()
                    created_directories.append(directory)
                    if journal is not None:
                        journal.record_directory(directory)
                descriptor, temporary_name = tempfile.mkstemp(
                    prefix=".x86qw_ktx_", dir=destination.parent,
                )
                temporary = Path(temporary_name)
                try:
                    digest_builder = hashlib.sha256()
                    extracted_size = 0
                    with os.fdopen(descriptor, "wb") as output, archive.open(info) as source:
                        for block in iter(lambda: source.read(1024 * 1024), b""):
                            extracted_size += len(block)
                            if extracted_size > info.file_size:
                                raise InstallerError(
                                    f"Tamanho divergente no pacote {label}: {info.filename}"
                                )
                            digest_builder.update(block)
                            output.write(block)
                    if extracted_size != info.file_size:
                        raise InstallerError(
                            f"Tamanho divergente no pacote {label}: {info.filename}"
                        )
                    digest = digest_builder.hexdigest()
                    existed = lexists(destination)
                    if existed:
                        if (
                            not destination.is_file()
                            or destination.is_symlink()
                            or file_sha256(destination) != digest
                        ):
                            raise InstallerError(
                                f"Arquivo local conflita com a carga dedicada de {label}: {destination}"
                            )
                        materialized_files.append(MaterializedFile(
                            destination, digest, package.as_posix(), False, True,
                        ))
                        if journal is not None:
                            journal.record_materialized(materialized_files[-1])
                        continue
                    if os.name != "nt":
                        temporary.chmod(0o644)
                    temporary.replace(destination)
                    materialized_files.append(MaterializedFile(
                        destination, digest, package.as_posix(), True, False,
                    ))
                    if journal is not None:
                        journal.record_materialized(materialized_files[-1])
                finally:
                    if lexists(temporary):
                        remove_path(temporary)
    except InstallerError:
        cleanup_dedicated_ktx(MaterializedKtx(tuple(materialized_files), tuple(created_directories)))
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as error:
        cleanup_dedicated_ktx(MaterializedKtx(tuple(materialized_files), tuple(created_directories)))
        raise InstallerError(
            f"Não foi possível preparar a carga de {label} para o MVDSV: {error}"
        ) from error
    return MaterializedKtx(tuple(materialized_files), tuple(created_directories))


def materialize_dedicated_ktx(target: Path) -> MaterializedKtx:
    return materialize_dedicated_pk3(target / "qw/ktx.pk3", target / "qw", "KTX")


def cleanup_dedicated_ktx(materialized: MaterializedKtx) -> None:
    for entry in reversed(materialized.files):
        if not entry.created_by_session:
            continue
        path = entry.path
        if not lexists(path):
            continue
        if (
            path.is_file()
            and not path.is_symlink()
            and file_sha256(path) == entry.expected_hash
        ):
            remove_path(path)
        else:
            console.warning(f"Arquivo materializado alterado durante a sessão foi preservado: {path}")
    for directory in reversed(materialized.directories):
        if directory.is_dir() and not directory.is_symlink():
            try:
                directory.rmdir()
            except OSError:
                pass


def select_hosted_game(
    player: gameplay.Player,
    options: argparse.Namespace,
) -> HostedGame:
    player.check_paks()
    games = player.available_local_games()
    if not games:
        raise InstallerError(
            "Nenhum mod gerenciado está instalado. Execute o bootstrap e selecione um jogo."
        )
    game = player.choose_local_game(games, options.game, activity="hospedar")
    component = player.installed_component_for_game(game)
    if component is None:
        raise InstallerError(f"O componente de {game.label} não está mais instalado.")
    player.verify_component(component)
    player.verify_local_play_support(games)
    mode = None
    assets: frozenset[str] = frozenset()
    if game.mode_catalog is not None:
        mode = player.choose_ktx_mode(
            gameplay.load_ktx_modes(player.project_root),
            options.mode,
            activity="hospedar",
        )
        console.success(f"Modo KTX selecionado: {mode.label}.")
        assets = ktx_assets(player.target)
        map_name = player.choose_local_map(
            game,
            default_map=mode.default_map,
            suggested_maps=mode.suggested_maps,
            label=f"KTX · {mode.label}",
            requested_map=options.map,
            required_asset=mode.required_map_asset,
            available_assets=assets,
        )
    else:
        map_name = player.choose_local_map(game, requested_map=options.map)
    return HostedGame(game, mode, map_name, assets, options.ktx_options)


def materialize_hosted_game(
    player: gameplay.Player,
    selection: HostedGame,
    journal: SessionJournal | None = None,
) -> MaterializedKtx | None:
    package = player.game_marker_path(selection.game)
    if package.suffix.casefold() != ".pk3":
        return None
    return materialize_dedicated_pk3(
        package,
        player.target / selection.game.gamedir,
        selection.game.label,
        journal,
    )


def dedicated_ktx_settings(
    mode: gameplay.KtxModeSpec,
    map_name: str,
    assets: frozenset[str],
    options: gameplay.KtxLaunchOptions,
    maxclients: int,
) -> tuple[tuple[str, str], ...]:
    # Reuse the client launch validator, then translate supported choices into
    # server cvars because MVDSV has no client aliases or client-command entity.
    gameplay.ktx_launch_commands(mode, map_name, assets, options)
    settings: list[tuple[str, str]] = list(DEDICATED_MODE_CVARS.get(mode.key, ()))
    if gameplay.ktx_bot_options_requested(options):
        target_clients = min(8, maxclients) if options.fill_bots else options.bots + 1
        if target_clients > maxclients:
            raise InstallerError(
                f"--bots {options.bots} exige --maxclients de pelo menos {target_clients}."
            )
        settings.extend((
            ("k_fb_enabled", "1"),
            ("k_fb_skill", str(options.bot_skill)),
            ("k_fb_autoadd_limit", str(target_clients)),
            ("k_fb_autoremove_at", str(target_clients)),
        ))
        if options.bot_weapon is not None:
            settings.append((
                "k_fb_weapon", "0" if options.bot_weapon == "random" else options.bot_weapon,
            ))
        if options.bot_health is not None:
            settings.append(("k_fb_health", str(options.bot_health)))
        if options.bot_break_on_death:
            settings.append(("k_fb_break_on_death", "1"))
    if mode.key == "ctf":
        if options.ctf_hook is not None:
            hook_styles = {"smooth": "1", "fast": "2", "classic": "3", "crhook": "4"}
            settings.append(("k_ctf_hook", "0" if options.ctf_hook == "off" else "1"))
            if options.ctf_hook != "off":
                settings.append(("k_ctf_hookstyle", hook_styles[options.ctf_hook]))
        if options.ctf_runes is not None:
            settings.append(("k_ctf_runes", "1" if options.ctf_runes == "on" else "0"))
        if options.ctf_based_spawn:
            settings.append(("k_ctf_based_spawn", "1"))
    if mode.key == "race":
        if options.race_style is not None:
            settings.extend((
                ("k_race_simultaneous", "1" if options.race_style == "simultaneous" else "0"),
                ("k_race_match", "1" if options.race_style == "match" else "0"),
            ))
        if options.race_scoring is not None:
            scoring = {"win": "0", "scaled": "1", "formula1": "2"}
            settings.extend((
                ("k_race_match", "1"),
                ("k_race_scoring_system", scoring[options.race_scoring]),
            ))
    return tuple(settings)


def host_spec(
    installer: gameplay.Player,
    options: argparse.Namespace,
    selection: HostedGame,
    session_paths: list[Path],
    materialized_ktx: list[MaterializedKtx],
    journal: SessionJournal | None = None,
) -> ProcessSpec:
    binary = runtime_binary(installer, "mvdsv")
    game = selection.game
    mode = selection.mode
    map_name = selection.map_name
    materialized = materialize_hosted_game(installer, selection, journal)
    if materialized is not None:
        materialized_ktx.append(materialized)

    hostname = options.hostname or f"x86QW - {game.label}"
    user_config = (
        PurePosixPath(game.personal_config).name
    )
    post_map_settings: tuple[tuple[str, str], ...] = ()
    if game.mode_catalog is not None:
        assert mode is not None
        post_map_settings = dedicated_ktx_settings(
            mode, map_name, selection.assets, selection.ktx_options, options.maxclients,
        )
    bootstrap_password = secrets.token_urlsafe(24)
    initial_rcon_password = bootstrap_password
    lines = [
        f"exec {user_config}",
        f"hostname {q(safe_text(hostname, 'hostname'))}",
        f"maxclients {options.maxclients}",
        f"password {q(options.password)}",
        f"spectator_password {q(options.spectator_password)}",
        f"rcon_password {q(initial_rcon_password)}",
        f"set demo_tmp_record {0 if options.no_mvd else 1}",
    ]
    if game.mode_catalog is not None:
        assert mode is not None
        lines.extend((
            "sv_progtype 2",
            "sv_mintic 0.01",
            "sv_maxtic 0.03",
            "pm_ktjump 1",
            f"set k_defmode {mode.usermode}",
            f"set k_defmap {map_name}",
            f"set x86qw_ktx_preset {mode.key}",
        ))
        for name, value in mode.launch_settings:
            lines.append(f"{name} {value}")
    else:
        lines.extend((
            "sv_progtype 0",
            f"sv_gamedir {game.gamedir}",
            f"sv_progsname x86qw_{game.gamedir}",
            "sv_mintic 0",
            "sv_maxtic 0.1",
            "pm_ktjump 0.5",
        ))
        for name, value in game.dedicated_settings:
            lines.append(f"{name} {value}")
    if options.with_qtv:
        lines.extend((
            f"qtv_streamport {options.port}",
            f"qtv_password {q(options.qtv_password)}",
        ))
    lines.append(f"map {q(map_name)}")
    game_directory = installer.target / game.gamedir
    session = temporary_config(game_directory, "x86qw_host_", lines, journal)
    session_paths.append(session)
    post_map = temporary_config(
        game_directory,
        "x86qw_host_post_",
        [
            *(f"set {name} {value}" for name, value in post_map_settings),
            f"rcon_password {q(options.rcon_password)}",
        ],
        journal,
    )
    session_paths.append(post_map)
    startup_rcon = StartupRcon(
        local_service_address(options.bind), options.port,
        bootstrap_password, post_map.name, map_name, game.gamedir,
    )

    arguments = [
        str(binary), "-basedir", str(installer.target),
    ]
    arguments.extend(game.dedicated_arguments)
    arguments.extend((
        "-ip", options.bind, "-port", str(options.port), "-mem", "64",
        "+exec", session.name,
    ))
    return ProcessSpec("MVDSV", tuple(arguments), installer.target, startup_rcon)


def proxy_spec(installer: core.Installer, options: argparse.Namespace) -> ProcessSpec:
    binary = runtime_binary(installer, "qwfwd")
    directory = installer.target / "_x86qw" / "services" / "qwfwd"
    config = directory / "qwfwd.cfg"
    if not config.is_file() or config.is_symlink():
        raise InstallerError(f"Configuração QWFWD ausente ou insegura: {config}")
    return ProcessSpec(
        "QWFWD", (str(binary), str(options.proxy_port), options.proxy_bind), directory,
        readiness=ServiceReadiness("udp", local_service_address(options.proxy_bind), options.proxy_port),
    )


def qtv_spec(
    installer: core.Installer,
    *, bind: str,
    port: int,
    hostname: str,
    upstream: str | None,
    password: str,
    session_paths: list[Path],
    journal: SessionJournal | None = None,
) -> ProcessSpec:
    binary = runtime_binary(installer, "qtv")
    directory = installer.target / "_x86qw" / "services" / "qtv"
    config = directory / "qtv.cfg"
    if not config.is_file() or config.is_symlink():
        raise InstallerError(f"Configuração QTV ausente ou insegura: {config}")
    demos = directory / "demos"
    ensure_private_directory(demos)
    lines = [
        f"hostname {q(safe_text(hostname, 'hostname QTV'))}",
        f"listen_address {q(endpoint(bind, port))}",
        'masters ""',
        "http_enabled 1",
        "http_upload_enabled 0",
    ]
    if upstream is not None:
        upstream = parse_network_endpoint(upstream)
        safe_text(password, "senha QTV") if password else None
        lines.append(f"qtv {q(upstream)} {q(password)}")
    session = temporary_config(directory, "x86qw-session-", lines, journal)
    session_paths.append(session)
    return ProcessSpec(
        "QTV", (str(binary), "exec", session.name), directory,
        readiness=ServiceReadiness("http", local_service_address(bind), port, upstream),
    )


def preflight_ports(requests: list[tuple[str, str, int, str]]) -> None:
    seen: dict[int, str] = {}
    for label, address, port, kind in requests:
        if port in seen:
            raise InstallerError(
                f"Porta local duplicada: {port} foi solicitada por {seen[port]} e {label}."
            )
        seen[port] = label
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        socket_type = socket.SOCK_STREAM if kind == "tcp" else socket.SOCK_DGRAM
        try:
            with socket.socket(family, socket_type) as listener:
                if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                listener.bind((address, port))
        except OSError as error:
            raise InstallerError(
                f"A porta {endpoint(address, port)} de {label} não está disponível."
            ) from error


def requested_ports(options: argparse.Namespace) -> list[tuple[str, str, int, str]]:
    if options.action == "proxy":
        return [("QWFWD", options.proxy_bind, options.proxy_port, "udp")]
    if options.action == "qtv":
        return [("QTV", options.bind, options.port, "tcp")]
    requests = [("MVDSV", options.bind, options.port, "udp")]
    if options.with_qtv:
        requests.append(("QTV", options.qtv_bind, options.qtv_port, "tcp"))
    if options.with_proxy:
        requests.append(("QWFWD", options.proxy_bind, options.proxy_port, "udp"))
    return requests


def wait_http_readiness(
    process: subprocess.Popen[bytes],
    readiness: ServiceReadiness,
    timeout: float = 8.0,
) -> None:
    deadline = time.monotonic() + timeout
    last_response = b""
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise InstallerError("QTV encerrou antes de ficar pronto.")
        try:
            with socket.create_connection((readiness.address, readiness.port), timeout=0.4) as connection:
                connection.sendall(b"GET / HTTP/1.0\r\nHost: x86qw.local\r\n\r\n")
                chunks: list[bytes] = []
                while sum(map(len, chunks)) < 1024 * 1024:
                    block = connection.recv(65535)
                    if not block:
                        break
                    chunks.append(block)
                last_response = b"".join(chunks)
                if last_response.startswith(b"HTTP/"):
                    if readiness.upstream is not None:
                        upstream_host, _ = endpoint_parts(readiness.upstream)
                        if upstream_host.casefold().encode("utf-8") not in last_response.lower():
                            time.sleep(0.1)
                            continue
                    return
        except OSError:
            pass
        time.sleep(0.1)
    if readiness.upstream is not None and last_response.startswith(b"HTTP/"):
        raise InstallerError("QTV respondeu por HTTP, mas não registrou o upstream solicitado.")
    raise InstallerError(f"QTV não respondeu em http://{endpoint(readiness.address, readiness.port)}/.")


def wait_udp_readiness(
    process: subprocess.Popen[bytes],
    readiness: ServiceReadiness,
    timeout: float = 1.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise InstallerError("QWFWD encerrou durante a inicialização.")
        time.sleep(0.05)
    family = socket.AF_INET6 if ":" in readiness.address else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as probe:
            if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind((readiness.address, readiness.port))
    except OSError:
        return
    raise InstallerError("QWFWD permaneceu vivo, mas não ocupou a porta UDP solicitada.")


def stop_processes(processes: list[subprocess.Popen[bytes]]) -> None:
    groups: list[int] = []
    for process in reversed(processes):
        if os.name != "nt" and isinstance(process, POPEN_TYPE):
            process_group = int(getattr(process, "_x86qw_process_group", process.pid))
            if process_group > 1 and process_group not in groups:
                groups.append(process_group)
            status = posix_process_group_status(process_group)
            if status == "inconclusive":
                raise InstallerError(
                    f"Não foi possível confirmar o grupo de processos {process_group}; estado preservado."
                )
            if status == "alive":
                try:
                    os.killpg(process_group, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                except OSError as error:
                    raise InstallerError(
                        f"Não foi possível encerrar o grupo de processos {process_group}."
                    ) from error
        elif process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
    deadline = time.monotonic() + 4
    for process in reversed(processes):
        if process.poll() is None:
            try:
                process.wait(max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                if os.name == "nt" or not isinstance(process, POPEN_TYPE):
                    try:
                        process.kill()
                        process.wait()
                    except OSError:
                        pass
    for process_group in groups:
        status = posix_process_group_status(process_group)
        if status == "inconclusive":
            raise InstallerError(
                f"Não foi possível confirmar o encerramento do grupo {process_group}."
            )
        if status == "alive":
            try:
                os.killpg(process_group, signal.SIGKILL)
            except ProcessLookupError:
                continue
            except OSError as error:
                raise InstallerError(
                    f"Não foi possível forçar o encerramento do grupo {process_group}."
                ) from error
            group_deadline = time.monotonic() + 1.0
            while time.monotonic() < group_deadline:
                if posix_process_group_status(process_group) == "dead":
                    break
                time.sleep(0.05)
            else:
                raise InstallerError(
                    f"O grupo de processos {process_group} permaneceu ativo após SIGKILL."
                )


def posix_process_group_status(process_group: int) -> str:
    try:
        os.killpg(process_group, 0)
        return "alive"
    except ProcessLookupError:
        return "dead"
    except PermissionError:
        return "inconclusive"
    except OSError as error:
        if error.errno == errno.ESRCH:
            return "dead"
        if error.errno == errno.EPERM:
            return "inconclusive"
        return "inconclusive"


def _windows_job_kernel32():
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


class WindowsJobObject:
    """Own a Windows process tree and terminate it when the controller closes."""

    def __init__(self) -> None:
        from ctypes import wintypes

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("read_operations", ctypes.c_ulonglong),
                ("write_operations", ctypes.c_ulonglong),
                ("other_operations", ctypes.c_ulonglong),
                ("read_bytes", ctypes.c_ulonglong),
                ("write_bytes", ctypes.c_ulonglong),
                ("other_bytes", ctypes.c_ulonglong),
            ]

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("per_process_user_time", ctypes.c_longlong),
                ("per_job_user_time", ctypes.c_longlong),
                ("limit_flags", wintypes.DWORD),
                ("minimum_working_set", ctypes.c_size_t),
                ("maximum_working_set", ctypes.c_size_t),
                ("active_process_limit", wintypes.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority_class", wintypes.DWORD),
                ("scheduling_class", wintypes.DWORD),
            ]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("basic", BasicLimitInformation),
                ("io", IoCounters),
                ("process_memory_limit", ctypes.c_size_t),
                ("job_memory_limit", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t),
                ("peak_job_memory", ctypes.c_size_t),
            ]

        self.kernel32 = _windows_job_kernel32()
        self.handle = self.kernel32.CreateJobObjectW(None, None)
        if not self.handle:
            raise InstallerError(
                f"Não foi possível criar o Job Object dos serviços ({ctypes.get_last_error()})."
            )
        information = ExtendedLimitInformation()
        information.basic.limit_flags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel32.SetInformationJobObject(
            self.handle, 9, ctypes.byref(information), ctypes.sizeof(information),
        ):
            error = ctypes.get_last_error()
            self.kernel32.CloseHandle(self.handle)
            self.handle = None
            raise InstallerError(f"Não foi possível configurar o Job Object ({error}).")

    def assign(self, process: subprocess.Popen[bytes]) -> None:
        if self.handle is None or not self.kernel32.AssignProcessToJobObject(
            self.handle, int(process._handle),  # type: ignore[attr-defined]
        ):
            raise InstallerError(
                f"Não foi possível associar PID {process.pid} ao Job Object "
                f"({ctypes.get_last_error()})."
            )

    def close(self) -> None:
        if self.handle is None:
            return
        handle = self.handle
        self.handle = None
        if not self.kernel32.CloseHandle(handle):
            raise InstallerError(
                f"Não foi possível fechar o Job Object ({ctypes.get_last_error()})."
            )


def apply_startup_rcon(startup: StartupRcon, timeout: float = 8.0) -> None:
    family = socket.AF_INET6 if ":" in startup.address else socket.AF_INET
    destination = (startup.address, startup.port)
    deadline = time.monotonic() + timeout
    with socket.socket(family, socket.SOCK_DGRAM) as connection:
        connection.settimeout(0.25)
        while time.monotonic() < deadline:
            connection.sendto(b"\xff\xff\xff\xffstatus\n", destination)
            try:
                response, _ = connection.recvfrom(65535)
            except TimeoutError:
                continue
            if response.startswith(b"\xff\xff\xff\xff"):
                break
        else:
            raise InstallerError(
                f"MVDSV não respondeu em {endpoint(startup.address, startup.port)}."
            )

        decoded_status = response.decode("latin-1", errors="replace").casefold()
        if startup.expected_map.casefold() not in decoded_status:
            raise InstallerError(
                f"MVDSV respondeu, mas não confirmou o mapa {startup.expected_map}."
            )
        serverinfo_command = f"rcon {startup.password} serverinfo\n".encode("ascii")
        connection.sendto(b"\xff\xff\xff\xff" + serverinfo_command, destination)
        try:
            serverinfo, _ = connection.recvfrom(65535)
        except TimeoutError as error:
            raise InstallerError("MVDSV não confirmou o gamecode carregado.") from error
        if b"Bad rcon_password" in serverinfo:
            raise InstallerError("MVDSV rejeitou o preflight RCON local.")
        combined = (response + b"\n" + serverinfo).decode("latin-1", errors="replace").casefold()
        if startup.expected_gamedir.casefold() not in combined:
            raise InstallerError(
                f"MVDSV não confirmou o gamecode {startup.expected_gamedir}."
            )

        # Apply typed post-map settings and restore the final RCON password.
        command = f"rcon {startup.password} exec {startup.config_name}\n".encode("ascii")
        connection.settimeout(2.0)
        connection.sendto(b"\xff\xff\xff\xff" + command, destination)
        try:
            response, _ = connection.recvfrom(65535)
        except TimeoutError as error:
            raise InstallerError("MVDSV não confirmou a configuração dedicada.") from error
        if b"Bad rcon_password" in response:
            raise InstallerError("MVDSV rejeitou a configuração dedicada por RCON local.")


class ServiceSignal(Exception):
    def __init__(self, signum: int) -> None:
        self.signum = signum


def run_processes(specs: list[ProcessSpec], journal: SessionJournal | None = None) -> int:
    processes: list[subprocess.Popen[bytes]] = []
    previous_handlers: dict[int, object] = {}
    windows_job = WindowsJobObject() if os.name == "nt" else None

    def interrupted(signum: int, _frame: object) -> None:
        raise ServiceSignal(signum)

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                previous_handlers[signum] = signal.signal(signum, interrupted)
            except (ValueError, OSError):
                pass
        for spec in specs:
            console.detail(f"Iniciando {spec.label}: {spec.arguments[0]}")
            popen_options: dict[str, object] = {}
            if os.name == "nt":
                popen_options["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            else:
                popen_options["start_new_session"] = True
            process = subprocess.Popen(spec.arguments, cwd=spec.cwd, **popen_options)
            if windows_job is not None:
                try:
                    windows_job.assign(process)
                except Exception:
                    try:
                        process.terminate()
                        try:
                            process.wait(timeout=4)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=1)
                    except Exception as cleanup_error:
                        console.warning(
                            f"Falha ao encerrar PID {process.pid} após associação recusada: "
                            f"{cleanup_error}"
                        )
                    raise
            process_group = process.pid
            setattr(process, "_x86qw_process_group", process_group)
            processes.append(process)
            if journal is not None:
                journal.record_process(spec, process, process_group)
            if spec.startup_rcon is not None:
                apply_startup_rcon(spec.startup_rcon)
                console.detail("MVDSV pronto; configuração pós-map aplicada e senha RCON restaurada.")
            if spec.readiness is not None:
                if spec.readiness.kind == "http":
                    wait_http_readiness(process, spec.readiness)
                    console.detail("QTV pronto e respondendo por HTTP.")
                elif spec.readiness.kind == "udp":
                    wait_udp_readiness(process, spec.readiness)
                    console.detail("QWFWD pronto e mantendo a porta UDP.")
        if journal is not None:
            journal.set_status("running")
        while True:
            for spec, process in zip(specs, processes):
                code = process.poll()
                if code is not None:
                    if code != 0:
                        console.warning(f"{spec.label} encerrou com código {code}.")
                    return code
            time.sleep(0.1)
    except (KeyboardInterrupt, ServiceSignal) as error:
        console.info("Encerrando serviços x86QW…")
        if journal is not None:
            journal.set_status("interrupted")
        signum = error.signum if isinstance(error, ServiceSignal) else signal.SIGINT
        return 128 + int(signum)
    except OSError as error:
        raise InstallerError(f"Não foi possível iniciar um serviço: {error}") from error
    finally:
        original_error = sys.exc_info()[0] is not None
        finalization_errors: list[Exception] = []
        try:
            stop_processes(processes)
        except Exception as error:
            finalization_errors.append(error)
        try:
            if windows_job is not None:
                windows_job.close()
        except Exception as error:
            finalization_errors.append(error)
        for signum, handler in previous_handlers.items():
            try:
                signal.signal(signum, handler)
            except (ValueError, OSError) as error:
                finalization_errors.append(error)
        for error in finalization_errors:
            console.warning(f"Falha ao finalizar árvore de processos: {error}")
        if finalization_errors and not original_error:
            first = finalization_errors[0]
            if isinstance(first, InstallerError):
                raise first
            raise InstallerError("Falha ao finalizar a árvore de processos.") from first


def add_target(parser: argparse.ArgumentParser, project_root: Path) -> None:
    parser.add_argument(
        "--target", type=Path, default=project_root / "quake-world",
        help="diretório da instalação x86QW",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="mostra detalhes técnicos")
    parser.add_argument("--no-color", action="store_true", help="desativa cores")


def parse_arguments(arguments: list[str], project_root: Path) -> argparse.Namespace:
    parser = core.FriendlyArgumentParser(
        prog="x86qw", description="Hospeda jogos e executa os serviços QuakeWorld do x86QW.",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    host = subparsers.add_parser(
        "host",
        help="inicia somente um servidor dedicado com MVDSV",
        description="Hospeda um jogo instalado somente no servidor MVDSV, sem abrir o ezQuake.",
        add_help=False,
    )
    host._positionals.title = "argumentos"
    host._optionals.title = "opções"
    host.add_argument("-h", "--help", action="help", help="mostra esta ajuda e encerra")
    add_target(host, project_root)
    gameplay.add_game_launch_arguments(host, dedicated=True)
    host.add_argument(
        "selection", nargs="?",
        help="jogo: ktx, final-arena, pro-x, team-fortress ou td2",
    )
    host.add_argument("--bind", type=bind_address, default="127.0.0.1", help="IP do servidor (padrão: loopback)")
    host.add_argument("--port", type=bounded_integer(1024, 65535), default=28501)
    host.add_argument("--hostname", help="nome público do servidor")
    host.add_argument("--maxclients", type=bounded_integer(1, 32), default=16)
    add_password_source(
        host, destination="password", legacy_flag="--password",
        prompt_flag="--prompt-password", file_flag="--password-file",
        help_label="senha para jogadores",
    )
    add_password_source(
        host, destination="spectator_password", legacy_flag="--spectator-password",
        prompt_flag="--prompt-spectator-password", file_flag="--spectator-password-file",
        help_label="senha para espectadores",
    )
    add_password_source(
        host, destination="rcon_password", legacy_flag="--rcon-password",
        prompt_flag="--prompt-rcon-password", file_flag="--rcon-password-file",
        help_label="senha administrativa RCON",
    )
    host.add_argument("--no-mvd", action="store_true", help="desativa gravação automática de MVD")
    host.add_argument("--with-qtv", action="store_true", help="inicia QTV conectado ao servidor")
    host.add_argument("--qtv-bind", type=bind_address, default="127.0.0.1")
    host.add_argument("--qtv-port", type=bounded_integer(1024, 65535), default=28000)
    add_password_source(
        host, destination="qtv_password", legacy_flag="--qtv-password",
        prompt_flag="--prompt-qtv-password", file_flag="--qtv-password-file",
        help_label="segredo entre MVDSV e QTV",
    )
    host.add_argument("--with-proxy", action="store_true", help="inicia também o QWFWD")
    host.add_argument("--proxy-bind", type=bind_address, default="127.0.0.1")
    host.add_argument("--proxy-port", type=bounded_integer(1024, 65535), default=30000)

    proxy = subparsers.add_parser("proxy", help="inicia o proxy QWFWD")
    add_target(proxy, project_root)
    proxy.add_argument("--bind", dest="proxy_bind", type=bind_address, default="127.0.0.1")
    proxy.add_argument("--port", dest="proxy_port", type=bounded_integer(1024, 65535), default=30000)

    qtv = subparsers.add_parser("qtv", help="inicia o relay HTTP/MVD QTV")
    add_target(qtv, project_root)
    qtv.add_argument("--bind", type=bind_address, default="127.0.0.1")
    qtv.add_argument("--port", type=bounded_integer(1024, 65535), default=28000)
    qtv.add_argument("--hostname", default="x86QW QTV")
    qtv.add_argument("--upstream", type=parse_network_endpoint, help="MVDSV de origem no formato host:porta")
    add_password_source(
        qtv, destination="qtv_password", legacy_flag=("--password", "--qtv-password"),
        prompt_flag="--prompt-qtv-password", file_flag="--qtv-password-file",
        help_label="senha QTV configurada no MVDSV",
    )
    namespace = parser.parse_args(arguments)
    if namespace.action == "host":
        namespace.game = None
        if namespace.selection is not None:
            game_keys = {game.key for game in gameplay.LOCAL_GAMES}
            if namespace.selection.casefold() not in game_keys:
                parser.error(f"jogo desconhecido: {namespace.selection}")
            namespace.game = namespace.selection.casefold()
        namespace.game, namespace.ktx_options = gameplay.resolve_ktx_launch_options(
            parser, namespace, namespace.game,
        )
    return namespace


def main(arguments: list[str] | None = None) -> int:
    temporary_paths: list[Path] = []
    materialized_ktx: list[MaterializedKtx] = []
    resources = ServiceResources(temporary_paths, materialized_ktx)
    try:
        with finalize_service_operation(resources):
            options = parse_arguments(sys.argv[1:] if arguments is None else arguments, core.PROJECT_ROOT)
            console.configure(verbose=options.verbose, no_color=options.no_color)
            resolve_passwords(options)
            warn_external_bind(options)
            target = options.target.expanduser().resolve()
            installer = gameplay.Player(
                core.PROJECT_ROOT, target, online_only=core.ZIPAPP_PATH is not None,
            )
            resources.installer = installer
            installer.validate_target("verify", purge=False)
            installer.reject_target_symlinks()
            session_lock = SessionLock.acquire(target, options.action)
            resources.session_lock = session_lock
            recover_sessions(target)
            session_lock.confirm_recovery()
            resources.recovery_confirmed = True
            preflight_ports(requested_ports(options))

            if options.action == "proxy":
                console.banner("iniciar QWFWD", target)
                spec = proxy_spec(installer, options)
                journal = SessionJournal(
                    target, session_id=session_lock.session_id, controller=session_lock.owner,
                )
                resources.journal = journal
                console.info(f"Proxy local: {options.proxy_bind}:{options.proxy_port}/UDP")
                return run_processes([spec], journal)
            if options.action == "qtv":
                console.banner("iniciar QTV", target)
                journal = SessionJournal(
                    target, session_id=session_lock.session_id, controller=session_lock.owner,
                )
                resources.journal = journal
                spec = qtv_spec(
                    installer, bind=options.bind, port=options.port,
                    hostname=options.hostname, upstream=options.upstream,
                    password=options.qtv_password, session_paths=temporary_paths,
                    journal=journal,
                )
                console.info(f"QTV HTTP: http://{endpoint(options.bind, options.port)}/")
                return run_processes([spec], journal)

            console.banner("hospedar jogo com MVDSV", target)
            selection = select_hosted_game(installer, options)
            hostname = options.hostname or f"x86QW - {selection.game.label}"
            safe_text(options.password, "senha de jogador") if options.password else None
            safe_text(options.spectator_password, "senha de espectador") if options.spectator_password else None
            safe_text(options.rcon_password, "senha RCON") if options.rcon_password else None
            safe_text(options.qtv_password, "senha QTV") if options.qtv_password else None
            journal = SessionJournal(
                target, session_id=session_lock.session_id, controller=session_lock.owner,
            )
            resources.journal = journal
            host = host_spec(
                installer, options, selection, temporary_paths, materialized_ktx, journal,
            )
            specs: list[ProcessSpec] = [host]
            if options.with_qtv:
                specs.append(qtv_spec(
                    installer, bind=options.qtv_bind, port=options.qtv_port,
                    hostname=f"{hostname} QTV",
                    upstream=host_qtv_upstream(options.bind, options.port),
                    password=options.qtv_password, session_paths=temporary_paths,
                    journal=journal,
                ))
            if options.with_proxy:
                specs.append(proxy_spec(installer, options))
            label = selection.game.label
            if selection.mode is not None:
                label += f" · {selection.mode.label}"
            console.info(
                f"Servidor: connect {options.bind}:{options.port} · {label} em {selection.map_name}"
            )
            if options.with_qtv:
                console.info(f"QTV HTTP: http://{endpoint(options.qtv_bind, options.qtv_port)}/")
            return run_processes(specs, journal)
    except InstallerError as error:
        console.error(str(error))
        return 1
    except Exception as error:
        if "options" in locals() and getattr(options, "verbose", False):
            import traceback
            traceback.print_exc()
        console.error(f"Falha inesperada nos serviços x86QW: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
