#!/usr/bin/env python3
"""Launcher local dos mods incluídos na distribuição x86QW."""

from __future__ import annotations

import importlib
import os
import re
import struct
import sys
import tempfile
import traceback
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

sys.dont_write_bytecode = True

core = importlib.import_module("manager")

InstallerError = core.InstallerError
console = core.console
file_count = core.file_count
file_hash = core.file_hash
lexists = core.lexists

PLAY_SUPPORT_VERSION = "7"
PROFILED_LOCAL_GAMES = frozenset({"ktx", "final-arena", "pro-x", "team-fortress", "td2"})
PRECONNECT_LOCAL_GAMES = frozenset({"team-fortress"})
LEGACY_LOCAL_CAPABILITIES = {
    "final-arena": ("noaim",),
    "pro-x": ("setinfo", "bind"),
}


@dataclass(frozen=True)
class LocalGameSpec:
    key: str
    label: str
    gamedir: str
    profile: str
    component: str
    marker: str
    program: str
    default_map: str
    suggested_maps: tuple[str, ...]
    version: str
    description: str
    confirmation: str


LOCAL_GAMES = (
    LocalGameSpec(
        "ktx", "KTX", "qw", "ktx", "ktx", "qw/ktx.pk3", "qw/ktx.pk3", "dm6",
        ("dm6", "dm2", "dm4", "aerowalk"),
        "1.47",
        "QuakeWorld competitivo com o QVM oficial do KTX.",
        "No console, ktxver deve mostrar a versão carregada.",
    ),
    LocalGameSpec(
        "final-arena", "Final Arena", "arena", "arena", "final-arena",
        "arena/arena.pk3", "arena/arena.pk3", "23ar-a",
        ("23ar-a", "arenarg2", "arenarg4", "dm2arena"),
        "1.20",
        "Duelos individuais em fila: o vencedor permanece na arena.",
        "No console, gamedir e *gamedir devem mostrar arena.",
    ),
    LocalGameSpec(
        "pro-x", "Pro-X", "prox", "prox", "pro-x",
        "prox/qwprogs.dat", "prox/qwprogs.dat", "proxmap1",
        ("proxmap1", "proxmap2", "proxmap3", "proxmap4", "proxmap5"),
        "1.1",
        "Rounds e equipes com ready, break e votação.",
        "No console, gamedir e *gamedir devem mostrar prox.",
    ),
    LocalGameSpec(
        "team-fortress", "Team Fortress", "fortress", "fortress", "team-fortress",
        "fortress/misc.pak", "fortress/qwprogs.dat", "2fort5r",
        ("2fort5r", "well6", "bases", "mbasesr"),
        "2.9",
        "Team Fortress clássico para QuakeWorld.",
        "A inicialização deve mostrar TeamFortress QuakeWorld v2.9 Beta.",
    ),
    LocalGameSpec(
        "td2", "Total Destruction 2", "td2", "td2", "total-destruction-2",
        "td2/qwprogs.dat", "td2/qwprogs.dat", "dm6", ("dm6", "dm2", "dm4", "e1m2"),
        "2.22",
        "Armas, magias, runas e poderes.",
        "No serverinfo, *gamedir deve ser td2 e td2qw deve ser 2.22.",
    ),
)


class Player(core.Installer):
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
            marker = self.game_marker_path(game)
            if component is not None and marker.is_file() and not marker.is_symlink():
                available.append(game)
        return available

    def game_marker_path(self, game: LocalGameSpec) -> Path:
        marker = self.target.joinpath(*PurePosixPath(game.marker).parts)
        if marker.is_file() or game.key != "pro-x":
            return marker
        legacy = self.target / "prox/prox.pk3"
        return legacy if legacy.is_file() else marker

    def game_program_path(self, game: LocalGameSpec) -> Path:
        program = self.target.joinpath(*PurePosixPath(game.program).parts)
        if not program.is_file() and game.key == "pro-x":
            legacy = self.target / "prox/prox.pk3"
            if legacy.is_file():
                program = legacy
        if not program.is_file() or program.is_symlink():
            raise InstallerError(f"Gamecode local não encontrado: {program}")
        return program

    def installed_component_for_game(self, game: LocalGameSpec) -> str | None:
        present, _, _ = self.validate_component_pair(game.component)
        if present:
            return game.component
        if game.key in {"final-arena", "pro-x"}:
            legacy_present, _, _ = self.validate_component_pair("clan-arena")
            if legacy_present:
                return "clan-arena"
        return None

    def installed_game_version(self, game: LocalGameSpec) -> str:
        component = self.installed_component_for_game(game)
        if component is None:
            return game.version
        present, _, receipt = self.validate_component_pair(component)
        if not present or receipt is None:
            return game.version
        match = re.match(r"^(\d+(?:\.\d+)+)", receipt["selection"])
        return match.group(1) if match is not None else game.version

    def choose_local_game(self, games: list[LocalGameSpec]) -> LocalGameSpec:
        print("\nQual mod deseja jogar localmente?")
        labels = [game.label + (" (padrão)" if index == 1 else "") for index, game in enumerate(games, 1)]
        versions = [self.installed_game_version(game) for game in games]
        label_width = max(map(len, labels))
        version_width = max(map(len, versions))
        index_width = len(str(len(games)))
        for index, (game, label, version) in enumerate(zip(games, labels, versions), 1):
            print(
                f"  {index:>{index_width}}) {label:<{label_width}}  "
                f"v{version:<{version_width}}  {game.description}"
            )
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
        self.migrate_saved_configs()
        self.refresh_qw_package_order()
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
        arguments = ["+sb_listcache", "0"]
        if game.key != "ktx":
            arguments.extend([
                "-game", game.gamedir,
                "+sv_gamedir", game.gamedir,
            ])
            arguments.extend(["+sv_progtype", "0"])
        if game.key in PRECONNECT_LOCAL_GAMES:
            arguments.extend(["+exec", f"x86qw-{game.profile}-pre.cfg"])
        if capabilities := LEGACY_LOCAL_CAPABILITIES.get(game.key):
            arguments.extend([
                "+cl_remote_capabilities",
                "$cl_remote_capabilities," + ",".join(capabilities),
            ])
        if game.key == "pro-x":
            arguments.extend(["+sv_loadentfiles", "1"])
        if game.key != "ktx":
            # PR1 gamecodes do not advertise the high-lag teleport extension.
            arguments.extend(["+cl_pext_lagteleport", "0"])
        if game.key == "ktx":
            arguments.extend(["+set", "k_defmap", map_name])
        arguments.extend(["+map", map_name])
        if game.key in PROFILED_LOCAL_GAMES:
            arguments.append("+wait")
            arguments.extend(["+exec", f"x86qw-{game.profile}.cfg"])
        console.info(f"Abrindo {game.label} no mapa {map_name}...")
        self.launch_runtime(runtime, arguments)
        console.success(f"{label} aberto com {game.label}.")
        console.info(game.confirmation)

    def ensure_local_play_support(self, games: list[LocalGameSpec]) -> None:
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
                files: dict[str, bytes] = {}
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
                if game.key in PROFILED_LOCAL_GAMES:
                    self.ensure_game_user_profile(game)
        finally:
            self.cleanup_stage()
            self.stage = previous_stage

    def ensure_game_user_profile(self, game: LocalGameSpec) -> None:
        destination = self.target / game.gamedir / f"x86qw-{game.profile}-user.cfg"
        if lexists(destination):
            if not destination.is_file() or destination.is_symlink():
                raise InstallerError(f"Configuração pessoal de {game.label} inválida: {destination}")
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            f"// x86QW: personalizações locais de {game.label}\n",
            encoding="utf-8",
        )
        if os.name != "nt":
            destination.chmod(0o644)
        console.info(f"Configuração pessoal de {game.label} criada: {destination}")

    def local_game_program(self, game: LocalGameSpec) -> bytes:
        package = self.game_program_path(game)
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


def parse_arguments(arguments: list[str], project_root: Path):
    public_cli = core.ZIPAPP_PATH is not None
    parser = core.FriendlyArgumentParser(
        prog="x86qw play" if public_cli else "dist/installer/bin/gameplay.py",
        description="Abre os mods locais da distribuição x86QW no ezQuake.",
        epilog=(
            "Exemplo: x86qw play"
            if public_cli
            else "Exemplo: ./dist/installer/bin/gameplay.py ./quake-world"
        ),
        add_help=False,
    )
    parser._positionals.title = "argumentos"
    parser._optionals.title = "opções"
    parser.add_argument("-h", "--help", action="help", help="mostra esta ajuda e encerra")
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="mostra comandos, caminhos e detalhes técnicos",
    )
    parser.add_argument(
        "--no-color", action="store_true",
        help="desativa cores mesmo em um terminal interativo",
    )
    parser.add_argument(
        "target", nargs="?", type=Path,
        help="diretório da instalação (padrão: ./quake-world)",
    )
    namespace = parser.parse_args(arguments)
    namespace.target = namespace.target or project_root / "quake-world"
    return namespace


def show_banner(target: Path) -> None:
    title = console.paint("x86-qw", "1;36")
    print(f"\n{title} · launcher QuakeWorld", flush=True)
    print(f"Destino: {target}", flush=True)


def main(arguments: list[str] | None = None) -> int:
    project_root = core.INSTALLER_ROOT
    options = None
    player = None
    try:
        options = parse_arguments(sys.argv[1:] if arguments is None else arguments, project_root)
        console.configure(verbose=options.verbose, no_color=options.no_color)
        show_banner(options.target)
        player = Player(project_root, options.target)
        player.validate_target("play")
        console.detail(f"Destino normalizado: {player.target}")
        player.reject_target_symlinks()
        console.section("Jogo local")
        player.play_local()
        return 0
    except KeyboardInterrupt:
        console.error("Operação cancelada. O jogo não foi iniciado.")
        return 130
    except InstallerError as error:
        console.error(str(error))
        if options is not None and not options.verbose:
            print("       Execute novamente com --verbose para obter detalhes técnicos.", file=sys.stderr)
        return 1
    except Exception as error:  # pragma: no cover - proteção final da CLI
        console.error(f"Falha inesperada: {error}")
        if options is not None and options.verbose:
            traceback.print_exc()
        else:
            print("       Execute novamente com --verbose para exibir o diagnóstico completo.", file=sys.stderr)
        return 1
    finally:
        if player is not None:
            player.cleanup_stage()


if __name__ == "__main__":
    raise SystemExit(main())
