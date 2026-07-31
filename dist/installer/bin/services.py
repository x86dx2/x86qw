#!/usr/bin/env python3
"""Launch the dedicated MVDSV, QWFWD and QTV components installed by x86QW."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import ipaddress
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

sys.dont_write_bytecode = True

core = importlib.import_module("manager")
gameplay = importlib.import_module("gameplay")

InstallerError = core.InstallerError
console = core.console
lexists = core.lexists
remove_path = core.remove_path

MODE_CVARS: dict[str, tuple[tuple[str, str], ...]] = {
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
RUNTIME_NAMES = {
    "mvdsv": ("mvdsv", "mvdsv.exe"),
    "qwfwd": ("qwfwd", "qwfwd.exe"),
    "qtv": ("qtv", "qtv.exe"),
}
MAP_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


@dataclass(frozen=True)
class ProcessSpec:
    label: str
    arguments: tuple[str, ...]
    cwd: Path


@dataclass(frozen=True)
class MaterializedKtx:
    files: tuple[tuple[Path, str], ...]
    directories: tuple[Path, ...]


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
    if not value or len(value) > maximum or any(character in value for character in '\\";\r\n'):
        raise InstallerError(f"{label} contém caracteres inválidos ou excede {maximum} caracteres.")
    return value


def q(value: str) -> str:
    return f'"{value}"'


def endpoint(address: str, port: int) -> str:
    return f"[{address}]:{port}" if ":" in address else f"{address}:{port}"


def ensure_private_directory(path: Path) -> None:
    if lexists(path):
        if path.is_symlink() or not path.is_dir():
            raise InstallerError(f"Diretório de serviço ausente ou inseguro: {path}")
    else:
        path.mkdir(mode=0o700)


def runtime_variant(system: str | None = None, machine: str | None = None) -> str:
    system = (system or platform.system()).casefold()
    machine = (machine or platform.machine()).casefold()
    if system == "darwin" and machine in {"arm64", "aarch64"}:
        return "macos-arm64"
    if system == "linux" and machine in {"x86_64", "amd64"}:
        return "linux-amd64"
    if system == "windows" and machine in {"amd64", "x86_64"}:
        return "windows-x64"
    raise InstallerError(
        f"Runtime de serviço indisponível para {platform.system()} {platform.machine()}. "
        "Os alvos distribuídos são macOS arm64, Linux amd64 e Windows x64."
    )


def runtime_binary(installer: core.Installer, component: str) -> Path:
    if component not in RUNTIME_NAMES:
        raise InstallerError(f"Runtime desconhecido: {component}")
    if installer.verify_component(component) == 0:
        raise InstallerError(
            f"O componente {component} não está instalado. "
            "Execute install.sh e selecione o perfil completo ou esse componente."
        )
    variant = runtime_variant()
    unix_name, windows_name = RUNTIME_NAMES[component]
    name = windows_name if variant == "windows-x64" else unix_name
    binary = installer.target / "_x86qw" / "runtimes" / component / variant / name
    if not binary.is_file() or binary.is_symlink():
        raise InstallerError(f"Executável gerenciado ausente ou inseguro: {binary}")
    if os.name != "nt":
        binary.chmod(binary.stat().st_mode | 0o100)
    return binary


def temporary_config(directory: Path, prefix: str, lines: list[str]) -> Path:
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
        return path
    except Exception:
        if lexists(path):
            remove_path(path)
        raise


def ktx_assets(target: Path) -> frozenset[str]:
    package = target / "qw" / "ktx.pk3"
    if not package.is_file() or package.is_symlink():
        raise InstallerError(f"Pacote KTX ausente ou inseguro: {package}")
    try:
        with zipfile.ZipFile(package) as archive:
            if archive.testzip() is not None:
                raise InstallerError(f"Pacote KTX corrompido: {package}")
            return frozenset(info.filename.casefold() for info in archive.infolist())
    except zipfile.BadZipFile as error:
        raise InstallerError(f"Pacote KTX inválido: {package}") from error


def materialize_dedicated_ktx(target: Path) -> MaterializedKtx:
    """Expose verified PK3 members to MVDSV, which does not implement PK3 loading."""
    package = target / "qw" / "ktx.pk3"
    destination_root = target / "qw"
    created_files: list[tuple[Path, str]] = []
    created_directories: list[Path] = []
    try:
        with zipfile.ZipFile(package) as archive:
            if archive.testzip() is not None:
                raise InstallerError(f"Pacote KTX corrompido: {package}")
            for info in archive.infolist():
                if info.is_dir():
                    continue
                relative = Path(info.filename)
                if (
                    relative.is_absolute()
                    or "\\" in info.filename
                    or any(part in {"", ".", ".."} for part in relative.parts)
                ):
                    raise InstallerError(f"Membro inseguro no pacote KTX: {info.filename}")
                destination = destination_root.joinpath(*relative.parts)
                parent = destination.parent
                missing_parents: list[Path] = []
                cursor = parent
                while cursor != destination_root and not lexists(cursor):
                    missing_parents.append(cursor)
                    cursor = cursor.parent
                if lexists(cursor) and (not cursor.is_dir() or cursor.is_symlink()):
                    raise InstallerError(f"Diretório inseguro ao preparar MVDSV: {cursor}")
                for directory in reversed(missing_parents):
                    directory.mkdir()
                    created_directories.append(directory)
                payload = archive.read(info)
                digest = hashlib.sha256(payload).hexdigest()
                if lexists(destination):
                    if (
                        not destination.is_file()
                        or destination.is_symlink()
                        or hashlib.sha256(destination.read_bytes()).hexdigest() != digest
                    ):
                        raise InstallerError(
                            f"Arquivo local conflita com a carga KTX dedicada: {destination}"
                        )
                    continue
                descriptor, temporary_name = tempfile.mkstemp(
                    prefix=".x86qw_ktx_", dir=destination.parent,
                )
                temporary = Path(temporary_name)
                try:
                    with os.fdopen(descriptor, "wb") as output:
                        output.write(payload)
                    if os.name != "nt":
                        temporary.chmod(0o644)
                    temporary.replace(destination)
                finally:
                    if lexists(temporary):
                        remove_path(temporary)
                created_files.append((destination, digest))
    except InstallerError:
        cleanup_dedicated_ktx(MaterializedKtx(tuple(created_files), tuple(created_directories)))
        raise
    except (OSError, zipfile.BadZipFile) as error:
        cleanup_dedicated_ktx(MaterializedKtx(tuple(created_files), tuple(created_directories)))
        raise InstallerError(f"Não foi possível preparar a carga KTX para o MVDSV: {error}") from error
    return MaterializedKtx(tuple(created_files), tuple(created_directories))


def cleanup_dedicated_ktx(materialized: MaterializedKtx) -> None:
    for path, expected in reversed(materialized.files):
        if not lexists(path):
            continue
        if (
            path.is_file()
            and not path.is_symlink()
            and hashlib.sha256(path.read_bytes()).hexdigest() == expected
        ):
            remove_path(path)
        else:
            console.warning(f"Arquivo KTX alterado durante a sessão foi preservado: {path}")
    for directory in reversed(materialized.directories):
        if directory.is_dir() and not directory.is_symlink():
            try:
                directory.rmdir()
            except OSError:
                pass


def selected_mode(project_root: Path, key: str):
    modes = gameplay.load_ktx_modes(project_root)
    aliases = {
        identity: mode
        for mode in modes
        for identity in (mode.key, *mode.aliases)
    }
    mode = aliases.get(key.casefold())
    if mode is None:
        raise InstallerError(
            f"Modo KTX desconhecido: {key}. Use um destes: "
            + ", ".join(mode.key for mode in modes)
        )
    return mode


def host_spec(
    installer: core.Installer, options: argparse.Namespace, session_paths: list[Path],
    materialized_ktx: list[MaterializedKtx],
) -> ProcessSpec:
    if installer.verify_component("ktx") == 0:
        raise InstallerError("O componente ktx é obrigatório para hospedar com MVDSV.")
    binary = runtime_binary(installer, "mvdsv")
    mode = selected_mode(installer.project_root, options.mode)
    map_name = options.map or mode.default_map
    if MAP_NAME.fullmatch(map_name) is None:
        raise InstallerError(f"Mapa inválido: {map_name}")
    assets = ktx_assets(installer.target)
    if mode.required_map_asset is not None:
        required = mode.required_map_asset.format(map=map_name.casefold())
        if required not in assets:
            raise InstallerError(f"O mapa {map_name} não possui o recurso obrigatório {required}.")
    materialized_ktx.append(materialize_dedicated_ktx(installer.target))

    lines = [
        "exec x86qw-mvdsv-user.cfg",
        f"hostname {q(safe_text(options.hostname, 'hostname'))}",
        f"maxclients {options.maxclients}",
        "sv_progtype 2",
        "sv_mintic 0.01",
        "sv_maxtic 0.03",
        "pm_ktjump 1",
        f"set k_defmode {mode.usermode}",
        f"set k_defmap {map_name}",
        f"password {q(options.password)}",
        f"spectator_password {q(options.spectator_password)}",
        f"rcon_password {q(options.rcon_password)}",
        f"set demo_tmp_record {0 if options.no_mvd else 1}",
    ]
    for name, value in mode.launch_settings:
        lines.append(f"{name} {value}")
    if options.with_qtv:
        lines.extend((
            f"qtv_streamport {options.port}",
            f"qtv_password {q(options.qtv_password)}",
        ))
    session = temporary_config(installer.target / "qw", "x86qw_host_", lines)
    session_paths.append(session)

    arguments = [
        str(binary), "-basedir", str(installer.target), "-ip", options.bind,
        "-port", str(options.port), "-mem", "64", "+exec", session.name,
        "+map", map_name,
    ]
    for name, value in MODE_CVARS.get(mode.key, ()):
        arguments.extend(("+set", name, value))
    return ProcessSpec("MVDSV", tuple(arguments), installer.target)


def proxy_spec(installer: core.Installer, options: argparse.Namespace) -> ProcessSpec:
    binary = runtime_binary(installer, "qwfwd")
    directory = installer.target / "_x86qw" / "services" / "qwfwd"
    config = directory / "qwfwd.cfg"
    if not config.is_file() or config.is_symlink():
        raise InstallerError(f"Configuração QWFWD ausente ou insegura: {config}")
    return ProcessSpec(
        "QWFWD", (str(binary), str(options.proxy_port), options.proxy_bind), directory,
    )


def qtv_spec(
    installer: core.Installer,
    *, bind: str,
    port: int,
    hostname: str,
    upstream: str | None,
    password: str,
    session_paths: list[Path],
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
        safe_text(upstream, "upstream QTV", 128)
        safe_text(password, "senha QTV") if password else None
        lines.append(f"qtv {q(upstream)} {q(password)}")
    session = temporary_config(directory, "x86qw-session-", lines)
    session_paths.append(session)
    return ProcessSpec("QTV", (str(binary), "exec", session.name), directory)


def stop_processes(processes: list[subprocess.Popen[bytes]]) -> None:
    for process in reversed(processes):
        if process.poll() is None:
            process.terminate()
    deadline = time.monotonic() + 4
    for process in reversed(processes):
        if process.poll() is None:
            try:
                process.wait(max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def run_processes(specs: list[ProcessSpec]) -> int:
    processes: list[subprocess.Popen[bytes]] = []
    try:
        for spec in specs:
            console.detail(f"Iniciando {spec.label}: {spec.arguments[0]}")
            processes.append(subprocess.Popen(spec.arguments, cwd=spec.cwd))
        while True:
            for spec, process in zip(specs, processes):
                code = process.poll()
                if code is not None:
                    if code != 0:
                        console.warning(f"{spec.label} encerrou com código {code}.")
                    return code
            time.sleep(0.1)
    except KeyboardInterrupt:
        console.info("Encerrando serviços x86QW…")
        return 130
    except OSError as error:
        raise InstallerError(f"Não foi possível iniciar um serviço: {error}") from error
    finally:
        stop_processes(processes)


def add_target(parser: argparse.ArgumentParser, project_root: Path) -> None:
    parser.add_argument(
        "--target", type=Path, default=project_root / "quake-world",
        help="diretório da instalação x86QW",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="mostra detalhes técnicos")
    parser.add_argument("--no-color", action="store_true", help="desativa cores")


def parse_arguments(arguments: list[str], project_root: Path) -> argparse.Namespace:
    parser = core.FriendlyArgumentParser(
        prog="x86qw", description="Hospeda KTX e executa os serviços QuakeWorld do x86QW.",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    host = subparsers.add_parser("host", help="inicia um servidor KTX dedicado com MVDSV")
    add_target(host, project_root)
    host.add_argument("--mode", default="duel", help="modo KTX (duel, 2on2, 4on4, ctf, race…)")
    host.add_argument("--map", help="mapa inicial")
    host.add_argument("--bind", type=bind_address, default="127.0.0.1", help="IP do servidor (padrão: loopback)")
    host.add_argument("--port", type=bounded_integer(1024, 65535), default=28501)
    host.add_argument("--hostname", default="x86QW MVDSV")
    host.add_argument("--maxclients", type=bounded_integer(1, 32), default=16)
    host.add_argument("--password", default="", help="senha para jogadores")
    host.add_argument("--spectator-password", default="", help="senha para espectadores")
    host.add_argument("--rcon-password", default="", help="senha administrativa RCON")
    host.add_argument("--no-mvd", action="store_true", help="desativa gravação automática de MVD")
    host.add_argument("--with-qtv", action="store_true", help="inicia QTV conectado ao servidor")
    host.add_argument("--qtv-bind", type=bind_address, default="127.0.0.1")
    host.add_argument("--qtv-port", type=bounded_integer(1024, 65535), default=28000)
    host.add_argument("--qtv-password", default="", help="segredo entre MVDSV e QTV")
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
    qtv.add_argument("--upstream", help="MVDSV de origem no formato host:porta")
    qtv.add_argument("--password", default="", help="senha QTV configurada no MVDSV")
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    temporary_paths: list[Path] = []
    materialized_ktx: list[MaterializedKtx] = []
    try:
        options = parse_arguments(sys.argv[1:] if arguments is None else arguments, core.PROJECT_ROOT)
        console.configure(verbose=options.verbose, no_color=options.no_color)
        target = options.target.expanduser().resolve()
        installer = core.Installer(core.PROJECT_ROOT, target, online_only=core.ZIPAPP_PATH is not None)
        installer.validate_target("verify", purge=False)
        installer.reject_target_symlinks()

        if options.action == "proxy":
            console.banner("iniciar QWFWD", target)
            console.info(f"Proxy local: {options.proxy_bind}:{options.proxy_port}/UDP")
            return run_processes([proxy_spec(installer, options)])
        if options.action == "qtv":
            console.banner("iniciar QTV", target)
            spec = qtv_spec(
                installer, bind=options.bind, port=options.port,
                hostname=options.hostname, upstream=options.upstream,
                password=options.password, session_paths=temporary_paths,
            )
            console.info(f"QTV HTTP: http://{endpoint(options.bind, options.port)}/")
            return run_processes([spec])

        console.banner("hospedar KTX com MVDSV", target)
        mode = selected_mode(installer.project_root, options.mode)
        map_name = options.map or mode.default_map
        safe_text(options.password, "senha de jogador") if options.password else None
        safe_text(options.spectator_password, "senha de espectador") if options.spectator_password else None
        safe_text(options.rcon_password, "senha RCON") if options.rcon_password else None
        safe_text(options.qtv_password, "senha QTV") if options.qtv_password else None
        specs: list[ProcessSpec] = []
        if options.with_proxy:
            specs.append(proxy_spec(installer, options))
        if options.with_qtv:
            specs.append(qtv_spec(
                installer, bind=options.qtv_bind, port=options.qtv_port,
                hostname=f"{options.hostname} QTV", upstream=f"127.0.0.1:{options.port}",
                password=options.qtv_password, session_paths=temporary_paths,
            ))
        host = host_spec(installer, options, temporary_paths, materialized_ktx)
        specs.append(host)
        console.info(f"Servidor: connect {options.bind}:{options.port} · {mode.label} em {map_name}")
        if options.with_qtv:
            console.info(f"QTV HTTP: http://{endpoint(options.qtv_bind, options.qtv_port)}/")
        return run_processes(specs)
    except InstallerError as error:
        console.error(str(error))
        return 1
    except Exception as error:
        if "options" in locals() and getattr(options, "verbose", False):
            import traceback
            traceback.print_exc()
        console.error(f"Falha inesperada nos serviços x86QW: {error}")
        return 1
    finally:
        for path in temporary_paths:
            if lexists(path):
                remove_path(path)
        for materialized in reversed(materialized_ktx):
            cleanup_dedicated_ktx(materialized)


if __name__ == "__main__":
    raise SystemExit(main())
