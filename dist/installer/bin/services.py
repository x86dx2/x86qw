#!/usr/bin/env python3
"""Launch the dedicated MVDSV, QWFWD and QTV components installed by x86QW."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import ipaddress
import os
import platform
import secrets
import socket
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

RUNTIME_NAMES = {
    "mvdsv": ("mvdsv", "mvdsv.exe"),
    "qwfwd": ("qwfwd", "qwfwd.exe"),
    "qtv": ("qtv", "qtv.exe"),
}
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


@dataclass(frozen=True)
class StartupRcon:
    address: str
    port: int
    password: str
    config_name: str


@dataclass(frozen=True)
class MaterializedKtx:
    files: tuple[tuple[Path, str], ...]
    directories: tuple[Path, ...]


@dataclass(frozen=True)
class HostedGame:
    game: gameplay.LocalGameSpec
    mode: gameplay.KtxModeSpec | None
    map_name: str
    assets: frozenset[str]
    ktx_options: gameplay.KtxLaunchOptions


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


def local_service_address(address: str) -> str:
    if address == "0.0.0.0":
        return "127.0.0.1"
    if address == "::":
        return "::1"
    return address


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


def materialize_dedicated_pk3(
    package: Path,
    destination_root: Path,
    label: str,
) -> MaterializedKtx:
    """Expose verified PK3 members to MVDSV, which does not implement PK3 loading."""
    if not package.is_file() or package.is_symlink():
        raise InstallerError(f"Pacote {label} ausente ou inseguro: {package}")
    if not destination_root.is_dir() or destination_root.is_symlink():
        raise InstallerError(f"Diretório de {label} ausente ou inseguro: {destination_root}")
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
                    raise InstallerError(f"Membro inseguro no pacote {label}: {info.filename}")
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
                payload = archive.read(info)
                digest = hashlib.sha256(payload).hexdigest()
                if lexists(destination):
                    if (
                        not destination.is_file()
                        or destination.is_symlink()
                        or hashlib.sha256(destination.read_bytes()).hexdigest() != digest
                    ):
                        raise InstallerError(
                            f"Arquivo local conflita com a carga dedicada de {label}: {destination}"
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
        raise InstallerError(
            f"Não foi possível preparar a carga de {label} para o MVDSV: {error}"
        ) from error
    return MaterializedKtx(tuple(created_files), tuple(created_directories))


def materialize_dedicated_ktx(target: Path) -> MaterializedKtx:
    return materialize_dedicated_pk3(target / "qw/ktx.pk3", target / "qw", "KTX")


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
    player.migrate_mutable_component_defaults(component)
    player.verify_component(component)
    player.ensure_local_play_support(games)
    mode = None
    assets: frozenset[str] = frozenset()
    if game.key == "ktx":
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
) -> MaterializedKtx | None:
    package = player.game_marker_path(selection.game)
    if package.suffix.casefold() != ".pk3":
        return None
    return materialize_dedicated_pk3(
        package,
        player.target / selection.game.gamedir,
        selection.game.label,
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
) -> ProcessSpec:
    binary = runtime_binary(installer, "mvdsv")
    game = selection.game
    mode = selection.mode
    map_name = selection.map_name
    materialized = materialize_hosted_game(installer, selection)
    if materialized is not None:
        materialized_ktx.append(materialized)

    hostname = options.hostname or f"x86QW - {game.label}"
    user_config = (
        "x86qw-mvdsv-user.cfg"
        if game.key == "ktx"
        else f"x86qw-{game.profile}-user.cfg"
    )
    post_map_settings: tuple[tuple[str, str], ...] = ()
    if game.key == "ktx":
        assert mode is not None
        post_map_settings = dedicated_ktx_settings(
            mode, map_name, selection.assets, selection.ktx_options, options.maxclients,
        )
    bootstrap_password = secrets.token_urlsafe(24) if post_map_settings else None
    initial_rcon_password = bootstrap_password or options.rcon_password
    lines = [
        f"exec {user_config}",
        f"hostname {q(safe_text(hostname, 'hostname'))}",
        f"maxclients {options.maxclients}",
        f"password {q(options.password)}",
        f"spectator_password {q(options.spectator_password)}",
        f"rcon_password {q(initial_rcon_password)}",
        f"set demo_tmp_record {0 if options.no_mvd else 1}",
    ]
    if game.key == "ktx":
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
        if game.key == "pro-x":
            lines.append("sv_loadentfiles 1")
    if options.with_qtv:
        lines.extend((
            f"qtv_streamport {options.port}",
            f"qtv_password {q(options.qtv_password)}",
        ))
    lines.append(f"map {q(map_name)}")
    game_directory = installer.target / game.gamedir
    session = temporary_config(game_directory, "x86qw_host_", lines)
    session_paths.append(session)
    startup_rcon = None
    if bootstrap_password is not None:
        post_map = temporary_config(
            game_directory,
            "x86qw_host_post_",
            [
                *(f"set {name} {value}" for name, value in post_map_settings),
                f"rcon_password {q(options.rcon_password)}",
            ],
        )
        session_paths.append(post_map)
        startup_rcon = StartupRcon(
            local_service_address(options.bind), options.port,
            bootstrap_password, post_map.name,
        )

    arguments = [
        str(binary), "-basedir", str(installer.target),
    ]
    if game.key != "ktx":
        arguments.extend(("-game", game.gamedir))
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

        # The first status reply is emitted only after the game VM has run its
        # first frame and applied the selected KTX usermode defaults.
        command = f"rcon {startup.password} exec {startup.config_name}\n".encode("ascii")
        connection.settimeout(2.0)
        connection.sendto(b"\xff\xff\xff\xff" + command, destination)
        try:
            response, _ = connection.recvfrom(65535)
        except TimeoutError as error:
            raise InstallerError("MVDSV não confirmou a configuração dedicada.") from error
        if b"Bad rcon_password" in response:
            raise InstallerError("MVDSV rejeitou a configuração dedicada por RCON local.")


def run_processes(specs: list[ProcessSpec]) -> int:
    processes: list[subprocess.Popen[bytes]] = []
    try:
        for spec in specs:
            console.detail(f"Iniciando {spec.label}: {spec.arguments[0]}")
            process = subprocess.Popen(spec.arguments, cwd=spec.cwd)
            processes.append(process)
            if spec.startup_rcon is not None:
                apply_startup_rcon(spec.startup_rcon)
                console.detail("Opções dedicadas aplicadas após a inicialização do KTX.")
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
    installer = None
    try:
        options = parse_arguments(sys.argv[1:] if arguments is None else arguments, core.PROJECT_ROOT)
        console.configure(verbose=options.verbose, no_color=options.no_color)
        target = options.target.expanduser().resolve()
        installer = gameplay.Player(
            core.PROJECT_ROOT, target, online_only=core.ZIPAPP_PATH is not None,
        )
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

        console.banner("hospedar jogo com MVDSV", target)
        selection = select_hosted_game(installer, options)
        hostname = options.hostname or f"x86QW - {selection.game.label}"
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
                hostname=f"{hostname} QTV", upstream=f"127.0.0.1:{options.port}",
                password=options.qtv_password, session_paths=temporary_paths,
            ))
        host = host_spec(
            installer, options, selection, temporary_paths, materialized_ktx,
        )
        specs.append(host)
        label = selection.game.label
        if selection.mode is not None:
            label += f" · {selection.mode.label}"
        console.info(
            f"Servidor: connect {options.bind}:{options.port} · {label} em {selection.map_name}"
        )
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
        if installer is not None:
            installer.cleanup_stage()


if __name__ == "__main__":
    raise SystemExit(main())
