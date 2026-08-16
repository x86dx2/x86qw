#!/usr/bin/env python3
"""Launch the dedicated MVDSV, QWFWD and QTV components installed by x86QW."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import importlib
import ipaddress
import json
import os
import secrets
import signal
import stat
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

sys.dont_write_bytecode = True

session_control = importlib.import_module("x86qw_runtime.session_control")
from x86qw_runtime.ui import menu as navigation

from x86qw_runtime.io.archive import (
    ArchiveError,
    scan_archive,
)
from x86qw_runtime.io import private_fs
from x86qw_runtime.io.atomic import AtomicWriteError, atomic_write_json
from x86qw_runtime.io.paths import lexists, remove_path
from x86qw_runtime.io.managed_files import (
    MAX_MANAGED_FILE_SIZE as _MAX_MANAGED_FILE_SIZE,
    MaterializedDirectory,
    MaterializedFile,
    cleanup_materialized_archive,
    cleanup_materialized_directory,
    cleanup_materialized_file,
    cleanup_sensitive_temporary,
    describe_non_sensitive_temporary,
    file_matches_sha256,
    file_sha256,
    materialize_archive,
    persistent_descriptor_identity,
    unlink_sensitive_temporary,
)
from x86qw_runtime.errors import ExitCode, InstallerError
from x86qw_runtime.host_presets import apply_host_preset, load_host_presets, save_host_preset
from x86qw_runtime.platform import host as host_adapter
from x86qw_runtime.ui.arguments import FriendlyArgumentParser
from x86qw_runtime.ui.console import Console as RuntimeConsole
from x86qw_runtime.supervisor.models import ProcessSpec, ServiceReadiness, StartupRcon
from x86qw_runtime.supervisor.readiness import (
    apply_startup_rcon,
    preflight_ports,
    qtv_http_response_ready,
    wait_http_readiness,
    wait_udp_readiness,
)
from x86qw_runtime.supervisor.core import (
    POPEN_TYPE,
    ServiceSignal,
    WindowsJobObject,
    posix_process_group_status,
    run_processes,
    spawn_background_controller,
    stop_processes,
)
from x86qw_runtime.supervisor import sessions as runtime_sessions

SessionJournal = runtime_sessions.SessionJournal
journal_path = runtime_sessions.journal_path
load_session_journal = runtime_sessions.load_session_journal
journal_controller_probe = runtime_sessions.journal_controller_probe
journal_process_probe = runtime_sessions.journal_process_probe
legacy_clean_journal_is_inert = runtime_sessions.legacy_clean_journal_is_inert
session_journal_paths = runtime_sessions.session_journal_paths
assert_recovery_processes_confirmable = (
    runtime_sessions.assert_recovery_processes_confirmable
)
process_still_matches = runtime_sessions.process_still_matches
signal_recorded_process = runtime_sessions.signal_recorded_process
terminate_recorded_process = runtime_sessions.terminate_recorded_process
write_session_journal = runtime_sessions.write_session_journal
unlink_stop_request = runtime_sessions.unlink_stop_request
publish_stop_request = runtime_sessions.publish_stop_request
activate_background_log = runtime_sessions.activate_background_log


@dataclass(frozen=True)
class ServiceContext:
    """Dependencies supplied by the manager composition root."""

    project_root: Path
    zipapp_path: Path | None
    installer_base: type
    runtimes: dict[str, object]
    capability_catalog: dict[str, object]
    host_platforms: dict[str, str]
    host_platform: object
    console: object
    gameplay_module: object
    gameplay_context: object


_service_context: ServiceContext | None = None


def configure_context(context: ServiceContext) -> None:
    """Bind one explicit service composition before parsing or execution."""

    global _service_context, console
    if not isinstance(context, ServiceContext):
        raise TypeError("contexto de serviços inválido")
    _service_context = context
    console = context.console
    context.gameplay_module.configure_context(context.gameplay_context)


def _context() -> ServiceContext:
    if _service_context is None:
        raise RuntimeError(
            "O adapter de serviços requer um ServiceContext explícito antes da execução."
        )
    return _service_context


class _ContextProxy:
    def __getattr__(self, name: str) -> object:
        aliases = {
            "PROJECT_ROOT": "project_root",
            "ZIPAPP_PATH": "zipapp_path",
            "Installer": "installer_base",
            "RUNTIMES": "runtimes",
            "CAPABILITY_CATALOG": "capability_catalog",
            "HOST_PLATFORMS": "host_platforms",
        }
        return getattr(_context(), aliases.get(name, name))


core = _ContextProxy()


class _GameplayProxy:
    def __getattr__(self, name: str) -> object:
        return getattr(_context().gameplay_module, name)


gameplay = _GameplayProxy()
console: object = RuntimeConsole()
ProcessIdentity = session_control.ProcessIdentity
ProcessProbe = session_control.ProcessProbe
new_session_id = session_control.new_session_id
process_identity = session_control.process_identity
probe_expected_process = session_control.probe_expected_process
_LEGACY_ACL_MIGRATED = session_control._LEGACY_ACL_MIGRATED


def reconcile_journal(target: Path, path: Path) -> None:
    """Compatibility facade using the active service reporter."""

    runtime_sessions.reconcile_journal(target, path, reporter=console)


def recover_sessions(target: Path) -> None:
    """Compatibility facade using the active service reporter."""

    runtime_sessions.recover_sessions(target, reporter=console)


class SessionLock(session_control.InstallationLock):
    """Compatibility facade over the shared installation operation lock."""

    @classmethod
    def acquire(cls, target: Path, command: str) -> SessionLock:
        try:
            return super().acquire(target, command, "service")
        except session_control.SessionControlError as error:
            raise InstallerError(str(error)) from error

BACKGROUND_SECRET_FIELDS = (
    "password", "spectator_password", "rcon_password", "qtv_password",
)
BACKGROUND_START_TIMEOUT = 30.0
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
class MaterializedKtx:
    files: tuple[MaterializedFile, ...]
    directories: tuple[MaterializedDirectory, ...]
    root: Path | None = None


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
    if metadata.st_size > 4096:
        raise InstallerError(f"O arquivo de {label} excede o limite de 4096 bytes.")
    try:
        value = private_fs.read_private_user_file(path, maximum_size=4096).decode("utf-8")
    except (OSError, UnicodeError) as error:
        guidance = private_fs.private_file_permission_guidance()
        raise InstallerError(
            f"Permissões inseguras no arquivo de {label}; {guidance}."
        ) from error
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
        elif getattr(options, prompt_name, False) and not value:
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
    try:
        private_fs.ensure_private_directory(path)
    except OSError as error:
        raise InstallerError(f"Diretório de serviço ausente ou inseguro: {path}") from error


STATUS_PARAMETER_LABELS = {
    "game": "Jogo",
    "mode": "Modo",
    "map": "Mapa",
    "bots": "Bots",
    "bot_skill": "Habilidade",
    "bot_names": "Nomes",
    "bot_team": "Equipe",
    "bot_weapon": "Arma",
    "bot_health": "Vida",
    "ctf_hook": "Gancho CTF",
    "ctf_runes": "Runas CTF",
    "race_style": "Estilo Race",
    "race_scoring": "Pontuação Race",
    "hostname": "Nome",
    "bind": "Bind",
    "port": "Porta",
    "maxclients": "Clientes",
    "mvd": "Gravação MVD",
    "memory": "Memória",
    "secrets": "Segredos",
    "protocol": "Protocolo",
    "http": "HTTP",
    "upstream": "Upstream",
    "upstream_secret": "Segredo upstream",
}
SESSION_STATUS_LABELS = {
    "starting": "inicializando",
    "running": "ativa",
    "stopping": "encerrando",
    "interrupted": "interrompida",
    "clean": "finalizada",
}


def status_value(value: object) -> str:
    text = str(value)
    return "".join(character if character.isprintable() else "?" for character in text)[:512]


def process_status_label(probe: ProcessProbe) -> str:
    return {
        "alive": "ativo",
        "dead": "encerrado",
        "identity_mismatch": "PID reutilizado; processo preservado",
        "inconclusive": "identidade inconclusiva",
    }.get(probe.status, status_value(probe.status))


def status_executable(target: Path, value: object) -> str:
    executable = Path(str(value))
    try:
        return executable.resolve(strict=False).relative_to(target).as_posix()
    except ValueError:
        return status_value(executable)


def print_status_row(label: str, value: object) -> None:
    print(f"  {label:<18}| {status_value(value)}")


def print_service_session_status(target: Path, data: dict[str, object], *, owns_lock: bool) -> None:
    session_id = str(data["session_id"])
    console.section(f"Sessão {session_id}")
    print_status_row(
        "Estado", SESSION_STATUS_LABELS.get(str(data["status"]), data["status"]),
    )
    print_status_row("Lock", "ativo" if owns_lock else "ausente")
    print_status_row("Iniciada", data["created_at"])
    print_status_row(
        "Execução", "segundo plano" if data.get("background") else "primeiro plano",
    )
    if data.get("background_log"):
        print_status_row("Log", data["background_log"])
    controller = data.get("controller")
    if isinstance(controller, dict):
        controller_probe = journal_controller_probe(data)
        assert controller_probe is not None
        print_status_row(
            "Controlador",
            f"PID {controller['pid']} · {process_status_label(controller_probe)}",
        )
        print_status_row("Comando", controller["command"])
    else:
        print_status_row("Controlador", "identidade não registrada")

    processes = data.get("processes", [])
    assert isinstance(processes, list)
    if not processes:
        console.info("Nenhum processo foi registrado; a stack pode ainda estar inicializando.")
        return
    for entry in processes:
        assert isinstance(entry, dict)
        label = status_value(entry.get("label", "Serviço"))
        probe = journal_process_probe(entry)
        print(f"\n  {console.paint(label, '1;36')}")
        print_status_row("Estado", process_status_label(probe))
        print_status_row("PID", entry["pid"])
        print_status_row("Runtime", entry.get("runtime", label.casefold()))
        address, port = entry.get("address"), entry.get("port")
        if isinstance(address, str) and isinstance(port, int):
            print_status_row("Endpoint local", endpoint(address, port))
        if entry.get("executable"):
            print_status_row("Executável", status_executable(target, entry["executable"]))
        parameters = entry.get("parameters", {})
        assert isinstance(parameters, dict)
        displayed = False
        for name, parameter_label in STATUS_PARAMETER_LABELS.items():
            if name not in parameters:
                continue
            print_status_row(parameter_label, parameters[name])
            displayed = True
        if not displayed:
            print_status_row("Parâmetros", "não registrados nesta sessão")


def show_service_status(target: Path) -> bool:
    console.banner("visualizar serviços ativos", target)
    sessions = target / ".x86qw" / "sessions"
    lock_path = sessions / "active.lock"
    lock_owner: dict[str, object] | None = None
    active_service = False
    if lexists(lock_path):
        try:
            lock_owner = session_control.read_lock_owner(lock_path)
        except session_control.SessionControlError as error:
            raise InstallerError(str(error)) from error
        probe = probe_expected_process(
            int(lock_owner["controller_pid"]),
            str(lock_owner["controller_start_token"]),
            str(lock_owner["controller_executable"]),
        )
        console.section("Operação ativa")
        print_status_row("Tipo", {
            "service": "serviços",
            "maintenance": "manutenção",
        }.get(str(lock_owner.get("operation_kind", "service")), "serviços"))
        print_status_row("Comando", lock_owner["command"])
        print_status_row("Sessão", lock_owner["session_id"])
        print_status_row(
            "Controlador",
            f"PID {lock_owner['controller_pid']} · {process_status_label(probe)}",
        )
        active_service = (
            lock_owner.get("operation_kind", "service") == "service"
            and probe.status == "alive"
        )

    unfinished: list[tuple[Path, dict[str, object]]] = []
    for path in session_journal_paths(target):
        data = load_session_journal(path)
        if data.get("status") != "clean":
            unfinished.append((path, data))
    if not unfinished:
        if lock_owner is not None and lock_owner.get("operation_kind") == "service":
            console.info("A stack está inicializando; nenhum processo foi registrado ainda.")
        elif lock_owner is not None:
            console.info("Não há serviços registrados durante esta operação de manutenção.")
        else:
            console.success("Nenhum serviço x86QW está ativo nesta instalação.")
        if active_service:
            console.info(
                "Para encerrar: Serviços › Encerrar serviços ativos ou status --stop."
            )
        return active_service

    lock_session = str(lock_owner["session_id"]) if lock_owner is not None else None
    for _path, data in unfinished:
        owns_lock = str(data["session_id"]) == lock_session
        if not owns_lock:
            console.warning(
                f"Sessão sem lock ativo preservada para inspeção: {data['session_id']}"
            )
        print_service_session_status(target, data, owns_lock=owns_lock)
    if active_service:
        console.info(
            "Para encerrar: Serviços › Encerrar serviços ativos ou status --stop."
        )
    return active_service


def request_service_stop(target: Path, *, timeout: float = 15.0) -> None:
    """Ask the confirmed controller to perform its own coordinated shutdown."""
    lock_path = target / ".x86qw" / "sessions" / "active.lock"
    if not lexists(lock_path):
        raise InstallerError("Nenhuma stack de serviços x86QW está ativa.")
    try:
        owner = session_control.read_lock_owner(lock_path)
    except session_control.SessionControlError as error:
        raise InstallerError(str(error)) from error
    if owner.get(_LEGACY_ACL_MIGRATED) is True:
        raise InstallerError(
            "O lock legado do Windows não possui autoridade para solicitar encerramento."
        )
    if owner.get("operation_kind", "service") != "service":
        raise InstallerError(
            f"A operação ativa é {owner['command']}, não uma stack de serviços."
        )
    probe = probe_expected_process(
        int(owner["controller_pid"]),
        str(owner["controller_start_token"]),
        str(owner["controller_executable"]),
    )
    if probe.status != "alive":
        raise InstallerError(
            "O controlador ativo não pôde ser confirmado; nenhum processo foi encerrado."
        )
    session_id = str(owner["session_id"])
    paths = session_journal_paths(target, session_id)
    if len(paths) != 1:
        raise InstallerError(
            "O journal da stack ativa não está disponível; nenhum processo foi encerrado."
        )
    journal = load_session_journal(paths[0])
    if journal.get(_LEGACY_ACL_MIGRATED) is True:
        raise InstallerError(
            "O journal legado do Windows não possui autoridade para solicitar encerramento."
        )
    controller = journal.get("controller")
    if (
        not isinstance(controller, dict)
        or controller.get("pid") != owner["controller_pid"]
        or controller.get("creation_token") != owner["controller_start_token"]
        or os.path.normcase(os.path.realpath(str(controller.get("executable"))))
        != os.path.normcase(os.path.realpath(str(owner["controller_executable"])))
    ):
        raise InstallerError(
            "Lock e journal não identificam o mesmo controlador; nada foi encerrado."
        )
    request = paths[0].parent / "stop.request"
    payload = (
        json.dumps({
            "format": 1,
            "project": "x86qw",
            "session_id": session_id,
            "requested_at": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")
    request_identity = publish_stop_request(request, payload)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not lexists(lock_path):
            unlink_stop_request(request, expected_identity=request_identity)
            return
        try:
            current = session_control.read_lock_owner(lock_path)
        except session_control.SessionControlError:
            if not lexists(lock_path):
                unlink_stop_request(request, expected_identity=request_identity)
                return
            raise
        if current.get("session_id") != session_id:
            raise InstallerError("O lock mudou de proprietário durante o encerramento.")
        time.sleep(0.1)
    raise InstallerError(
        "A stack recebeu o pedido, mas não concluiu o encerramento dentro do limite."
    )


def cleanup_current_session(
    journal: SessionJournal | None,
    temporary_paths: list[Path],
    materialized_packages: list[MaterializedKtx],
) -> None:
    temporary_records: dict[str, tuple[str, int, tuple[int, int]]] = {}
    sensitive_records: dict[str, tuple[int, int] | None] = {}
    if journal is not None:
        entries = journal.data.get("temporary_files", [])
        if isinstance(entries, list):
            temporary_records = {
                str(entry.get("path")): (
                    str(entry.get("expected_hash")), int(entry["expected_size"]),
                    (int(entry["device"]), int(entry["inode"])),
                )
                for entry in entries
                if (
                    isinstance(entry, dict)
                    and isinstance(entry.get("expected_hash"), str)
                    and type(entry.get("expected_size")) is int
                    and type(entry.get("device")) is int
                    and type(entry.get("inode")) is int
                )
            }
            sensitive_records = {
                str(entry.get("path")): (
                    (int(entry["device"]), int(entry["inode"]))
                    if type(entry.get("device")) is int
                    and type(entry.get("inode")) is int
                    else None
                )
                for entry in entries
                if isinstance(entry, dict) and entry.get("sensitive") is True
            }
    try:
        for path in temporary_paths:
            relative = journal._relative(path) if journal is not None else ""
            expected = temporary_records.get(relative)
            if relative in sensitive_records and journal is not None:
                journal.release_sensitive_temporary(path)
            if not lexists(path):
                continue
            if relative in sensitive_records:
                if not cleanup_sensitive_temporary(
                    path, sensitive_records[relative],
                ):
                    console.warning(
                        f"Temporário sensível substituído foi preservado: {path}"
                    )
                    raise InstallerError(
                        "Temporário sensível sem identidade original confirmável foi "
                        f"preservado: {path}"
                    )
            elif expected is not None and journal is not None:
                entry = MaterializedFile(
                    path,
                    expected[0],
                    "temporário de sessão",
                    True,
                    False,
                    journal.target,
                    expected[2],
                    expected[1],
                )
                if not cleanup_materialized_file(entry):
                    console.warning(f"Arquivo temporário alterado foi preservado: {path}")
            elif journal is None:
                unlink_sensitive_temporary(path)
            else:
                console.warning(f"Arquivo temporário alterado foi preservado: {path}")
    finally:
        if journal is not None:
            journal.release_all_sensitive_temporaries()
    for materialized in reversed(materialized_packages):
        if journal is not None:
            for entry in materialized.files:
                if (
                    entry.created_by_session
                    and lexists(entry.path)
                    and not (
                        entry.expected_size is not None
                        and entry.path.is_file()
                        and not entry.path.is_symlink()
                        and file_matches_sha256(
                            entry.path, entry.expected_hash, entry.expected_size,
                        )
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
    architecture_aliases = core.CAPABILITY_CATALOG.get("architecture_aliases")
    if not isinstance(architecture_aliases, dict):
        raise InstallerError("Catálogo de arquiteturas da CLI está inválido.")
    return host_adapter.service_runtime_variant(
        core.RUNTIMES,
        architecture_aliases,
        core.HOST_PLATFORMS,
        runtime_id=runtime_id,
        system=system,
        machine=machine,
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
    return host_adapter.service_runtime_executable(binary)


def runtime_launch_target(
    installer: core.Installer, component: str,
) -> host_adapter.LaunchTarget:
    """Bind a service executable to the inventory entry verified for it."""

    binary = runtime_binary(installer, component)
    present, entries, _receipt = installer.validate_component_pair(component)
    if not present:
        raise InstallerError(
            f"O inventário do componente {component} não pôde ser validado."
        )
    try:
        relative = binary.relative_to(installer.target).as_posix()
    except ValueError as error:
        raise InstallerError(
            f"Executável {component} fora da instalação gerenciada: {binary}"
        ) from error
    expected_sha256 = dict(entries).get(relative)
    if expected_sha256 is None:
        raise InstallerError(
            f"O inventário de {component} não registra seu executável: {relative}"
        )
    return host_adapter.executable_launch_target(
        binary, expected_sha256=expected_sha256,
    )


def temporary_config(
    directory: Path,
    prefix: str,
    lines: list[str | bytes],
    journal: SessionJournal | None = None,
    *,
    sensitive: bool = True,
) -> Path:
    if not directory.is_dir() or directory.is_symlink():
        raise InstallerError(f"Diretório de configuração ausente ou inseguro: {directory}")
    payload = bytearray(
        "// x86QW: configuração efêmera removida ao encerrar.\n".encode("utf-8")
    )
    for line in lines:
        payload.extend(line.encode("utf-8") if isinstance(line, str) else line)
        payload.extend(b"\n")
    expected_hash = hashlib.sha256(payload).hexdigest()
    try:
        descriptor, path = private_fs.private_mkstemp(
            prefix=prefix, suffix=".cfg", directory=directory,
        )
    except OSError as error:
        raise InstallerError(
            "Não foi possível criar a configuração efêmera com acesso privado."
        ) from error
    tracked: MaterializedFile | None = None
    reservation: MaterializedFile | None = None
    sensitive_guard: object | None = None
    try:
        with os.fdopen(descriptor, "wb") as output:
            identity = persistent_descriptor_identity(
                output.fileno(), directory=False,
            )
            reservation = MaterializedFile(
                path,
                hashlib.sha256(b"").hexdigest(),
                "reserva de configuração efêmera",
                True,
                False,
                journal.target if journal is not None else directory,
                identity,
                0,
            )
            if journal is not None:
                journal.record_temporary_intent(
                    path,
                    "configuração efêmera redigida",
                    sensitive=sensitive,
                    identity=identity,
                    expected_hash=None if sensitive else expected_hash,
                    expected_size=None if sensitive else len(payload),
                )
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        private_fs.validate_private_file(path)
        if sensitive and journal is not None:
            sensitive_guard = private_fs.hold_private_path(path, directory=False)
        if not sensitive:
            tracked = describe_non_sensitive_temporary(
                path,
                journal.target if journal is not None else directory,
                "configuração efêmera redigida",
            )
        if journal is not None:
            journal.record_temporary(
                path,
                "configuração efêmera redigida",
                sensitive=sensitive,
                tracked=tracked,
            )
            if sensitive_guard is not None:
                journal.hold_sensitive_temporary(path, sensitive_guard)
                sensitive_guard = None
        return path
    except BaseException as primary_error:
        if sensitive_guard is not None:
            try:
                sensitive_guard.close()
            except BaseException as guard_error:
                console.warning(
                    "Falha ao liberar a proteção da configuração efêmera após erro; "
                    f"o erro original foi preservado: {guard_error}"
                )
        if lexists(path):
            try:
                if sensitive:
                    if not cleanup_sensitive_temporary(
                        path, reservation.identity if reservation is not None else None,
                    ):
                        console.warning(
                            f"Temporário sensível substituído foi preservado: {path}"
                        )
                elif tracked is not None:
                    if not cleanup_materialized_file(tracked):
                        console.warning(
                            f"Temporário não sensível alterado foi preservado: {path}"
                        )
                elif reservation is not None:
                    if not cleanup_materialized_file(reservation):
                        console.warning(
                            f"Temporário não sensível alterado foi preservado: {path}"
                        )
                else:
                    console.warning(
                        f"Temporário não sensível sem identidade foi preservado: {path}"
                    )
            except BaseException as cleanup_error:
                console.warning(
                    "Falha ao limpar configuração efêmera após erro operacional; "
                    f"o erro original foi preservado: {cleanup_error}"
                )
        raise


def ktx_assets(target: Path) -> frozenset[str]:
    package = target / "qw" / "ktx.pk3"
    if not package.is_file() or package.is_symlink():
        raise InstallerError(f"Pacote KTX ausente ou inseguro: {package}")
    try:
        plan = scan_archive(package)
        managed = frozenset(
            member.path.as_posix().casefold()
            for member in plan.members
            if member.kind == "file"
        )
        return managed | gameplay.ktx_external_assets(target)
    except (ArchiveError, OSError) as error:
        raise InstallerError(f"Pacote KTX inválido: {package}") from error


def materialize_dedicated_pk3(
    package: Path,
    destination_root: Path,
    label: str,
    journal: SessionJournal | None = None,
) -> MaterializedKtx:
    """Expose verified PK3 members through the canonical runtime boundary."""

    materialized = materialize_archive(
        package,
        destination_root,
        label,
        journal,
        warning=console.warning,
    )
    return MaterializedKtx(
        materialized.files,
        materialized.directories,
        materialized.root,
    )


def materialize_dedicated_ktx(target: Path) -> MaterializedKtx:
    return materialize_dedicated_pk3(target / "qw/ktx.pk3", target / "qw", "KTX")


def cleanup_dedicated_ktx(materialized: MaterializedKtx) -> None:
    cleanup_materialized_archive(materialized, warning=console.warning)


def select_hosted_game(
    player: gameplay.Player,
    options: argparse.Namespace,
    *,
    initial: HostedGame | None = None,
    start_state: str = "game",
) -> HostedGame | None:
    player.check_paks()
    games = player.available_local_games()
    if not games:
        raise InstallerError(
            "Nenhum mod gerenciado está instalado. Execute o bootstrap e selecione um jogo."
        )
    game = initial.game if initial is not None else None
    mode = initial.mode if initial is not None else None
    map_name = initial.map_name if initial is not None else None
    assets = initial.assets if initial is not None else frozenset()
    launch_options = initial.ktx_options if initial is not None else options.ktx_options
    base_launch_options = options.ktx_options
    previous_mode_key = mode.key if mode is not None else None
    state = start_state
    while True:
        if state == "game":
            game = player.choose_local_game(games, options.game, activity="hospedar")
            if game is None:
                return None
            component = player.installed_component_for_game(game)
            if component is None:
                raise InstallerError(f"O componente de {game.label} não está mais instalado.")
            player.verify_component(component)
            player.verify_local_play_support(games)
            mode = None
            map_name = None
            assets = frozenset()
            launch_options = base_launch_options
            previous_mode_key = None
            state = "mode" if game.mode_catalog is not None else "map"
            continue
        assert game is not None
        if state == "mode":
            mode = player.choose_ktx_mode(
                gameplay.load_ktx_modes(player.project_root),
                options.mode,
                activity="hospedar",
            )
            if mode is None:
                state = "game"
                continue
            if previous_mode_key is not None and previous_mode_key != mode.key:
                launch_options = base_launch_options
            previous_mode_key = mode.key
            console.success(f"Modo KTX selecionado: {mode.label}.")
            state = "options" if options.menu else "map"
            continue
        if state == "options":
            assert mode is not None
            chosen = player.choose_ktx_launch_options(
                mode, launch_options, activity="Hospedar",
            )
            if chosen is None:
                state = "mode"
                continue
            launch_options = chosen
            state = "map"
            continue
        if game.mode_catalog is not None:
            assert mode is not None
            resolved = gameplay.resolve_frogbot_name_profile(
                player.project_root, player.target, game, launch_options, mode,
            )
            assets = ktx_assets(player.target)
            required_assets = gameplay.required_ktx_map_assets(mode, resolved)
            map_name = player.choose_local_map(
                game,
                default_map=mode.default_map,
                suggested_maps=mode.suggested_maps,
                label=f"KTX · {mode.label}",
                requested_map=options.map,
                required_assets=required_assets,
                available_assets=assets,
                breadcrumb=f"x86QW › Hospedar › KTX › {mode.label} › Mapa",
            )
            if map_name is None:
                state = "options" if options.menu else "mode"
                continue
            launch_options = resolved
        else:
            map_name = player.choose_local_map(
                game,
                requested_map=options.map,
                breadcrumb=f"x86QW › Hospedar › {game.label} › Mapa",
            )
            if map_name is None:
                state = "game"
                continue
        return HostedGame(game, mode, map_name, assets, launch_options)


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
) -> tuple[tuple[str, str | bytes], ...]:
    # Reuse the client launch validator, then translate supported choices into
    # server cvars because MVDSV has no client aliases or client-command entity.
    gameplay.ktx_launch_commands(mode, map_name, assets, options)
    settings: list[tuple[str, str | bytes]] = list(DEDICATED_MODE_CVARS.get(mode.key, ()))
    if gameplay.ktx_bot_options_requested(options):
        target_clients = gameplay.requested_frogbot_names(options, mode) + 1
        if target_clients > maxclients:
            raise InstallerError(
                f"{mode.label} com os Frogbots selecionados exige --maxclients "
                f"de pelo menos {target_clients}."
            )
        settings.extend((
            ("k_fb_enabled", "1"),
            ("k_fb_skill", "5" if options.bot_skill == "random" else str(options.bot_skill)),
            ("k_fb_skill_random", "1" if options.bot_skill == "random" else "0"),
            ("k_fb_autoadd_limit", str(target_clients)),
            ("k_fb_autoremove_at", str(target_clients)),
        ))
        settings.extend(gameplay.ktx_bot_name_binary_settings(options, mode))
        if options.bot_weapon is not None:
            settings.append((
                "k_fb_weapon", "0" if options.bot_weapon == "random" else options.bot_weapon,
            ))
        if options.bot_health is not None:
            settings.append(("k_fb_health", str(options.bot_health)))
        if options.bot_break_on_death is not None:
            settings.append((
                "k_fb_break_on_death", "1" if options.bot_break_on_death else "0",
            ))
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
    launch_target = runtime_launch_target(installer, "mvdsv")
    binary = launch_target.executable
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
    post_map_settings: tuple[tuple[str, str | bytes], ...] = ()
    if game.mode_catalog is not None:
        assert mode is not None
        post_map_settings = dedicated_ktx_settings(
            mode, map_name, selection.assets, selection.ktx_options, options.maxclients,
        )
        if (
            gameplay.ktx_bot_options_requested(selection.ktx_options)
            and is_external_bind(options.bind)
        ):
            post_map_settings = (*post_map_settings, ("k_fb_admin_only", "1"))
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
        if any(name == "k_fb_admin_only" for name, _value in post_map_settings):
            # Remote players and spectators must not be able to mutate the
            # Frogbot roster of an externally reachable host.
            lines.append("k_fb_admin_only 1")
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
            *(
                b"set " + name.encode("ascii") + b' "' + value + b'"'
                if isinstance(value, bytes)
                else f"set {name} {value}"
                for name, value in post_map_settings
            ),
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
    parameters: list[tuple[str, str]] = [
        ("game", game.label),
        ("map", map_name),
        ("hostname", hostname),
        ("bind", options.bind),
        ("port", str(options.port)),
        ("maxclients", str(options.maxclients)),
        ("memory", "64 MiB"),
        ("mvd", "desativada" if options.no_mvd else "automática"),
    ]
    if mode is not None:
        parameters.insert(1, ("mode", mode.label))
        ktx_options = selection.ktx_options
        if ktx_options.fill_bots:
            parameters.append(("bots", "preencher servidor"))
        else:
            parameters.append(("bots", str(ktx_options.bots)))
        if ktx_options.bots or ktx_options.fill_bots:
            parameters.extend((
                ("bot_skill", str(ktx_options.bot_skill)),
                ("bot_names", {
                    "default": "KTX Default",
                    "x86qw": "x86QW aleatório",
                    "personal": "lista pessoal",
                }.get(ktx_options.bot_names_profile, ktx_options.bot_names_profile)),
            ))
            if ktx_options.bot_team is not None:
                parameters.append(("bot_team", ktx_options.bot_team))
            if ktx_options.bot_weapon is not None:
                parameters.append(("bot_weapon", ktx_options.bot_weapon))
            if ktx_options.bot_health is not None:
                parameters.append(("bot_health", str(ktx_options.bot_health)))
        if ktx_options.ctf_hook is not None:
            parameters.append(("ctf_hook", ktx_options.ctf_hook))
        if ktx_options.ctf_runes is not None:
            parameters.append(("ctf_runes", ktx_options.ctf_runes))
        if ktx_options.race_style is not None:
            parameters.append(("race_style", ktx_options.race_style))
        if ktx_options.race_scoring is not None:
            parameters.append(("race_scoring", ktx_options.race_scoring))
    configured_secrets = [
        label for label, value in (
            ("jogadores", options.password),
            ("espectadores", options.spectator_password),
            ("RCON", options.rcon_password),
        ) if value
    ]
    parameters.append((
        "secrets", ", ".join(configured_secrets) if configured_secrets else "nenhum",
    ))
    return ProcessSpec(
        "MVDSV", tuple(arguments), installer.target, startup_rcon,
        parameters=tuple(parameters), launch_target=launch_target,
    )


def proxy_spec(installer: core.Installer, options: argparse.Namespace) -> ProcessSpec:
    launch_target = runtime_launch_target(installer, "qwfwd")
    binary = launch_target.executable
    directory = installer.target / "qwfwd"
    config = directory / "qwfwd.cfg"
    if not config.is_file() or config.is_symlink():
        raise InstallerError(f"Configuração QWFWD ausente ou insegura: {config}")
    return ProcessSpec(
        "QWFWD", (str(binary), str(options.proxy_port), options.proxy_bind), directory,
        readiness=ServiceReadiness("udp", local_service_address(options.proxy_bind), options.proxy_port),
        parameters=(
            ("bind", options.proxy_bind),
            ("port", str(options.proxy_port)),
            ("protocol", "UDP QuakeWorld"),
        ),
        launch_target=launch_target,
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
    launch_target = runtime_launch_target(installer, "qtv")
    binary = launch_target.executable
    directory = installer.target / "qtv"
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
        parameters=(
            ("hostname", hostname),
            ("bind", bind),
            ("port", str(port)),
            ("http", f"http://{endpoint(local_service_address(bind), port)}/"),
            ("upstream", upstream or "nenhum"),
            ("upstream_secret", "configurado" if password else "não configurado"),
        ),
        launch_target=launch_target,
    )


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


def add_target(parser: argparse.ArgumentParser, project_root: Path) -> None:
    parser.add_argument(
        "--target", type=Path, default=project_root / "quake-world",
        help="diretório da instalação x86QW",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="mostra detalhes técnicos")
    parser.add_argument("--no-color", action="store_true", help="desativa cores")
    parser.add_argument("--menu", action="store_true", help=argparse.SUPPRESS)


def add_background_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--background", action="store_true",
        help="mantém a stack em segundo plano após confirmar a inicialização",
    )
    parser.add_argument("--background-child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--background-log", help=argparse.SUPPRESS)


def menu_text(
    prompt: str, default: str, validator: object, *, allow_back: bool = False,
) -> object | None:
    while True:
        try:
            answer = input(f"{prompt} (padrão: {default}): ").strip() or default
        except EOFError as error:
            raise InstallerError(f"Nenhum valor foi informado para {prompt.casefold()}.") from error
        if allow_back and answer.casefold() in {"b", "voltar"}:
            return None
        if answer.casefold() in {"q", "sair"}:
            raise navigation.MenuExit(prompt)
        try:
            return validator(answer)  # type: ignore[operator]
        except (ValueError, argparse.ArgumentTypeError) as error:
            console.warning(str(error))


def menu_bind(
    label: str,
    current: str = "127.0.0.1",
    *,
    breadcrumb: str | None = None,
) -> str | None:
    known = ("127.0.0.1", "0.0.0.0", "::")
    default = known.index(current) if current in known else 3
    selected = navigation.select_one(
        f"Interface de rede do {label}",
        (
            navigation.MenuOption("loopback", "Somente este computador", "127.0.0.1"),
            navigation.MenuOption("lan", "Todas as interfaces IPv4", "0.0.0.0 · revise senhas e firewall"),
            navigation.MenuOption("ipv6", "Todas as interfaces IPv6", ":: · revise senhas e firewall"),
            navigation.MenuOption("custom", "Endereço personalizado", "IPv4 ou IPv6 específico"),
        ),
        breadcrumb=breadcrumb or f"x86QW › Serviços › {label} › Rede",
        default=default,
        allow_back=True,
    )
    if selected is None:
        return None
    if selected == "custom":
        value = menu_text("Endereço IP", current, bind_address, allow_back=True)
        return None if value is None else str(value)
    return {"loopback": "127.0.0.1", "lan": "0.0.0.0", "ipv6": "::"}.get(
        str(selected), current,
    )


def menu_port(
    label: str,
    current: int,
    *,
    breadcrumb: str | None = None,
) -> int | None:
    selected = navigation.select_one(
        f"Porta do {label}",
        (
            navigation.MenuOption("default", str(current), "porta padrão"),
            navigation.MenuOption("custom", "Outra porta", "escolher entre 1024 e 65535"),
        ),
        breadcrumb=breadcrumb or f"x86QW › Serviços › {label} › Rede",
        allow_back=True,
    )
    if selected is None:
        return None
    if selected == "custom":
        value = menu_text(
            "Porta", str(current), bounded_integer(1024, 65535), allow_back=True,
        )
        return None if value is None else int(value)
    return current


def menu_execution_mode(options: argparse.Namespace, breadcrumb: str) -> bool:
    selected = navigation.select_one(
        "Como manter o serviço?",
        (
            navigation.MenuOption(
                "foreground", "Primeiro plano", "terminal acompanha a execução",
                "Ctrl+C encerra a stack coordenadamente.",
            ),
            navigation.MenuOption(
                "background", "Segundo plano", "liberar o terminal após a inicialização",
                "Consulte e encerre pela área Serviços.",
            ),
        ),
        breadcrumb=breadcrumb + " › Execução",
        default=1 if options.background else 0,
        allow_back=True,
    )
    if selected is None:
        return False
    options.background = selected == "background"
    return True


def configure_service_menu(options: argparse.Namespace) -> bool:
    if not options.menu:
        return True
    if options.action == "proxy":
        while True:
            bind = menu_bind("QWFWD", options.proxy_bind)
            if bind is None:
                return False
            options.proxy_bind = bind
            port = menu_port("QWFWD", options.proxy_port)
            if port is None:
                continue
            options.proxy_port = port
            break
        return menu_execution_mode(options, "x86QW › Serviços › QWFWD")
    if options.action == "qtv":
        stage = 0
        while stage < 5:
            if stage == 0:
                bind = menu_bind("QTV", options.bind)
                if bind is None:
                    return False
                options.bind = bind
            elif stage == 1:
                port = menu_port("QTV", options.port)
                if port is None:
                    stage -= 1
                    continue
                options.port = port
            elif stage == 2:
                upstream = navigation.select_one(
                    "Origem da transmissão",
                    (
                        navigation.MenuOption("none", "Sem upstream inicial", "iniciar QTV isoladamente"),
                        navigation.MenuOption("custom", "Conectar a um MVDSV", "informar host e porta"),
                    ),
                    breadcrumb="x86QW › Serviços › QTV",
                    default=1 if options.upstream else 0,
                    allow_back=True,
                )
                if upstream is None:
                    stage -= 1
                    continue
                if upstream == "custom":
                    endpoint_value = menu_text(
                        "Endpoint MVDSV", "127.0.0.1:28501", parse_network_endpoint,
                        allow_back=True,
                    )
                    if endpoint_value is None:
                        continue
                    options.upstream = str(endpoint_value)
                else:
                    options.upstream = None
                    stage = 4
                    continue
            elif stage == 3:
                prompt = navigation.confirm(
                    "Solicitar senha do upstream?",
                    breadcrumb="x86QW › Serviços › QTV › Segurança",
                    default=False,
                    allow_back=True,
                )
                if prompt is None:
                    stage -= 1
                    continue
                options.prompt_qtv_password = prompt
            else:
                if not menu_execution_mode(options, "x86QW › Serviços › QTV"):
                    stage = 3 if options.upstream else 2
                    continue
            stage += 1
        return True

    return True


def configure_advanced_host_menu(options: argparse.Namespace) -> bool:
    """Collect advanced host settings; False returns to the profile choice."""

    stage = 0
    while stage < 12:
        if stage == 0:
            bind = menu_bind(
                "MVDSV", options.bind,
                breadcrumb="x86QW › Hospedar › Avançado › MVDSV › Rede",
            )
            if bind is None:
                return False
            options.bind = bind
        elif stage == 1:
            port = menu_port(
                "MVDSV", options.port,
                breadcrumb="x86QW › Hospedar › Avançado › MVDSV › Rede",
            )
            if port is None:
                stage -= 1
                continue
            options.port = port
        elif stage == 2:
            clients = navigation.select_one(
                "Máximo de clientes",
                (
                    navigation.MenuOption("8", "8", "partida pequena"),
                    navigation.MenuOption("16", "16", "padrão recomendado"),
                    navigation.MenuOption("24", "24", "servidor grande"),
                    navigation.MenuOption("32", "32", "limite atual"),
                ),
                breadcrumb="x86QW › Hospedar › Capacidade",
                default=1,
                allow_back=True,
            )
            if clients is None:
                stage -= 1
                continue
            options.maxclients = int(clients)
        elif stage == 3:
            record = navigation.confirm(
                "Gravar demos MVD automaticamente?",
                breadcrumb="x86QW › Hospedar › Gravação", default=True, allow_back=True,
            )
            if record is None:
                stage -= 1
                continue
            options.no_mvd = not record
        elif stage == 4:
            with_qtv = navigation.confirm(
                "Iniciar QTV conectado ao servidor?",
                breadcrumb="x86QW › Hospedar › Serviços adicionais",
                default=False, allow_back=True,
            )
            if with_qtv is None:
                stage -= 1
                continue
            options.with_qtv = with_qtv
            if not with_qtv:
                stage = 6
        elif stage == 5:
            qtv_bind = menu_bind(
                "QTV", options.qtv_bind,
                breadcrumb="x86QW › Hospedar › Avançado › QTV › Rede",
            )
            if qtv_bind is None:
                stage -= 1
                continue
            options.qtv_bind = qtv_bind
        elif stage == 6:
            if options.with_qtv:
                qtv_port = menu_port(
                    "QTV", options.qtv_port,
                    breadcrumb="x86QW › Hospedar › Avançado › QTV › Rede",
                )
                if qtv_port is None:
                    stage -= 1
                    continue
                options.qtv_port = qtv_port
        elif stage == 7:
            with_proxy = navigation.confirm(
                "Iniciar também o proxy QWFWD?",
                breadcrumb="x86QW › Hospedar › Serviços adicionais",
                default=False, allow_back=True,
            )
            if with_proxy is None:
                stage = 6 if options.with_qtv else 4
                continue
            options.with_proxy = with_proxy
            if not with_proxy:
                stage = 9
        elif stage == 8:
            proxy_bind = menu_bind(
                "QWFWD", options.proxy_bind,
                breadcrumb="x86QW › Hospedar › Avançado › QWFWD › Rede",
            )
            if proxy_bind is None:
                stage -= 1
                continue
            options.proxy_bind = proxy_bind
        elif stage == 9:
            if options.with_proxy:
                proxy_port = menu_port(
                    "QWFWD", options.proxy_port,
                    breadcrumb="x86QW › Hospedar › Avançado › QWFWD › Rede",
                )
                if proxy_port is None:
                    stage -= 1
                    continue
                options.proxy_port = proxy_port
        elif stage == 10:
            passwords = navigation.confirm(
                "Configurar senhas com entrada oculta?",
                breadcrumb="x86QW › Hospedar › Segurança",
                description="As senhas não serão colocadas na linha de comando.",
                default=False, allow_back=True,
            )
            if passwords is None:
                stage = 9 if options.with_proxy else 7
                continue
            if passwords:
                options.prompt_password = True
                options.prompt_spectator_password = True
                options.prompt_rcon_password = True
                if options.with_qtv:
                    options.prompt_qtv_password = True
        else:
            if not menu_execution_mode(options, "x86QW › Hospedar › Avançado"):
                stage = 10
                continue
        stage += 1
    return True


def apply_quick_host_defaults(options: argparse.Namespace) -> None:
    """Apply the safe, local-only host profile without retaining advanced choices."""
    options.bind = "127.0.0.1"
    options.port = 28501
    options.maxclients = 16
    options.no_mvd = False
    options.with_qtv = False
    options.qtv_bind = "127.0.0.1"
    options.qtv_port = 28000
    options.with_proxy = False
    options.proxy_bind = "127.0.0.1"
    options.proxy_port = 30000
    options.background = False
    for name in (
        "prompt_password", "prompt_spectator_password",
        "prompt_rcon_password", "prompt_qtv_password",
    ):
        setattr(options, name, False)
    for name in ("password", "spectator_password", "rcon_password", "qtv_password"):
        setattr(options, name, "")
    for name in (
        "password_file", "spectator_password_file",
        "rcon_password_file", "qtv_password_file",
    ):
        setattr(options, name, None)


def choose_host_configuration(
    options: argparse.Namespace, current: str | None = None,
) -> str | None:
    profile = navigation.select_one(
        "Como deseja configurar o servidor?",
        (
            navigation.MenuOption(
                "quick", "Rápido local", "padrões seguros em 127.0.0.1",
                "MVD ativo, sem QTV/QWFWD e sem exposição à rede",
            ),
            navigation.MenuOption(
                "advanced", "Avançado", "rede, portas, capacidade e serviços",
                "controle completo de MVDSV, QTV, QWFWD e senhas",
            ),
        ),
        breadcrumb="x86QW › Hospedar › Configuração",
        default=1 if current == "advanced" else 0,
        allow_back=True,
    )
    if profile == "quick":
        apply_quick_host_defaults(options)
    return profile


def host_command_arguments(
    selection: HostedGame, options: argparse.Namespace,
) -> list[str]:
    arguments = ["host", selection.game.key]
    if selection.mode is not None:
        arguments.extend(["--mode", selection.mode.key])
        arguments.extend(gameplay.ktx_options_cli_arguments(selection.ktx_options))
    arguments.extend(["--map", selection.map_name])
    arguments.extend(["--bind", options.bind, "--port", str(options.port)])
    arguments.extend(["--maxclients", str(options.maxclients)])
    if options.hostname:
        arguments.extend(["--hostname", options.hostname])
    if options.no_mvd:
        arguments.append("--no-mvd")
    if options.with_qtv:
        arguments.extend([
            "--with-qtv", "--qtv-bind", options.qtv_bind,
            "--qtv-port", str(options.qtv_port),
        ])
    if options.with_proxy:
        arguments.extend([
            "--with-proxy", "--proxy-bind", options.proxy_bind,
            "--proxy-port", str(options.proxy_port),
        ])
    if options.background:
        arguments.append("--background")
    for enabled, flag in (
        (options.prompt_password, "--prompt-password"),
        (options.prompt_spectator_password, "--prompt-spectator-password"),
        (options.prompt_rcon_password, "--prompt-rcon-password"),
        (options.prompt_qtv_password, "--prompt-qtv-password"),
    ):
        if enabled:
            arguments.append(flag)
    return arguments


def service_command_arguments(options: argparse.Namespace) -> list[str]:
    """Return a reproducible command without putting secrets in process arguments."""
    if options.action == "proxy":
        arguments = [
            "proxy", "--bind", options.proxy_bind,
            "--port", str(options.proxy_port),
        ]
        if options.background:
            arguments.append("--background")
        return arguments
    arguments = [
        "qtv", "--bind", options.bind, "--port", str(options.port),
        "--hostname", options.hostname,
    ]
    if options.upstream is not None:
        arguments.extend(["--upstream", str(options.upstream)])
    if (
        options.qtv_password
        or options.prompt_qtv_password
        or options.qtv_password_file is not None
    ):
        arguments.append("--prompt-qtv-password")
    if options.background:
        arguments.append("--background")
    return arguments


def service_summary_text(options: argparse.Namespace) -> str:
    if options.action == "proxy":
        lines = (
            "Resumo do serviço",
            "  Serviço   | QWFWD",
            f"  Endpoint  | {endpoint(options.proxy_bind, options.proxy_port)}/UDP",
            "  Protocolo | proxy UDP QuakeWorld",
        )
    else:
        secret = bool(
            options.qtv_password
            or options.prompt_qtv_password
            or options.qtv_password_file is not None
        )
        lines = (
            "Resumo do serviço",
            "  Serviço   | QTV",
            f"  Nome      | {options.hostname}",
            f"  HTTP      | http://{endpoint(options.bind, options.port)}/",
            f"  Upstream  | {options.upstream or 'isolado; sem origem inicial'}",
            "  Segurança | " + (
                "segredo do upstream configurado; valor redigido"
                if secret else "sem segredo de upstream"
            ),
        )
    return "\n".join((
        *lines,
        f"  Execução  | {'segundo plano' if options.background else 'primeiro plano'}",
        "",
        "Comando equivalente seguro:",
        "  " + gameplay.public_command(service_command_arguments(options)),
    ))


def host_summary_text(
    selection: HostedGame, options: argparse.Namespace, profile: str,
) -> str:
    lines = ["Resumo da hospedagem", f"  Jogo       | {selection.game.label}"]
    if selection.mode is not None:
        lines.append(f"  Modo       | {selection.mode.label}")
        for line in gameplay.ktx_summary_lines(selection.ktx_options):
            label, value = line.split("|", 1)
            lines.append(f"  {label.strip():<11}|{value}")
    lines.extend((
        f"  Mapa       | {selection.map_name}",
        f"  Perfil     | {'Rápido local' if profile == 'quick' else 'Avançado'}",
        f"  Execução   | {'segundo plano' if options.background else 'primeiro plano'}",
        f"  MVDSV      | {endpoint(options.bind, options.port)} · {options.maxclients} clientes",
        f"  Gravação   | {'desativada' if options.no_mvd else 'MVD automática'}",
        f"  QTV        | {'ativo em ' + endpoint(options.qtv_bind, options.qtv_port) if options.with_qtv else 'desativado'}",
        f"  QWFWD      | {'ativo em ' + endpoint(options.proxy_bind, options.proxy_port) if options.with_proxy else 'desativado'}",
    ))
    secret_count = sum(bool(value) for value in (
        options.password, options.spectator_password,
        options.rcon_password, options.qtv_password,
    )) + sum(bool(getattr(options, name, False)) for name in (
        "prompt_password", "prompt_spectator_password",
        "prompt_rcon_password", "prompt_qtv_password",
    ))
    lines.extend((
        f"  Segurança  | {secret_count} segredo(s) configurado(s); valores redigidos",
        "",
        "Comando equivalente seguro:",
        "  " + gameplay.public_command(host_command_arguments(selection, options)),
    ))
    return "\n".join(lines)


def print_host_summary(
    selection: HostedGame, options: argparse.Namespace, profile: str,
) -> None:
    print("\n" + host_summary_text(selection, options, profile))


def background_controller_arguments(
    options: argparse.Namespace, selection: HostedGame | None, log_relative: str,
) -> list[str]:
    if options.action == "host":
        assert selection is not None
        arguments = host_command_arguments(selection, options)
    else:
        arguments = service_command_arguments(options)
    internal_only = {
        "--background", "--prompt-password", "--prompt-spectator-password",
        "--prompt-rcon-password", "--prompt-qtv-password",
    }
    arguments = [argument for argument in arguments if argument not in internal_only]
    arguments.extend([
        "--target", str(options.target),
        "--background-child", "--background-log", log_relative,
    ])
    if options.verbose:
        arguments.append("--verbose")
    if options.no_color:
        arguments.append("--no-color")
    return arguments


def background_entrypoint() -> Path:
    if core.ZIPAPP_PATH is not None:
        return core.ZIPAPP_PATH
    return core.PROJECT_ROOT / "dist/installer/bin/manager.py"


def read_background_request(options: argparse.Namespace) -> None:
    try:
        payload = sys.stdin.buffer.read(65537)
        if len(payload) > 65536:
            raise ValueError("pedido excede o limite")
        data = json.loads(payload.decode("utf-8"))
        if (
            not isinstance(data, dict)
            or set(data) != {"format", "project", "secrets"}
            or data.get("format") != 1
            or data.get("project") != "x86qw"
            or not isinstance(data.get("secrets"), dict)
            or set(data["secrets"]) != set(BACKGROUND_SECRET_FIELDS)
            or not all(
                isinstance(data["secrets"].get(name), str)
                and len(data["secrets"][name]) <= 4096
                for name in BACKGROUND_SECRET_FIELDS
            )
        ):
            raise ValueError("pedido inválido")
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise InstallerError(
            "O controlador em segundo plano não recebeu uma configuração privada válida."
        ) from error
    for name in BACKGROUND_SECRET_FIELDS:
        setattr(options, name, data["secrets"][name])
        setattr(options, f"prompt_{name}", False)
        setattr(options, f"{name}_file", None)
    options.background = True


def background_log_tail(path: Path, secrets_to_redact: tuple[str, ...]) -> str:
    if not path.is_file() or path.is_symlink():
        return ""
    try:
        lines = private_fs.read_private_file(
            path, maximum_size=1024 * 1024,
        ).decode("utf-8", errors="replace").splitlines()[-8:]
    except OSError:
        return ""
    rendered = "\n".join(
        "".join(character if character.isprintable() else "?" for character in line)
        for line in lines
    )
    for secret in secrets_to_redact:
        if secret:
            rendered = rendered.replace(secret, "[REDIGIDO]")
    return rendered


def launch_background_controller(
    options: argparse.Namespace, selection: HostedGame | None,
) -> int:
    token = secrets.token_hex(8)
    log_relative = f".x86qw/logs/service-{token}.log"
    log_path = options.target.joinpath(*PurePosixPath(log_relative).parts)
    arguments = background_controller_arguments(options, selection, log_relative)
    command = (sys.executable, str(background_entrypoint()), *arguments)
    request = (
        json.dumps({
            "format": 1,
            "project": "x86qw",
            "secrets": {
                name: str(getattr(options, name, "") or "")
                for name in BACKGROUND_SECRET_FIELDS
            },
        }, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")
    try:
        process = spawn_background_controller(
            command, core.PROJECT_ROOT, request,
        )
    except OSError as error:
        raise InstallerError(
            f"Não foi possível criar o controlador em segundo plano: {error}"
        ) from error
    deadline = time.monotonic() + BACKGROUND_START_TIMEOUT
    lock_path = options.target / ".x86qw" / "sessions" / "active.lock"
    while time.monotonic() < deadline:
        code = process.poll()
        if code is not None:
            tail = background_log_tail(
                log_path,
                tuple(str(getattr(options, name, "") or "") for name in BACKGROUND_SECRET_FIELDS),
            )
            detail = f"\n{tail}" if tail else ""
            raise InstallerError(
                f"O controlador em segundo plano encerrou com código {code}.{detail}"
            )
        if lexists(lock_path):
            try:
                owner = session_control.read_lock_owner(lock_path)
                if owner.get("controller_pid") == process.pid:
                    paths = session_journal_paths(options.target, str(owner["session_id"]))
                    if len(paths) == 1:
                        journal = load_session_journal(paths[0])
                        if journal.get("status") == "running":
                            console.success(
                                "Stack iniciada em segundo plano; consulte em Serviços › "
                                "Visualizar serviços ativos."
                            )
                            console.info(f"Controlador: PID {process.pid}")
                            console.info(f"Log: {log_relative}")
                            return 0
            except (InstallerError, session_control.SessionControlError):
                pass
        time.sleep(0.1)
    console.warning(
        "A stack continua inicializando em segundo plano; use status para acompanhar."
    )
    console.info(f"Controlador: PID {process.pid}")
    return 0


def parse_arguments(arguments: list[str], project_root: Path) -> argparse.Namespace:
    parser = FriendlyArgumentParser(
        prog="x86qw", description="Hospeda jogos e executa os serviços QuakeWorld do x86QW.",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    status = subparsers.add_parser(
        "status",
        help="mostra todos os serviços ativos e seus parâmetros não sensíveis",
        description=(
            "Consulta a stack x86QW sem alterar processos; --stop solicita "
            "encerramento coordenado explicitamente."
        ),
        add_help=False,
    )
    status._optionals.title = "opções"
    status.add_argument("-h", "--help", action="help", help="mostra esta ajuda e encerra")
    add_target(status, project_root)
    status.add_argument(
        "--stop", action="store_true",
        help="solicita encerramento coordenado da stack ativa",
    )
    status.add_argument(
        "--yes", action="store_true",
        help="confirma o encerramento sem perguntar",
    )

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
    add_background_options(host)
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
    host.add_argument(
        "--preset",
        metavar="NOME",
        help="carrega um preset local de hospedagem, sem senhas",
    )
    host.add_argument(
        "--save-preset",
        metavar="NOME",
        help="grava um preset local de hospedagem e não inicia o servidor",
    )

    proxy = subparsers.add_parser("proxy", help="inicia o proxy QWFWD")
    add_target(proxy, project_root)
    add_background_options(proxy)
    proxy.add_argument("--bind", dest="proxy_bind", type=bind_address, default="127.0.0.1")
    proxy.add_argument("--port", dest="proxy_port", type=bounded_integer(1024, 65535), default=30000)

    qtv = subparsers.add_parser("qtv", help="inicia o relay HTTP/MVD QTV")
    add_target(qtv, project_root)
    add_background_options(qtv)
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
        if namespace.preset and namespace.save_preset:
            parser.error("--preset e --save-preset não podem ser combinados")
        if namespace.preset:
            if namespace.selection is not None:
                parser.error("--preset não pode ser combinado com o jogo na linha de comando")
            try:
                presets = load_host_presets(namespace.target)
                preset = presets[namespace.preset]
            except (OSError, ValueError) as error:
                parser.error(str(error))
            except KeyError:
                parser.error(f"preset de hospedagem não encontrado: {namespace.preset}")
            apply_host_preset(namespace, preset)
        namespace.game = None
        if namespace.selection is not None:
            game_keys = {game.key for game in gameplay.load_local_games(project_root)}
            if namespace.selection.casefold() not in game_keys:
                parser.error(f"jogo desconhecido: {namespace.selection}")
            namespace.game = namespace.selection.casefold()
        namespace.game, namespace.ktx_options = gameplay.resolve_ktx_launch_options(
            parser, namespace, namespace.game,
        )
    return namespace


def main(
    arguments: list[str] | None = None, *, propagate_menu_exit: bool = False,
) -> int:
    raw_arguments = sys.argv[1:] if arguments is None else arguments
    if _service_context is None and any(value in {"-h", "--help"} for value in raw_arguments):
        root = Path(__file__).resolve().parents[3]
        gameplay_module = importlib.import_module("gameplay")

        def unavailable_catalog(*_arguments) -> dict[str, object]:
            raise RuntimeError("catálogo zipapp indisponível no adapter de ajuda")

        gameplay_context = gameplay_module.GameplayContext(
            project_root=root,
            installer_root=root,
            zipapp_path=None,
            installer_base=object,
            console=console,
            read_zipapp_json=unavailable_catalog,
            public_cli=False,
        )
        configure_context(ServiceContext(
            project_root=root,
            zipapp_path=None,
            installer_base=object,
            runtimes={},
            capability_catalog={},
            host_platforms={},
            host_platform=host_adapter,
            console=console,
            gameplay_module=gameplay_module,
            gameplay_context=gameplay_context,
        ))
    temporary_paths: list[Path] = []
    materialized_ktx: list[MaterializedKtx] = []
    resources = ServiceResources(temporary_paths, materialized_ktx)
    try:
        with finalize_service_operation(resources):
            options = parse_arguments(raw_arguments, core.PROJECT_ROOT)
            console.configure(verbose=options.verbose, no_color=options.no_color)
            navigation.configure(no_color=options.no_color)
            target = options.target.expanduser().resolve()
            options.target = target
            if getattr(options, "background_child", False):
                read_background_request(options)
            installer = gameplay.Player(
                core.PROJECT_ROOT, target, online_only=core.ZIPAPP_PATH is not None,
            )
            resources.installer = installer
            installer.validate_target("verify", purge=False)
            installer.reject_target_symlinks()

            if options.action == "host" and options.save_preset:
                try:
                    saved = save_host_preset(target, options.save_preset, options)
                except (OSError, ValueError) as error:
                    raise InstallerError(str(error)) from error
                console.success(f"Preset de hospedagem gravado: {saved['name']}")
                return 0

            if options.action == "status":
                active = show_service_status(target)
                if options.stop:
                    if not active:
                        raise InstallerError("Nenhuma stack de serviços x86QW está ativa.")
                    confirmed = options.yes or navigation.confirm(
                        "Encerrar a stack ativa?",
                        breadcrumb="x86QW › Serviços › Encerrar",
                        description="finalizar dependentes, serviços, servidor e temporários",
                        default=False,
                    )
                    if not confirmed:
                        console.info("Encerramento cancelado; a stack continua ativa.")
                        return 0
                    request_service_stop(target)
                    console.success("Stack de serviços encerrada coordenadamente.")
                return 0

            selection: HostedGame | None = None
            if options.action == "host":
                selection_state = "game"
                while True:
                    selection = select_hosted_game(
                        installer, options, initial=selection,
                        start_state=selection_state,
                    )
                    if selection is None:
                        console.info("Hospedagem cancelada; nenhum serviço foi iniciado.")
                        return 0
                    return_to_selection = False
                    profile_choice: str | None = None
                    while True:
                        profile = (
                            choose_host_configuration(options, profile_choice)
                            if options.menu else "advanced"
                        )
                        if profile is None:
                            selection_state = "map"
                            return_to_selection = True
                            break
                        profile_choice = profile
                        if profile == "advanced" and options.menu:
                            if not configure_advanced_host_menu(options):
                                continue
                        elif profile == "quick" and options.menu:
                            if not menu_execution_mode(
                                options, "x86QW › Hospedar › Rápido local",
                            ):
                                continue
                        resolve_passwords(options)
                        warn_external_bind(options)
                        if not options.menu:
                            break
                        summary = host_summary_text(selection, options, profile)
                        confirmed = navigation.confirm(
                            "Iniciar esta hospedagem?",
                            breadcrumb="x86QW › Hospedar › Confirmação",
                            subtitle="\n" + summary,
                            description="iniciar MVDSV e os serviços selecionados",
                            default=True,
                            allow_back=True,
                        )
                        if confirmed is None:
                            continue
                        if not confirmed:
                            console.info("Hospedagem cancelada; nenhum serviço foi iniciado.")
                            return 0
                        break
                    if return_to_selection:
                        continue
                    break
            else:
                while True:
                    if not configure_service_menu(options):
                        console.info("Serviço cancelado; nenhum processo foi iniciado.")
                        return 0
                    if not options.menu:
                        break
                    confirmed = navigation.confirm(
                        "Iniciar este serviço?",
                        breadcrumb=(
                            "x86QW › Serviços › QTV › Confirmação"
                            if options.action == "qtv"
                            else "x86QW › Serviços › QWFWD › Confirmação"
                        ),
                        subtitle="\n" + service_summary_text(options),
                        description=(
                            "iniciar o relay QTV com os parâmetros acima"
                            if options.action == "qtv"
                            else "iniciar o proxy QWFWD com os parâmetros acima"
                        ),
                        default=True,
                        allow_back=True,
                    )
                    if confirmed is None:
                        continue
                    if not confirmed:
                        console.info("Serviço cancelado; nenhum processo foi iniciado.")
                        return 0
                    break
                resolve_passwords(options)
                warn_external_bind(options)

            if options.background and not options.background_child:
                return launch_background_controller(options, selection)

            session_lock = SessionLock.acquire(target, options.action)
            resources.session_lock = session_lock
            if options.background_child:
                if not options.background_log:
                    raise InstallerError("Controlador em segundo plano sem log privado.")
                activate_background_log(target, options.background_log)
            recover_sessions(target)
            session_lock.confirm_recovery()
            resources.recovery_confirmed = True
            preflight_ports(requested_ports(options))

            if options.action == "proxy":
                console.banner("iniciar QWFWD", target)
                spec = proxy_spec(installer, options)
                journal = SessionJournal(
                    target, session_id=session_lock.session_id, controller=session_lock.owner,
                    background=options.background_child,
                    background_log=options.background_log,
                )
                resources.journal = journal
                console.info(f"Proxy local: {options.proxy_bind}:{options.proxy_port}/UDP")
                return run_processes([spec], journal, reporter=console)
            if options.action == "qtv":
                console.banner("iniciar QTV", target)
                journal = SessionJournal(
                    target, session_id=session_lock.session_id, controller=session_lock.owner,
                    background=options.background_child,
                    background_log=options.background_log,
                )
                resources.journal = journal
                spec = qtv_spec(
                    installer, bind=options.bind, port=options.port,
                    hostname=options.hostname, upstream=options.upstream,
                    password=options.qtv_password, session_paths=temporary_paths,
                    journal=journal,
                )
                console.info(f"QTV HTTP: http://{endpoint(options.bind, options.port)}/")
                return run_processes([spec], journal, reporter=console)

            console.banner("hospedar jogo com MVDSV", target)
            assert selection is not None
            hostname = options.hostname or f"x86QW - {selection.game.label}"
            safe_text(options.password, "senha de jogador") if options.password else None
            safe_text(options.spectator_password, "senha de espectador") if options.spectator_password else None
            safe_text(options.rcon_password, "senha RCON") if options.rcon_password else None
            safe_text(options.qtv_password, "senha QTV") if options.qtv_password else None
            journal = SessionJournal(
                target, session_id=session_lock.session_id, controller=session_lock.owner,
                background=options.background_child,
                background_log=options.background_log,
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
            return run_processes(specs, journal, reporter=console)
    except InstallerError as error:
        console.error(str(error))
        return int(error.exit_code)
    except navigation.MenuExit:
        if propagate_menu_exit:
            raise
        console.info("Menu encerrado; nenhum serviço foi iniciado.")
        return int(ExitCode.SUCCESS)
    except navigation.MenuCancelled:
        console.info("Operação cancelada; nenhum serviço foi iniciado.")
        return int(ExitCode.INTERRUPTED)
    except Exception as error:
        if "options" in locals() and getattr(options, "verbose", False):
            import traceback
            traceback.print_exc()
        console.error(f"Falha inesperada nos serviços x86QW: {error}")
        return int(ExitCode.FAILURE)


if __name__ == "__main__":
    raise SystemExit(main())
