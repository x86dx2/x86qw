#!/usr/bin/env python3
"""Launcher local dos mods incluídos na distribuição x86QW."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import struct
import subprocess
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
remove_path = core.remove_path

PLAY_SUPPORT_VERSION = "8"
DEVELOPMENT_KTX_MODE_CATALOG = "dist/mods/ktx/1.47/x86qw/modes.json"
RUNTIME_KTX_MODE_CATALOG = "_x86qw/ktx-modes.json"
PROFILED_LOCAL_GAMES = frozenset({"ktx", "final-arena", "pro-x", "team-fortress", "td2"})
PRECONNECT_LOCAL_GAMES = frozenset({"team-fortress"})
LEGACY_LOCAL_CAPABILITIES = {
    "final-arena": ("noaim",),
    "pro-x": ("setinfo", "bind"),
}
LEGACY_MACOS_VIDEO_LAYOUT = Path(".install/launcher/macos-video-layout.json")
LEGACY_MACOS_VIDEO_CVARS = (
    "vid_fullscreen",
    "vid_usedesktopres",
    "vid_win_borderless",
    "vid_win_displaynumber",
    "vid_win_width",
    "vid_win_height",
    "vid_xpos",
    "vid_ypos",
)
MACOS_FULLSCREEN_LAYOUT = Path(".install/launcher/macos-fullscreen-layout.json")
MACOS_FULLSCREEN_CVARS = (
    "vid_fullscreen",
    "vid_usedesktopres",
    "vid_width",
    "vid_height",
    "vid_displayfrequency",
)
MACOS_SAFE_AREA_SCRIPT = """
ObjC.import("AppKit");
var screen = $.NSScreen.mainScreen;
JSON.stringify({
    top: Number(screen.safeAreaInsets.top),
    width: Number(screen.frame.size.width),
    height: Number(screen.frame.size.height)
});
"""

# The ezQuake listen server persists these server cvars in the shared personal
# config. KTX intentionally uses MVDSV-oriented timing and jump values; reset
# them explicitly when changing games so they cannot leak into legacy gamecode.
NQUAKE_LOCAL_SERVER_SETTINGS = (
    ("sv_mintic", "0"),
    ("sv_maxtic", "0.1"),
    ("pm_ktjump", "0.5"),
)
KTX_LOCAL_SERVER_SETTINGS = (
    ("sv_mintic", "0.01"),
    ("sv_maxtic", "0.03"),
    ("pm_ktjump", "1"),
)


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


@dataclass(frozen=True)
class KtxModeSpec:
    key: str
    aliases: tuple[str, ...]
    label: str
    description: str
    recommended_players: str
    usermode: str
    default_map: str
    suggested_maps: tuple[str, ...]
    entry_config: str | None


LOCAL_GAMES = (
    LocalGameSpec(
        "ktx", "KTX", "qw", "ktx", "ktx", "qw/ktx.pk3", "qw/ktx.pk3", "dm6",
        ("dm6", "dm2", "dm4", "aerowalk"),
        "1.47",
        "QuakeWorld competitivo com o QVM oficial do KTX.",
    ),
    LocalGameSpec(
        "final-arena", "Final Arena", "arena", "arena", "final-arena",
        "arena/arena.pk3", "arena/arena.pk3", "23ar-a",
        ("23ar-a", "arenarg2", "arenarg4", "dm2arena"),
        "1.20",
        "Duelos individuais em fila: o vencedor permanece na arena.",
    ),
    LocalGameSpec(
        "pro-x", "Pro-X", "prox", "prox", "pro-x",
        "prox/qwprogs.dat", "prox/qwprogs.dat", "proxmap1",
        ("proxmap1", "proxmap2", "proxmap3", "proxmap4", "proxmap5"),
        "1.1",
        "Rounds e equipes com ready, break e votação.",
    ),
    LocalGameSpec(
        "team-fortress", "Team Fortress", "fortress", "fortress", "team-fortress",
        "fortress/misc.pak", "fortress/qwprogs.dat", "2fort5r",
        ("2fort5r", "well6", "bases", "mbasesr"),
        "2.9",
        "Team Fortress clássico para QuakeWorld.",
    ),
    LocalGameSpec(
        "td2", "Total Destruction 2", "td2", "td2", "total-destruction-2",
        "td2/qwprogs.dat", "td2/qwprogs.dat", "dm6", ("dm6", "dm2", "dm4", "e1m2"),
        "2.22",
        "Armas, magias, runas e poderes.",
    ),
)


def load_ktx_modes(project_root: Path) -> tuple[KtxModeSpec, ...]:
    if core.ZIPAPP_PATH is not None:
        catalog = core.read_zipapp_json(
            core.ZIPAPP_PATH, RUNTIME_KTX_MODE_CATALOG, "Catálogo de modos KTX",
        )
    else:
        path = project_root / DEVELOPMENT_KTX_MODE_CATALOG
        try:
            catalog = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise InstallerError(f"Catálogo de modos KTX inválido: {path}") from error
    if catalog.get("format") != 1 or catalog.get("game") != "ktx":
        raise InstallerError("Catálogo de modos KTX possui identidade inválida.")
    raw_modes = catalog.get("modes")
    if not isinstance(raw_modes, list) or not raw_modes:
        raise InstallerError("Catálogo de modos KTX não declara nenhum modo.")
    modes: list[KtxModeSpec] = []
    identities: set[str] = set()
    for raw in raw_modes:
        if not isinstance(raw, dict):
            raise InstallerError("Entrada inválida no catálogo de modos KTX.")
        key = raw.get("id")
        aliases = raw.get("aliases")
        suggested_maps = raw.get("suggested_maps")
        entry_config = raw.get("entry_config")
        text_fields = (
            "label", "description", "recommended_players", "usermode",
            "default_map",
        )
        if (
            not isinstance(key, str)
            or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", key) is None
            or not isinstance(aliases, list)
            or not all(
                isinstance(alias, str)
                and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", alias)
                for alias in aliases
            )
            or not isinstance(suggested_maps, list)
            or not suggested_maps
            or not all(
                isinstance(name, str)
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name)
                for name in suggested_maps
            )
            or entry_config not in {
                None,
                "x86qw-ktx-mode-midair.cfg",
                "x86qw-ktx-mode-race.cfg",
                "x86qw-ktx-mode-practice.cfg",
            }
            or any(not isinstance(raw.get(field), str) or not raw[field] for field in text_fields)
        ):
            raise InstallerError(f"Definição inválida do modo KTX: {key!r}.")
        if raw["default_map"].casefold() not in {name.casefold() for name in suggested_maps}:
            raise InstallerError(f"Mapa padrão ausente nas sugestões do modo KTX {key}.")
        mode_identities = {key.casefold(), *(alias.casefold() for alias in aliases)}
        if identities & mode_identities:
            raise InstallerError(f"Identidade duplicada no catálogo de modos KTX: {key}.")
        identities.update(mode_identities)
        modes.append(KtxModeSpec(
            key=key,
            aliases=tuple(aliases),
            label=str(raw["label"]),
            description=str(raw["description"]),
            recommended_players=str(raw["recommended_players"]),
            usermode=str(raw["usermode"]),
            default_map=str(raw["default_map"]),
            suggested_maps=tuple(suggested_maps),
            entry_config=entry_config,
        ))
    return tuple(modes)


class Player(core.Installer):
    @staticmethod
    def config_cvars(payload: bytes, names: tuple[str, ...]) -> dict[str, str]:
        values: dict[str, str] = {}
        for name in names:
            match = re.search(
                rb"(?mi)^[ \t]*" + re.escape(name.encode("ascii"))
                + rb"[ \t]+\"?([^\"\r\n]+?)\"?[ \t]*$",
                payload,
            )
            if match is not None:
                values[name] = match.group(1).decode("ascii", errors="strict").strip()
        return values

    @staticmethod
    def set_config_cvars(payload: bytes, settings: dict[str, str]) -> bytes:
        newline = b"\r\n" if b"\r\n" in payload else b"\n"
        updated = payload
        for name, value in settings.items():
            pattern = re.compile(
                rb"(?mi)^([ \t]*" + re.escape(name.encode("ascii"))
                + rb"[ \t]+)\"?[^\"\r\n]+?\"?([ \t]*)(\r?)$"
            )
            replacement = rb'\g<1>"' + value.encode("ascii") + rb'"\g<2>\g<3>'
            updated, count = pattern.subn(replacement, updated, count=1)
            if count == 0:
                if updated and not updated.endswith((b"\n", b"\r")):
                    updated += newline
                updated += name.encode("ascii") + b' "' + value.encode("ascii") + b'"' + newline
        return updated

    def remove_legacy_macos_video_layout(self) -> None:
        """Remove the short-lived 0.1.7 borderless workaround without touching personal video settings."""
        if sys.platform != "darwin":
            return
        marker = self.target / LEGACY_MACOS_VIDEO_LAYOUT
        if not lexists(marker):
            return
        if not marker.is_file() or marker.is_symlink():
            raise InstallerError(f"Estado legado de vídeo do launcher inválido: {marker}")
        try:
            state = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise InstallerError(f"Estado legado de vídeo do launcher inválido: {marker}") from error
        settings = state.get("settings") if isinstance(state, dict) else None
        managed = state.get("managed") is True if isinstance(state, dict) else False
        config = self.target / "ezquake/configs/config.cfg"
        backup = config.with_name("config.video-pre-x86qw.cfg")
        valid_settings = (
            isinstance(settings, dict)
            and set(settings) == set(LEGACY_MACOS_VIDEO_CVARS)
            and all(isinstance(value, str) for value in settings.values())
        )
        if managed and valid_settings and config.is_file() and backup.is_file():
            values = self.config_cvars(config.read_bytes(), LEGACY_MACOS_VIDEO_CVARS)
            if values == settings:
                self.write_personal_config(config, backup.read_bytes())
                remove_path(backup)
                console.success("Fullscreen pessoal anterior restaurado após a remoção do ajuste 0.1.7.")
        remove_path(marker)
        console.info("Ajuste legado de janela sem bordas removido; o ezQuake continuará em fullscreen.")

    def macos_notched_fullscreen_settings(self) -> dict[str, str] | None:
        try:
            safe_area = subprocess.run(
                ["osascript", "-l", "JavaScript", "-e", MACOS_SAFE_AREA_SCRIPT],
                check=True, capture_output=True, text=True, timeout=3,
            )
            geometry = json.loads(safe_area.stdout)
            top = int(geometry["top"])
            if top <= 0:
                return None
            profile = subprocess.run(
                ["system_profiler", "SPDisplaysDataType", "-json"],
                check=True, capture_output=True, text=True, timeout=8,
            )
            displays = json.loads(profile.stdout).get("SPDisplaysDataType")
            if not isinstance(displays, list):
                raise ValueError("lista de monitores ausente")
            main: dict[str, object] | None = None
            for gpu in displays:
                if not isinstance(gpu, dict) or not isinstance(gpu.get("spdisplays_ndrvs"), list):
                    continue
                for display in gpu["spdisplays_ndrvs"]:
                    if isinstance(display, dict) and display.get("spdisplays_main") == "spdisplays_yes":
                        main = display
                        break
                if main is not None:
                    break
            if main is None:
                raise ValueError("monitor principal ausente")
            native = str(main.get("spdisplays_pixelresolution", ""))
            resolution = re.fullmatch(r"spdisplays_(\d+)x(\d+)Retina", native)
            if resolution is None:
                raise ValueError("resolução física ausente")
            width, panel_height = (int(value) for value in resolution.groups())
            height = round(width * 10 / 16)
        except (
            OSError, subprocess.SubprocessError, json.JSONDecodeError,
            KeyError, TypeError, ValueError,
        ) as error:
            raise InstallerError(f"Não foi possível detectar o modo fullscreen seguro do macOS: {error}") from error
        if width < 1280 or height < 800 or panel_height <= height or panel_height - height > 256:
            raise InstallerError(
                f"Geometria inesperada na tela com notch: {width}x{panel_height}; modo seguro {width}x{height}."
            )
        return {
            "vid_fullscreen": "1",
            "vid_usedesktopres": "0",
            "vid_width": str(width),
            "vid_height": str(height),
            # Let SDL/macOS negotiate the refresh rate. Forcing the panel's
            # ProMotion maximum here couples a menu-layout fix to frame timing.
            "vid_displayfrequency": "0",
        }

    def write_macos_fullscreen_marker(
        self, marker: Path, *, managed: bool, settings: dict[str, str],
    ) -> None:
        marker.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({
            "format": 1,
            "project": "x86qw",
            "mode": "notched-fullscreen",
            "managed": managed,
            "settings": settings,
        }, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        self.write_personal_config(marker, payload)

    def configure_macos_fullscreen(self) -> None:
        if sys.platform != "darwin":
            return
        config = self.target / "ezquake/configs/config.cfg"
        if not lexists(config):
            return
        if not config.is_file() or config.is_symlink():
            raise InstallerError(f"Configuração global do ezQuake inválida: {config}")
        marker = self.target / MACOS_FULLSCREEN_LAYOUT
        current_payload = config.read_bytes()
        current = self.config_cvars(current_payload, MACOS_FULLSCREEN_CVARS)
        managed = False
        previous: dict[str, str] = {}
        if lexists(marker):
            if not marker.is_file() or marker.is_symlink():
                raise InstallerError(f"Estado de fullscreen do launcher inválido: {marker}")
            try:
                state = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise InstallerError(f"Estado de fullscreen do launcher inválido: {marker}") from error
            settings = state.get("settings") if isinstance(state, dict) else None
            valid = (
                isinstance(state, dict)
                and state.get("format") == 1
                and state.get("project") == "x86qw"
                and state.get("mode") == "notched-fullscreen"
                and isinstance(state.get("managed"), bool)
                and isinstance(settings, dict)
                and (
                    set(settings) == set(MACOS_FULLSCREEN_CVARS)
                    if state.get("managed") is True else settings == {}
                )
                and all(isinstance(value, str) for value in settings.values())
            )
            if not valid:
                raise InstallerError(f"Estado de fullscreen do launcher inválido: {marker}")
            managed = state["managed"]
            previous = dict(settings)
        if managed and current != previous:
            self.write_macos_fullscreen_marker(marker, managed=False, settings={})
            console.info("Configuração de vídeo pessoal detectada; o fullscreen automático foi desativado.")
            return
        if lexists(marker) and not managed:
            return

        desired = self.macos_notched_fullscreen_settings()
        if desired is None:
            if managed:
                updated = self.set_config_cvars(current_payload, {
                    "vid_fullscreen": "1", "vid_usedesktopres": "1",
                })
                self.write_personal_config(config, updated)
                remove_path(marker)
                console.success("Fullscreen desktop restaurado para o monitor sem notch.")
            return
        default_fullscreen = (
            not current
            or (
                current.get("vid_fullscreen", "1") == "1"
                and current.get("vid_usedesktopres", "1") == "1"
            )
        )
        if not managed and not default_fullscreen:
            self.write_macos_fullscreen_marker(marker, managed=False, settings={})
            console.info("Configuração de vídeo pessoal preservada; nenhum modo fullscreen foi alterado.")
            return
        updated = self.set_config_cvars(current_payload, desired)
        if updated != current_payload:
            self.write_personal_config(config, updated)
        self.write_macos_fullscreen_marker(marker, managed=True, settings=desired)
        if not managed:
            console.success(
                f"Fullscreen macOS ajustado para {desired['vid_width']}x{desired['vid_height']} "
                "com frequência automática; menus permanecem abaixo do notch."
            )

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

    def choose_local_game(
        self, games: list[LocalGameSpec], requested: str | None = None,
    ) -> LocalGameSpec:
        if requested is not None:
            matches = [game for game in games if game.key.casefold() == requested.casefold()]
            if len(matches) == 1:
                return matches[0]
            known = next(
                (game for game in LOCAL_GAMES if game.key.casefold() == requested.casefold()), None,
            )
            if known is not None:
                raise InstallerError(
                    f"{known.label} não está instalado. "
                    f"Adicione o componente {known.component} pelo bootstrap x86QW."
                )
            available = ", ".join(game.key for game in games)
            raise InstallerError(f"Jogo local desconhecido: {requested}. Disponíveis: {available}.")
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

    def choose_ktx_mode(
        self, modes: tuple[KtxModeSpec, ...], requested: str | None = None,
    ) -> KtxModeSpec:
        if requested is not None:
            answer = requested.casefold()
            matches = [
                mode for mode in modes
                if answer == mode.key.casefold()
                or answer in {alias.casefold() for alias in mode.aliases}
            ]
            if len(matches) == 1:
                return matches[0]
            available = ", ".join(mode.key for mode in modes)
            raise InstallerError(f"Modo KTX desconhecido: {requested}. Disponíveis: {available}.")
        print("\nQual modo KTX deseja jogar?")
        labels = [mode.label + (" (padrão)" if index == 1 else "") for index, mode in enumerate(modes, 1)]
        label_width = max(map(len, labels))
        players_width = max(len(mode.recommended_players) for mode in modes)
        index_width = len(str(len(modes)))
        for index, (mode, label) in enumerate(zip(modes, labels), 1):
            print(
                f"  {index:>{index_width}}) {label:<{label_width}}  "
                f"{mode.recommended_players:>{players_width}} jogador(es)  {mode.description}"
            )
        while True:
            try:
                answer = input(f"Escolha [1-{len(modes)}] (padrão: 1): ").strip()
            except EOFError as error:
                raise InstallerError("Nenhum modo KTX foi selecionado.") from error
            if not answer:
                return modes[0]
            if answer.isdigit() and 1 <= int(answer) <= len(modes):
                return modes[int(answer) - 1]
            matches = [
                mode for mode in modes
                if answer.casefold() == mode.key.casefold()
                or answer.casefold() in {alias.casefold() for alias in mode.aliases}
            ]
            if len(matches) == 1:
                return matches[0]
            console.warning(f"Escolha inválida. Use um número entre 1 e {len(modes)}.")

    @staticmethod
    def show_map_names(maps: list[str]) -> None:
        print("\nTodos os mapas disponíveis:")
        for offset in range(0, len(maps), 6):
            print("  " + "  ".join(f"{name:<16}" for name in maps[offset:offset + 6]).rstrip())

    def choose_local_map(
        self,
        game: LocalGameSpec,
        *,
        default_map: str | None = None,
        suggested_maps: tuple[str, ...] | None = None,
        label: str | None = None,
    ) -> str:
        maps = self.local_map_names(game.gamedir)
        lookup = {name.casefold(): name for name in maps}
        requested_default = default_map or game.default_map
        requested_suggestions = suggested_maps or game.suggested_maps
        display_label = label or game.label
        default = lookup.get(requested_default.casefold())
        if default is None:
            raise InstallerError(
                f"O mapa padrão {requested_default} não está disponível para {display_label}. "
                "Execute components para reparar o conteúdo."
            )
        suggestions = [
            lookup[name.casefold()] for name in requested_suggestions if name.casefold() in lookup
        ]
        print(f"\nMapas sugeridos para {display_label}:")
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

    def play_local(self, game_key: str | None = None, mode_key: str | None = None) -> None:
        self.check_paks()
        self.migrate_saved_configs()
        self.remove_legacy_macos_video_layout()
        self.configure_macos_fullscreen()
        self.refresh_qw_package_order()
        games = self.available_local_games()
        if not games:
            raise InstallerError(
                "Nenhum mod local gerenciado está instalado. Execute components e instale ao menos KTX."
            )
        game = self.choose_local_game(games, game_key)
        if mode_key is not None and game.key != "ktx":
            raise InstallerError("--mode só pode ser usado com o jogo KTX.")
        installed_component = self.installed_component_for_game(game)
        if installed_component is None:
            raise InstallerError(f"O componente de {game.label} não está mais instalado.")
        self.migrate_mutable_component_defaults(installed_component)
        self.verify_component(installed_component)
        ktx_mode = None
        if game.key == "ktx":
            ktx_mode = self.choose_ktx_mode(load_ktx_modes(self.project_root), mode_key)
            console.success(f"Modo KTX selecionado: {ktx_mode.label}.")
            map_name = self.choose_local_map(
                game,
                default_map=ktx_mode.default_map,
                suggested_maps=ktx_mode.suggested_maps,
                label=f"KTX · {ktx_mode.label}",
            )
        else:
            map_name = self.choose_local_map(game)
        self.ensure_local_play_support(games)
        label, runtime = self.choose_host_runtime()
        arguments = ["+sb_listcache", "0", "+spectator", "0"]
        server_settings = (
            KTX_LOCAL_SERVER_SETTINGS if game.key == "ktx" else NQUAKE_LOCAL_SERVER_SETTINGS
        )
        for name, value in server_settings:
            arguments.extend([f"+{name}", value])
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
            for event in ("on_enter", "on_enter_ffa", "on_enter_ctf"):
                arguments.extend(["+tempalias", event, "wait"])
            if ktx_mode.entry_config is not None:
                event = {
                    "ffa": "on_enter_ffa",
                    "ctf": "on_enter_ctf",
                }.get(ktx_mode.usermode, "on_enter")
                arguments.extend(["+tempalias", event, f"exec {ktx_mode.entry_config}"])
            arguments.extend(["+set", "k_defmap", map_name])
            assert ktx_mode is not None
            arguments.extend(["+set", "k_defmode", ktx_mode.usermode])
            arguments.extend(["+set", "x86qw_ktx_preset", ktx_mode.key])
            arguments.extend([
                "+tempalias", "ktx_mode",
                f"echo x86QW KTX preset: {ktx_mode.label} [{ktx_mode.key}]",
            ])
        arguments.extend(["+map", map_name])
        if game.key in PROFILED_LOCAL_GAMES:
            arguments.append("+wait")
            arguments.extend(["+exec", f"x86qw-{game.profile}.cfg"])
        selection = f"{game.label} · {ktx_mode.label}" if ktx_mode is not None else game.label
        console.info(f"Abrindo {selection} no mapa {map_name}...")
        self.launch_runtime(runtime, arguments)
        console.success(f"{label} aberto com {selection}.")

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
        "--mode", metavar="MODO",
        help="seleciona diretamente um modo quando o jogo for KTX",
    )
    parser.add_argument("--target", type=Path, help=argparse.SUPPRESS)
    parser.add_argument(
        "selection", nargs="?",
        help="jogo local: ktx, final-arena, pro-x, team-fortress ou td2",
    )
    namespace = parser.parse_args(arguments)
    namespace.game = None
    if namespace.selection is not None:
        game_keys = {game.key for game in LOCAL_GAMES}
        if namespace.selection.casefold() in game_keys:
            namespace.game = namespace.selection.casefold()
        elif namespace.target is None:
            namespace.target = Path(namespace.selection)
        else:
            parser.error(f"jogo local desconhecido: {namespace.selection}")
    if namespace.mode is not None:
        if namespace.game not in {None, "ktx"}:
            parser.error("--mode só pode ser usado com o jogo KTX")
        namespace.game = "ktx"
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
        player.play_local(options.game, options.mode)
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
