#!/usr/bin/env python3
"""Launcher local dos mods incluídos na distribuição x86QW."""

from __future__ import annotations

import argparse
import hashlib
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
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

sys.dont_write_bytecode = True

core = importlib.import_module("manager")
navigation = importlib.import_module("menu")
from maintenance.tools.runtime_catalog import load_games, games_by_id

InstallerError = core.InstallerError
console = core.console
file_count = core.file_count
file_hash = core.file_hash
lexists = core.lexists
remove_path = core.remove_path

PLAY_SUPPORT_VERSION = "8"
DEVELOPMENT_KTX_MODE_CATALOG = "dist/mods/ktx/1.47/x86qw/modes.json"
RUNTIME_KTX_MODE_CATALOG = "_x86qw/ktx-modes.json"
DEVELOPMENT_GAME_CATALOG = "maintenance/inventory/games.json"
RUNTIME_GAME_CATALOG = "_x86qw/games.json"
LEGACY_MACOS_VIDEO_LAYOUT = Path(".x86qw/launcher/macos-video-layout.json")
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
MACOS_FULLSCREEN_LAYOUT = Path(".x86qw/launcher/macos-fullscreen-layout.json")
MACOS_FULLSCREEN_CVARS = (
    "vid_fullscreen",
    "vid_usedesktopres",
    "vid_width",
    "vid_height",
    "vid_displayfrequency",
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
    protocol: str
    gamecode_type: str
    client_runtimes: tuple[str, ...]
    server_runtimes: tuple[str, ...]
    pre_map_arguments: tuple[str, ...]
    post_map_arguments: tuple[str, ...]
    managed_config: str
    personal_config: str
    required_capabilities: tuple[str, ...]
    smoke_test: str
    local_server_settings: tuple[tuple[str, str], ...]
    legacy_remote_capabilities: tuple[str, ...]
    legacy_components: tuple[str, ...]
    legacy_marker: str | None
    mode_catalog: str | None
    play_support_gamecode: str | None
    dedicated_arguments: tuple[str, ...]
    client_game_arguments: tuple[str, ...]
    pre_connect_arguments: tuple[str, ...]
    client_compatibility_arguments: tuple[str, ...]
    dedicated_settings: tuple[tuple[str, str], ...]


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
    help_commands: tuple[tuple[str, str], ...]
    launch_settings: tuple[tuple[str, str], ...]
    entry_config: str | None
    required_map_asset: str | None
    bots: bool


@dataclass(frozen=True)
class KtxLaunchOptions:
    bots: int = 0
    fill_bots: bool = False
    bot_skill: int = 5
    bot_team: str | None = None
    bot_weapon: str | None = None
    bot_health: int | None = None
    bot_break_on_death: bool = False
    ctf_hook: str | None = None
    ctf_runes: str | None = None
    ctf_based_spawn: bool = False
    race_style: str | None = None
    race_scoring: str | None = None
    race_pacemaker: int | None = None
    race_hide_players: bool = False


def load_local_games(project_root: Path) -> tuple[LocalGameSpec, ...]:
    if core.ZIPAPP_PATH is not None:
        document = core.read_zipapp_json(
            core.ZIPAPP_PATH, RUNTIME_GAME_CATALOG, "Catálogo de jogos da CLI",
        )
    else:
        document = load_games(project_root / DEVELOPMENT_GAME_CATALOG)
    entries = games_by_id(document)
    result: list[LocalGameSpec] = []
    for raw in entries.values():
        result.append(LocalGameSpec(
            key=str(raw["id"]),
            label=str(raw["label"]),
            gamedir=str(raw["gamedir"]),
            profile=str(raw["profile"]),
            component=str(raw["component"]),
            marker=str(raw["marker"]),
            program=str(raw["gamecode"]),
            default_map=str(raw["default_map"]),
            suggested_maps=tuple(str(value) for value in raw["suggested_maps"]),
            version=str(raw["version"]),
            description=str(raw["description"]),
            protocol=str(raw["protocol"]),
            gamecode_type=str(raw["gamecode_type"]),
            client_runtimes=tuple(str(value) for value in raw["client_runtimes"]),
            server_runtimes=tuple(str(value) for value in raw["server_runtimes"]),
            pre_map_arguments=tuple(str(value) for value in raw["pre_map_arguments"]),
            post_map_arguments=tuple(str(value) for value in raw["post_map_arguments"]),
            managed_config=str(raw["managed_config"]),
            personal_config=str(raw["personal_config"]),
            required_capabilities=tuple(str(value) for value in raw["required_capabilities"]),
            smoke_test=str(raw["smoke_test"]),
            local_server_settings=tuple(
                (str(item[0]), str(item[1])) for item in raw.get("local_server_settings", [])
            ),
            legacy_remote_capabilities=tuple(
                str(value) for value in raw.get("legacy_remote_capabilities", [])
            ),
            legacy_components=tuple(str(value) for value in raw.get("legacy_components", [])),
            legacy_marker=str(raw["legacy_marker"]) if raw.get("legacy_marker") else None,
            mode_catalog=str(raw["mode_catalog"]) if raw.get("mode_catalog") else None,
            play_support_gamecode=(
                str(raw["play_support_gamecode"])
                if raw.get("play_support_gamecode") else None
            ),
            dedicated_arguments=tuple(str(value) for value in raw.get("dedicated_arguments", [])),
            client_game_arguments=tuple(
                str(value) for value in raw.get("client_game_arguments", [])
            ),
            pre_connect_arguments=tuple(
                str(value) for value in raw.get("pre_connect_arguments", [])
            ),
            client_compatibility_arguments=tuple(
                str(value) for value in raw.get("client_compatibility_arguments", [])
            ),
            dedicated_settings=tuple(
                (str(item[0]), str(item[1])) for item in raw.get("dedicated_settings", [])
            ),
        ))
    return tuple(result)


LOCAL_GAMES = load_local_games(core.PROJECT_ROOT)
# Compatibility projections for integrations that imported the former constants.
# The canonical values now live in games.json.
KTX_LOCAL_SERVER_SETTINGS = next(
    game.local_server_settings for game in LOCAL_GAMES if game.key == "ktx"
)
NQUAKE_LOCAL_SERVER_SETTINGS = next(
    game.local_server_settings for game in LOCAL_GAMES if game.key != "ktx"
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
        help_commands = raw.get("help_commands")
        launch_settings = raw.get("launch_settings", [])
        entry_config = raw.get("entry_config")
        required_map_asset = raw.get("required_map_asset")
        bots = raw.get("bots", True)
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
            or not isinstance(help_commands, list)
            or not help_commands
            or not all(
                isinstance(entry, list)
                and len(entry) == 2
                and all(isinstance(value, str) and value for value in entry)
                and re.fullmatch(r"[A-Za-z0-9_+./ -]{1,48}", entry[0]) is not None
                and re.fullmatch(r"[A-Za-z0-9 ,.áàâãéêíóôõúçÁÀÂÃÉÊÍÓÔÕÚÇ-]{1,72}", entry[1]) is not None
                for entry in help_commands
            )
            or not isinstance(launch_settings, list)
            or not all(
                isinstance(entry, list)
                and len(entry) == 2
                and isinstance(entry[0], str)
                and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,47}", entry[0]) is not None
                and isinstance(entry[1], str)
                and re.fullmatch(r"[A-Za-z0-9_.+-]{1,64}", entry[1]) is not None
                for entry in launch_settings
            )
            or entry_config not in {None, f"x86qw-ktx-mode-{key}.cfg"}
            or (
                required_map_asset is not None
                and (
                    not isinstance(required_map_asset, str)
                    or re.fullmatch(r"[a-z0-9_./-]*\{map\}[a-z0-9_./-]*", required_map_asset)
                    is None
                    or ".." in PurePosixPath(required_map_asset.replace("{map}", "map")).parts
                )
            )
            or not isinstance(bots, bool)
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
            help_commands=tuple((str(entry[0]), str(entry[1])) for entry in help_commands),
            launch_settings=tuple(
                (str(entry[0]), str(entry[1])) for entry in launch_settings
            ),
            entry_config=entry_config,
            required_map_asset=required_map_asset,
            bots=bots,
        ))
    return tuple(modes)


def ktx_mode_help_alias(mode: KtxModeSpec) -> str:
    return f"exec {ktx_mode_help_config(mode)}"


def ktx_mode_help_config(mode: KtxModeSpec) -> str:
    return f"x86qw-ktx-help-{mode.key}.cfg"


def quote_console_command(command: str) -> str:
    """Keep an ezQuake command body inside one command-line argument."""
    if '"' in command or any(ord(character) < 32 for character in command):
        raise InstallerError("Comando interno do ezQuake contém caracteres inválidos.")
    return f'"{command}"'


def ktx_bot_options_requested(options: KtxLaunchOptions) -> bool:
    return any((
        options.bots,
        options.fill_bots,
        options.bot_skill != 5,
        options.bot_team is not None,
        options.bot_weapon is not None,
        options.bot_health is not None,
        options.bot_break_on_death,
    ))


def ktx_launch_commands(
    mode: KtxModeSpec,
    map_name: str,
    assets: frozenset[str],
    options: KtxLaunchOptions,
) -> tuple[str, ...]:
    commands: list[str] = []
    bot_options = ktx_bot_options_requested(options)
    if bot_options:
        if not mode.bots:
            raise InstallerError(f"Bots Frogbot não são compatíveis com o modo {mode.label}.")
        route = f"bots/maps/{map_name.casefold()}.bot"
        if route not in assets:
            raise InstallerError(
                f"O mapa {map_name} não possui rota Frogbot no pacote KTX ({route})."
            )
        if options.bot_team is not None and not options.bots:
            raise InstallerError("--bot-team exige --bots; o comando fill distribui as equipes.")
        commands.append(f"cmd botcmd skill {options.bot_skill}")
        if options.bot_health is not None:
            commands.append(f"cmd botcmd health {options.bot_health}")
        if options.bot_weapon is not None:
            commands.append(f"cmd botcmd weapon {options.bot_weapon}")
        if options.fill_bots:
            commands.append(f"cmd botcmd fill {options.bot_skill}")
        else:
            team = f" {options.bot_team}" if options.bot_team is not None else ""
            commands.extend(
                f"cmd botcmd addbot {options.bot_skill}{team}"
                for _ in range(options.bots)
            )

    ctf_options = any((
        options.ctf_hook is not None,
        options.ctf_runes is not None,
        options.ctf_based_spawn,
    ))
    if ctf_options and mode.key != "ctf":
        raise InstallerError("Opções --ctf-* só podem ser usadas com o modo KTX ctf.")
    if mode.key == "ctf":
        hook_commands = {
            "off": "nohook",
            "smooth": "hook_smooth",
            "fast": "hook_fast",
            "classic": "hook_classic",
            "crhook": "hook_crhook",
        }
        if options.ctf_hook is not None:
            commands.append(f"cmd {hook_commands[options.ctf_hook]}")
        if options.ctf_runes == "off":
            commands.append("cmd norunes")
        if options.ctf_based_spawn:
            commands.append("cmd ctfbasedspawn")

    race_options = any((
        options.race_style is not None,
        options.race_scoring is not None,
        options.race_pacemaker is not None,
        options.race_hide_players,
    ))
    if race_options and mode.key != "race":
        raise InstallerError("Opções --race-* só podem ser usadas com o modo KTX race.")
    if mode.key == "race":
        if options.race_scoring is not None and options.race_style in {"solo", "simultaneous"}:
            raise InstallerError("--race-scoring exige --race-style match (ou omitir o estilo).")
        if options.race_style == "solo":
            commands.append("cmd race_simultaneous")
        if options.race_style == "match" or (
            options.race_style is None and options.race_scoring is not None
        ):
            commands.append("cmd race_match")
        scoring_steps = {None: 0, "win": 0, "scaled": 1, "formula1": 2}
        commands.extend("cmd race_scoring" for _ in range(scoring_steps[options.race_scoring]))
        if options.race_pacemaker is not None:
            commands.append(f"cmd race_pacemaker {options.race_pacemaker}")
        if options.race_hide_players:
            commands.append("cmd race_hide_players")
    return tuple(commands)


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
            if main.get("spdisplays_connection_type") != "spdisplays_internal":
                return None
            native = str(main.get("spdisplays_pixelresolution", ""))
            resolution = re.fullmatch(r"spdisplays_(\d+)x(\d+)Retina", native)
            if resolution is None:
                raise ValueError("resolução física ausente")
            width, panel_height = (int(value) for value in resolution.groups())
            safe_height = round(width * 10 / 16)
        except (
            OSError, subprocess.SubprocessError, json.JSONDecodeError,
            KeyError, TypeError, ValueError,
        ) as error:
            raise InstallerError(f"Não foi possível detectar o modo fullscreen seguro do macOS: {error}") from error
        if width < 1280 or panel_height < 800:
            raise InstallerError(
                f"Geometria inesperada na tela interna: {width}x{panel_height}."
            )
        notch_height = panel_height - safe_height
        if notch_height <= 0:
            return None
        if safe_height < 800 or notch_height > 256 or notch_height > round(panel_height * 0.08):
            raise InstallerError(
                f"Geometria inesperada na tela com notch: {width}x{panel_height}; "
                f"área segura {width}x{safe_height}."
            )
        return {
            "vid_fullscreen": "1",
            # The desktop-fullscreen mode ignores the explicit dimensions on
            # notched displays and lets the menu occupy the panel's full
            # 3024x1964 area. Select the detected safe mode before ezQuake
            # starts so the engine opens directly at 3024x1890 instead.
            "vid_usedesktopres": "0",
            "vid_width": str(width),
            "vid_height": str(safe_height),
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
                f"Fullscreen macOS definido antes da abertura em "
                f"{desired['vid_width']}x{desired['vid_height']}, com frequência automática."
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
        if marker.is_file() or game.legacy_marker is None:
            return marker
        legacy = self.target.joinpath(*PurePosixPath(game.legacy_marker).parts)
        return legacy if legacy.is_file() else marker

    def game_program_path(self, game: LocalGameSpec) -> Path:
        program = self.target.joinpath(*PurePosixPath(game.program).parts)
        if not program.is_file() and game.legacy_marker is not None:
            legacy = self.target.joinpath(*PurePosixPath(game.legacy_marker).parts)
            if legacy.is_file():
                program = legacy
        if not program.is_file() or program.is_symlink():
            raise InstallerError(f"Gamecode local não encontrado: {program}")
        return program

    def installed_component_for_game(self, game: LocalGameSpec) -> str | None:
        present, _, _ = self.validate_component_pair(game.component)
        if present:
            return game.component
        for legacy_component in game.legacy_components:
            legacy_present, _, _ = self.validate_component_pair(legacy_component)
            if legacy_present:
                return legacy_component
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
        self,
        games: list[LocalGameSpec],
        requested: str | None = None,
        *,
        activity: str = "jogar localmente",
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
        selected = navigation.select_one(
            f"O que deseja {activity}?",
            (
                navigation.MenuOption(
                    game.key,
                    game.label,
                    f"v{self.installed_game_version(game)}",
                    game.description,
                )
                for game in games
            ),
            breadcrumb="x86QW › " + ("Hospedar" if activity == "hospedar" else "Jogar"),
            searchable=True,
        )
        if selected is None:
            raise InstallerError("Nenhum jogo foi selecionado.")
        return next(game for game in games if game.key == selected)

    def choose_ktx_mode(
        self,
        modes: tuple[KtxModeSpec, ...],
        requested: str | None = None,
        *,
        activity: str = "jogar",
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
        selected = navigation.select_one(
            "Qual modo KTX deseja " + activity + "?",
            (
                navigation.MenuOption(
                    mode.key,
                    mode.label,
                    f"{mode.recommended_players} jogador(es)",
                    mode.description,
                    aliases=mode.aliases,
                )
                for mode in modes
            ),
            breadcrumb=f"x86QW › {'Hospedar' if activity == 'hospedar' else 'Jogar'} › KTX",
            searchable=True,
        )
        if selected is None:
            raise InstallerError("Nenhum modo KTX foi selecionado.")
        return next(mode for mode in modes if mode.key == selected)

    @staticmethod
    def show_map_names(maps: list[str]) -> None:
        print("\nTodos os mapas disponíveis:")
        for offset in range(0, len(maps), 6):
            print("  " + "  ".join(f"{name:<16}" for name in maps[offset:offset + 6]).rstrip())

    def ktx_archive_members(self) -> frozenset[str]:
        package = self.target / "qw/ktx.pk3"
        try:
            if package.is_symlink() or not package.is_file() or not zipfile.is_zipfile(package):
                raise InstallerError(f"Pacote KTX inválido: {package}")
            with zipfile.ZipFile(package) as archive:
                return frozenset(name.casefold() for name in archive.namelist())
        except (OSError, zipfile.BadZipFile) as error:
            raise InstallerError(f"Não foi possível consultar os recursos do KTX: {package}") from error

    @staticmethod
    def ktx_required_asset(mode: KtxModeSpec, map_name: str) -> str | None:
        if mode.required_map_asset is None:
            return None
        return mode.required_map_asset.replace("{map}", map_name.casefold())

    def choose_local_map(
        self,
        game: LocalGameSpec,
        *,
        default_map: str | None = None,
        suggested_maps: tuple[str, ...] | None = None,
        label: str | None = None,
        requested_map: str | None = None,
        required_asset: str | None = None,
        available_assets: frozenset[str] | None = None,
    ) -> str:
        maps = self.local_map_names(game.gamedir)
        if required_asset is not None:
            assert available_assets is not None
            maps = [
                name for name in maps
                if required_asset.replace("{map}", name.casefold()).casefold() in available_assets
            ]
            if not maps:
                raise InstallerError(
                    f"Nenhum mapa instalado possui o recurso exigido por {label or game.label}: "
                    f"{required_asset}."
                )
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
        if requested_map is not None:
            selected = lookup.get(requested_map.casefold())
            if selected is None:
                raise InstallerError(
                    f"O mapa {requested_map} não está disponível ou não é compatível com "
                    f"{display_label}."
                )
            return selected
        suggestions = [
            lookup[name.casefold()] for name in requested_suggestions if name.casefold() in lookup
        ]
        ordered = suggestions + [name for name in maps if name not in suggestions]
        default_index = ordered.index(default)
        selected = navigation.select_one(
            f"Escolha o mapa para {display_label}",
            (
                navigation.MenuOption(
                    name,
                    name,
                    "sugerido" if name in suggestions else "",
                    "rota compatível e instalada" if required_asset is not None else "mapa instalado",
                )
                for name in ordered
            ),
            breadcrumb=f"x86QW › {display_label} › Mapa",
            subtitle=f"{len(maps)} mapa(s) compatível(is). Pressione / para buscar.",
            default=default_index,
            searchable=True,
        )
        if selected is None:
            raise InstallerError("Nenhum mapa foi selecionado.")
        return selected

    def choose_ktx_launch_options(
        self,
        mode: KtxModeSpec,
        options: KtxLaunchOptions | None = None,
        *,
        activity: str = "Jogar",
    ) -> KtxLaunchOptions:
        """Collect only the options that are meaningful for the selected KTX mode."""
        selected = options or KtxLaunchOptions()
        breadcrumb = f"x86QW › {activity} › KTX › {mode.label}"
        if mode.key == "race":
            style = navigation.select_one(
                "Formato da corrida",
                (
                    navigation.MenuOption("solo", "Solo", "um corredor por vez"),
                    navigation.MenuOption("simultaneous", "Largada simultânea", "todos percorrem a rota juntos"),
                    navigation.MenuOption("match", "Match competitivo", "rodadas com sistema de pontuação"),
                ),
                breadcrumb=breadcrumb,
                default=0,
            )
            if style is None:
                raise InstallerError("Nenhum formato de Race foi selecionado.")
            scoring = None
            if style == "match":
                scoring = navigation.select_one(
                    "Sistema de pontuação",
                    (
                        navigation.MenuOption("win", "Vitória simples", "vence quem ganhar a rodada"),
                        navigation.MenuOption("scaled", "Escalonada", "pontua conforme a colocação"),
                        navigation.MenuOption("formula1", "Fórmula 1", "pontuação no estilo automobilismo"),
                    ),
                    breadcrumb=breadcrumb + " › Match",
                )
            pacemaker = navigation.select_one(
                "Pacemaker",
                (
                    navigation.MenuOption("none", "Nenhum", "correr sem referência"),
                    *(navigation.MenuOption(str(rank), f"Posição {rank}", "usar esse tempo do ranking") for rank in range(1, 11)),
                ),
                breadcrumb=breadcrumb,
                searchable=True,
            )
            hide_players = navigation.confirm(
                "Ocultar os demais corredores?",
                breadcrumb=breadcrumb,
                default=False,
            )
            return replace(
                selected,
                race_style=style,
                race_scoring=scoring,
                race_pacemaker=None if pacemaker in (None, "none") else int(pacemaker),
                race_hide_players=hide_players,
            )
        if mode.key == "ctf":
            hook = navigation.select_one(
                "Estilo do gancho",
                (
                    navigation.MenuOption("smooth", "Suave", "movimento progressivo"),
                    navigation.MenuOption("fast", "Rápido", "resposta imediata"),
                    navigation.MenuOption("classic", "Clássico", "comportamento tradicional"),
                    navigation.MenuOption("crhook", "CRHook", "variação competitiva"),
                    navigation.MenuOption("off", "Desativado", "partida sem gancho"),
                ),
                breadcrumb=breadcrumb,
            )
            runes = navigation.select_one(
                "Runas",
                (
                    navigation.MenuOption("on", "Ativadas", "regras completas de CTF"),
                    navigation.MenuOption("off", "Desativadas", "CTF sem powerups de runa"),
                ),
                breadcrumb=breadcrumb,
            )
            based_spawn = navigation.confirm(
                "Usar spawn baseado na base?",
                breadcrumb=breadcrumb,
                default=False,
            )
            return replace(
                selected,
                ctf_hook=hook,
                ctf_runes=runes,
                ctf_based_spawn=based_spawn,
            )
        if mode.bots:
            bot_choice = navigation.select_one(
                "Adicionar Frogbots?",
                (
                    navigation.MenuOption("none", "Sem bots", "iniciar somente com jogadores humanos"),
                    navigation.MenuOption("1", "1 bot", "adicionar um oponente"),
                    navigation.MenuOption("2", "2 bots", "partida pequena"),
                    navigation.MenuOption("4", "4 bots", "partida intermediária"),
                    navigation.MenuOption("fill", "Preencher servidor", "até oito Frogbots"),
                    navigation.MenuOption("custom", "Quantidade personalizada", "escolher de 1 a 31"),
                ),
                breadcrumb=breadcrumb,
            )
            if bot_choice not in (None, "none"):
                if bot_choice == "custom":
                    bot_choice = navigation.select_one(
                        "Quantidade de Frogbots",
                        (navigation.MenuOption(str(number), str(number)) for number in range(1, 32)),
                        breadcrumb=breadcrumb + " › Frogbots",
                        searchable=True,
                    )
                skill = navigation.select_one(
                    "Habilidade dos Frogbots",
                    (
                        navigation.MenuOption(
                            str(level), str(level),
                            "iniciante" if level <= 4 else "intermediária" if level <= 10 else "avançada",
                        )
                        for level in range(1, 21)
                    ),
                    breadcrumb=breadcrumb + " › Frogbots",
                    default=4,
                    searchable=True,
                )
                selected = replace(
                    selected,
                    bots=0 if bot_choice == "fill" else int(bot_choice or 0),
                    fill_bots=bot_choice == "fill",
                    bot_skill=int(skill or 5),
                )
        return selected

    def play_local(
        self,
        game_key: str | None = None,
        mode_key: str | None = None,
        map_key: str | None = None,
        ktx_options: KtxLaunchOptions | None = None,
        *,
        configure_interactively: bool = False,
    ) -> None:
        self.check_paks()
        games = self.available_local_games()
        if not games:
            raise InstallerError(
                "Nenhum mod local gerenciado está instalado. Execute components e instale ao menos KTX."
        )
        game = self.choose_local_game(games, game_key)
        uses_mode_catalog = game.mode_catalog is not None
        if mode_key is not None and not uses_mode_catalog:
            raise InstallerError("--mode só pode ser usado com o jogo KTX.")
        if (
            not uses_mode_catalog
            and ktx_options is not None
            and ktx_options != KtxLaunchOptions()
        ):
            raise InstallerError("Opções de bots, CTF e Race só podem ser usadas com o jogo KTX.")
        installed_component = self.installed_component_for_game(game)
        if installed_component is None:
            raise InstallerError(f"O componente de {game.label} não está mais instalado.")
        self.verify_component(installed_component)
        ktx_mode = None
        ktx_assets: frozenset[str] | None = None
        if uses_mode_catalog:
            ktx_mode = self.choose_ktx_mode(load_ktx_modes(self.project_root), mode_key)
            console.success(f"Modo KTX selecionado: {ktx_mode.label}.")
            launch_options = ktx_options or KtxLaunchOptions()
            if configure_interactively:
                launch_options = self.choose_ktx_launch_options(ktx_mode, launch_options)
            ktx_assets = (
                self.ktx_archive_members()
                if ktx_mode.required_map_asset is not None
                or ktx_bot_options_requested(launch_options)
                else frozenset()
            )
            map_name = self.choose_local_map(
                game,
                default_map=ktx_mode.default_map,
                suggested_maps=ktx_mode.suggested_maps,
                label=f"KTX · {ktx_mode.label}",
                requested_map=map_key,
                required_asset=ktx_mode.required_map_asset,
                available_assets=ktx_assets,
            )
        else:
            map_name = self.choose_local_map(game, requested_map=map_key)
        self.verify_local_play_support(games)
        label, runtime = self.choose_host_runtime()
        arguments = ["+sb_listcache", "0", "+spectator", "0"]
        for name, value in game.local_server_settings:
            arguments.extend([f"+{name}", value])
        arguments.extend(game.client_game_arguments)
        arguments.extend(game.pre_connect_arguments)
        if game.legacy_remote_capabilities:
            arguments.extend([
                "+cl_remote_capabilities",
                "$cl_remote_capabilities," + ",".join(game.legacy_remote_capabilities),
            ])
        arguments.extend(game.pre_map_arguments)
        arguments.extend(game.client_compatibility_arguments)
        if uses_mode_catalog:
            assert ktx_mode is not None
            assert ktx_assets is not None
            setup_commands = (
                "tempalias ktx_mode echo "
                f"x86QW KTX preset: {ktx_mode.label} [{ktx_mode.key}]",
                "tempalias x86qw_ktx_mode_help "
                f"exec {ktx_mode_help_config(ktx_mode)}",
                *ktx_launch_commands(
                    ktx_mode, map_name, ktx_assets, launch_options,
                ),
            )
            if ktx_bot_options_requested(launch_options):
                # ToT registers and enables this server cvar from its own
                # usermode. Remove values persisted by older launchers before
                # the QVM loads; otherwise the server rejects their ownership.
                if ktx_mode.key == "tot":
                    arguments.extend([
                        "+unset", "k_fb_enabled", "k_fb_break_on_death",
                    ])
                else:
                    arguments.extend(["+set", "k_fb_enabled", "1"])
                if launch_options.bot_break_on_death and ktx_mode.key != "tot":
                    arguments.extend(["+set", "k_fb_break_on_death", "1"])
            for name, value in ktx_mode.launch_settings:
                arguments.extend([f"+{name}", value])
            setup_body = ";".join((
                "unalias x86qw_ktx_launch_setup", *setup_commands,
            ))
            arguments.extend([
                "+tempalias", "x86qw_ktx_launch_setup",
                quote_console_command(setup_body),
            ])
            event = {
                "ffa": "on_enter_ffa",
                "tot": "on_enter_ffa",
                "ctf": "on_enter_ctf",
            }.get(ktx_mode.usermode, "on_enter")
            if ktx_mode.entry_config is None:
                arguments.extend([
                    "+tempalias", event,
                    quote_console_command(
                        "exec x86qw-ktx.cfg;x86qw_ktx_launch_setup"
                    ),
                ])
            else:
                arguments.extend([
                    "+tempalias", event,
                    f"exec {ktx_mode.entry_config}",
                ])
            arguments.extend(["+set", "k_defmap", map_name])
            arguments.extend(["+set", "k_defmode", ktx_mode.usermode])
        arguments.extend(["+map", map_name])
        if (
            ktx_mode is not None
            and ktx_mode.key == "tot"
            and launch_options.bot_break_on_death
        ):
            arguments.extend(["+k_fb_break_on_death", "1"])
        arguments.extend(game.post_map_arguments)
        selection = f"{game.label} · {ktx_mode.label}" if ktx_mode is not None else game.label
        console.info(f"Abrindo {selection} no mapa {map_name}...")
        self.launch_runtime(runtime, arguments)
        console.success(f"{label} aberto com {selection}.")

    def expected_local_play_support(self, games: list[LocalGameSpec]) -> dict[str, bytes]:
        return {
            game.play_support_gamecode: self.local_game_program(game)
            for game in games
            if game.play_support_gamecode is not None
        }

    def local_play_support_issues(self, games: list[LocalGameSpec]) -> list[str]:
        expected = self.expected_local_play_support(games)
        present, entries, _ = self.validate_component_pair("play-support")
        issues: list[str] = []
        recorded = dict(entries) if present else {}
        expected_hashes = {
            relative: hashlib.sha256(payload).hexdigest()
            for relative, payload in expected.items()
        }
        if recorded != expected_hashes:
            issues.append("recibo play-support ausente ou divergente")
        for relative, digest in expected_hashes.items():
            destination = self.target.joinpath(*PurePosixPath(relative).parts)
            if (
                not destination.is_file()
                or destination.is_symlink()
                or file_hash(destination) != digest
            ):
                issues.append(f"gamecode derivado ausente ou divergente: {relative}")
        for game in games:
            destination = self.target.joinpath(*PurePosixPath(game.personal_config).parts)
            if not destination.is_file() or destination.is_symlink():
                issues.append(f"configuração pessoal ausente: {game.personal_config}")
        return issues

    def verify_local_play_support(self, games: list[LocalGameSpec]) -> None:
        issues = self.local_play_support_issues(games)
        if issues:
            raise InstallerError(
                "Suporte de execução incompleto (" + "; ".join(issues) + "). "
                "Execute update, upgrade ou repair antes de jogar ou hospedar."
            )

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
            for relative, payload in self.expected_local_play_support(games).items():
                destination = self.target.joinpath(*PurePosixPath(relative).parts)
                expected_digest = hashlib.sha256(payload).hexdigest()
                if lexists(destination):
                    if not destination.is_file() or destination.is_symlink():
                        raise InstallerError(f"Suporte local inválido: {destination}")
                    current_digest = file_hash(destination)
                    if current_digest != expected_digest and old.get(relative) != current_digest:
                        console.warning(f"Arquivo pessoal preservado: {destination}")
                        continue
                candidate = managed.joinpath(*PurePosixPath(relative).parts)
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
                self.ensure_game_user_profile(game)
        finally:
            self.cleanup_stage()
            self.stage = previous_stage

    def ensure_game_user_profile(self, game: LocalGameSpec) -> None:
        destination = self.target.joinpath(*PurePosixPath(game.personal_config).parts)
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


def bounded_integer(minimum: int, maximum: int):
    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError as error:
            raise argparse.ArgumentTypeError(f"esperado inteiro entre {minimum} e {maximum}") from error
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(f"esperado inteiro entre {minimum} e {maximum}")
        return number

    return parse


def add_game_launch_arguments(
    parser: argparse.ArgumentParser,
    *,
    dedicated: bool = False,
) -> None:
    parser.add_argument(
        "--mode", metavar="MODO",
        help="seleciona diretamente um modo quando o jogo for KTX",
    )
    parser.add_argument("--map", metavar="MAPA", help="seleciona diretamente um mapa instalado")
    bots = parser.add_mutually_exclusive_group()
    bots.add_argument(
        "--bots", type=bounded_integer(1, 31), metavar="N",
        help="adiciona de 1 a 31 bots Frogbot no KTX",
    )
    bots.add_argument(
        "--fill-bots", action="store_true",
        help="preenche o servidor KTX com até 8 bots Frogbot",
    )
    parser.add_argument(
        "--bot-skill", type=bounded_integer(1, 20), default=5, metavar="1-20",
        help="define a habilidade dos bots (padrão: 5)",
    )
    if not dedicated:
        parser.add_argument(
            "--bot-team", metavar="EQUIPE",
            help="coloca os bots de --bots numa equipe (máximo: 9 caracteres)",
        )
    parser.add_argument(
        "--bot-weapon", choices=("random", *map(str, range(1, 9))), metavar="ARMA",
        help="limita os bots à arma 1-8 ou random",
    )
    parser.add_argument(
        "--bot-health", type=bounded_integer(1, 300), metavar="HP",
        help="define a vida dos bots entre 1 e 300",
    )
    parser.add_argument(
        "--bot-break-on-death", action="store_true",
        help="encerra a tentativa quando o jogador humano morre",
    )
    parser.add_argument(
        "--ctf-hook", choices=("smooth", "fast", "classic", "crhook", "off"),
        help="seleciona o estilo do gancho no CTF",
    )
    parser.add_argument(
        "--ctf-runes", choices=("on", "off"),
        help="mantém ou desativa as runas no CTF",
    )
    parser.add_argument(
        "--ctf-based-spawn", action="store_true",
        help="ativa spawn baseado na base no CTF",
    )
    parser.add_argument(
        "--race-style", choices=("solo", "simultaneous", "match"),
        help="seleciona o formato da corrida",
    )
    parser.add_argument(
        "--race-scoring", choices=("win", "scaled", "formula1"),
        help="seleciona a pontuação do Race match",
    )
    if not dedicated:
        parser.add_argument(
            "--race-pacemaker", type=bounded_integer(1, 10), metavar="RANK",
            help="carrega como pacemaker o tempo da posição 1-10",
        )
        parser.add_argument(
            "--race-hide-players", action="store_true",
            help="oculta os demais corredores",
        )


def resolve_ktx_launch_options(
    parser: argparse.ArgumentParser,
    namespace: argparse.Namespace,
    game: str | None,
) -> tuple[str | None, KtxLaunchOptions]:
    if namespace.mode is not None:
        if game not in {None, "ktx"}:
            parser.error("--mode só pode ser usado com o jogo KTX")
        game = "ktx"
    bot_team = getattr(namespace, "bot_team", None)
    race_pacemaker = getattr(namespace, "race_pacemaker", None)
    race_hide_players = getattr(namespace, "race_hide_players", False)
    if bot_team is not None and re.fullmatch(
        r"[A-Za-z0-9_-]{1,9}", bot_team,
    ) is None:
        parser.error("--bot-team aceita 1 a 9 letras, números, _ ou -")
    ktx_specific = any((
        namespace.bots,
        namespace.fill_bots,
        namespace.bot_skill != 5,
        bot_team is not None,
        namespace.bot_weapon is not None,
        namespace.bot_health is not None,
        namespace.bot_break_on_death,
        namespace.ctf_hook is not None,
        namespace.ctf_runes is not None,
        namespace.ctf_based_spawn,
        namespace.race_style is not None,
        namespace.race_scoring is not None,
        race_pacemaker is not None,
        race_hide_players,
    ))
    if ktx_specific:
        if game not in {None, "ktx"}:
            parser.error("opções de bots, CTF e Race só podem ser usadas com o jogo KTX")
        game = "ktx"
    return game, KtxLaunchOptions(
        bots=namespace.bots or 0,
        fill_bots=namespace.fill_bots,
        bot_skill=namespace.bot_skill,
        bot_team=bot_team,
        bot_weapon=namespace.bot_weapon,
        bot_health=namespace.bot_health,
        bot_break_on_death=namespace.bot_break_on_death,
        ctf_hook=namespace.ctf_hook,
        ctf_runes=namespace.ctf_runes,
        ctf_based_spawn=namespace.ctf_based_spawn,
        race_style=namespace.race_style,
        race_scoring=namespace.race_scoring,
        race_pacemaker=race_pacemaker,
        race_hide_players=race_hide_players,
    )


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
    parser.add_argument("--menu", action="store_true", help=argparse.SUPPRESS)
    add_game_launch_arguments(parser)
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
    namespace.game, namespace.ktx_options = resolve_ktx_launch_options(
        parser, namespace, namespace.game,
    )
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
        navigation.configure(no_color=options.no_color)
        show_banner(options.target)
        player = Player(project_root, options.target)
        player.validate_target("play")
        console.detail(f"Destino normalizado: {player.target}")
        player.reject_target_symlinks()
        console.section("Jogo local")
        player.play_local(
            options.game, options.mode, options.map, options.ktx_options,
            configure_interactively=options.menu or options.mode is None,
        )
        return 0
    except KeyboardInterrupt:
        console.error("Operação cancelada. O jogo não foi iniciado.")
        return 130
    except navigation.MenuCancelled:
        console.info("Operação cancelada; o jogo não foi iniciado.")
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
