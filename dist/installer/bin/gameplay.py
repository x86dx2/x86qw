#!/usr/bin/env python3
"""Launcher local dos mods incluídos na distribuição x86QW."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import random
import re
import shlex
import stat
import struct
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

sys.dont_write_bytecode = True

core = importlib.import_module("manager")
navigation = importlib.import_module("menu")
from maintenance.tools.runtime_catalog import load_games, games_by_id
from x86qw_runtime.io.archive import (
    ArchiveError,
    read_archive_member,
    scan_archive,
)
from x86qw_runtime.io import private_fs

InstallerError = core.InstallerError
console = core.console
file_count = core.file_count
file_hash = core.file_hash
lexists = core.lexists
remove_path = core.remove_path

PLAY_SUPPORT_VERSION = "8"
DEVELOPMENT_KTX_MODE_CATALOG = "dist/mods/ktx/1.47/x86qw/catalog/modes.json"
RUNTIME_KTX_MODE_CATALOG = "_x86qw/ktx-modes.json"
DEVELOPMENT_KTX_BOT_NAME_CATALOG = (
    "dist/mods/ktx/1.47/x86qw/catalog/frogbots/names.json"
)
RUNTIME_KTX_BOT_NAME_CATALOG = "_x86qw/ktx-frogbot-names.json"
KTX_CONTEXT_KEYS = ("F5", "F6", "H", "I", "M", "X", "Z", "F11")
KTX_RUNTIME_CONFIG_PLACEHOLDER = "__X86QW_KTX_RUNTIME_CONFIG__"
# ezQuake accepts large alias bodies, but a single startup argument close to
# its command buffer limit can leave initialization incomplete. Larger mode
# plans are therefore loaded from the same private ephemeral configuration
# already used by Frogbot sessions.
KTX_INLINE_SETUP_LIMIT = 1400
FROGBOT_RUNTIME_CONFIG_PLACEHOLDER = KTX_RUNTIME_CONFIG_PLACEHOLDER
FROGBOT_ADD_WAIT_FRAMES = 8
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
    bot_names_personal_config: str | None
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
    key_bindings: tuple[tuple[str, str, str], ...]
    launch_settings: tuple[tuple[str, str], ...]
    entry_config: str | None
    map_requirements: tuple[KtxMapRequirement, ...]
    bots: bool
    bot_teams: tuple[str, ...]


@dataclass(frozen=True)
class KtxMapRequirement:
    key: str
    asset: str
    when: str
    label: str


@dataclass(frozen=True)
class KtxMenuGroupSpec:
    key: str
    label: str
    description: str
    modes: tuple[str, ...]


@dataclass(frozen=True)
class FrogbotIdentity:
    name: str


@dataclass(frozen=True)
class KtxLaunchOptions:
    bots: int = 0
    fill_bots: bool = False
    bot_skill: int | str = 5
    bot_team: str | None = None
    bot_weapon: str | None = None
    bot_health: int | None = None
    bot_break_on_death: bool | None = None
    bot_names_profile: str = "default"
    bot_name_pool: tuple[FrogbotIdentity, ...] = ()
    ctf_hook: str | None = None
    ctf_runes: str | None = None
    ctf_based_spawn: bool = False
    race_style: str | None = None
    race_scoring: str | None = None
    race_pacemaker: int | None = None
    race_hide_players: bool = False


@dataclass(frozen=True)
class KtxRuntimeConfig:
    path: Path
    device: int
    inode: int
    lease: object


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
            bot_names_personal_config=(
                str(raw["bot_names_personal_config"])
                if raw.get("bot_names_personal_config") else None
            ),
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


def read_ktx_mode_catalog(project_root: Path) -> dict[str, object]:
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
    if not isinstance(catalog, dict):
        raise InstallerError("Catálogo de modos KTX inválido.")
    return catalog


def load_ktx_modes(project_root: Path) -> tuple[KtxModeSpec, ...]:
    catalog = read_ktx_mode_catalog(project_root)
    if catalog.get("format") != 1 or catalog.get("game") != "ktx":
        raise InstallerError("Catálogo de modos KTX possui identidade inválida.")
    raw_policies = catalog.get("map_asset_policies")
    defaults = catalog.get("defaults")
    if not isinstance(raw_policies, dict) or not raw_policies:
        raise InstallerError("Catálogo de modos KTX não declara políticas de mapas.")
    if not isinstance(defaults, dict):
        raise InstallerError("Catálogo de modos KTX não declara padrões de mapas.")
    policies: dict[str, KtxMapRequirement] = {}
    for policy_key, raw_policy in raw_policies.items():
        asset_path = (
            PurePosixPath(raw_policy["asset"].replace("{map}", "map"))
            if isinstance(raw_policy, dict) and isinstance(raw_policy.get("asset"), str)
            else None
        )
        if (
            not isinstance(policy_key, str)
            or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", policy_key) is None
            or not isinstance(raw_policy, dict)
            or set(raw_policy) != {"asset", "when", "label"}
            or not isinstance(raw_policy.get("asset"), str)
            or re.fullmatch(
                r"[a-z0-9_./-]*\{map\}[a-z0-9_./-]*", raw_policy["asset"],
            ) is None
            or asset_path is None
            or asset_path.is_absolute()
            or any(part in {"", ".", ".."} for part in asset_path.parts)
            or "//" in raw_policy["asset"]
            or raw_policy.get("when") not in {"always", "frogbots"}
            or not isinstance(raw_policy.get("label"), str)
            or not raw_policy["label"]
        ):
            raise InstallerError(f"Política de mapa KTX inválida: {policy_key!r}.")
        policies[policy_key] = KtxMapRequirement(
            policy_key,
            str(raw_policy["asset"]),
            str(raw_policy["when"]),
            str(raw_policy["label"]),
        )
    default_bot_policies = defaults.get("frogbot_map_policies")
    if (
        not isinstance(default_bot_policies, list)
        or not default_bot_policies
        or not all(isinstance(value, str) and value in policies for value in default_bot_policies)
        or len(default_bot_policies) != len(set(default_bot_policies))
        or any(policies[value].when != "frogbots" for value in default_bot_policies)
    ):
        raise InstallerError("Políticas padrão de mapas Frogbot inválidas.")
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
        key_bindings = raw.get("key_bindings")
        launch_settings = raw.get("launch_settings", [])
        entry_config = raw.get("entry_config")
        raw_mode_policies = raw.get("map_policies", [])
        bots = raw.get("bots", True)
        bot_teams = raw.get("bot_teams", [])
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
            or len({name.casefold() for name in suggested_maps}) != len(suggested_maps)
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
            or not isinstance(key_bindings, list)
            or not 3 <= len(key_bindings) <= len(KTX_CONTEXT_KEYS)
            or not all(
                isinstance(entry, list)
                and len(entry) == 3
                and entry[0] in KTX_CONTEXT_KEYS
                and isinstance(entry[1], str)
                and re.fullmatch(r"[A-Za-z0-9_+./ -]{1,48}", entry[1]) is not None
                and isinstance(entry[2], str)
                and re.fullmatch(
                    r"[A-Za-z0-9 ,.áàâãéêíóôõúçÁÀÂÃÉÊÍÓÔÕÚÇ-]{1,72}",
                    entry[2],
                ) is not None
                for entry in key_bindings
            )
            or len({entry[0] for entry in key_bindings}) != len(key_bindings)
            or not {"F5", "F6", "F11"}.issubset(
                {entry[0] for entry in key_bindings}
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
            or not isinstance(raw_mode_policies, list)
            or not all(
                isinstance(value, str) and value in policies
                for value in raw_mode_policies
            )
            or len(raw_mode_policies) != len(set(raw_mode_policies))
            or any(
                policies[value].when == "frogbots" for value in raw_mode_policies
            )
            or not isinstance(bots, bool)
            or not isinstance(bot_teams, list)
            or len(bot_teams) > 4
            or not all(
                isinstance(team, str)
                and re.fullmatch(r"[A-Za-z0-9_-]{1,9}", team) is not None
                for team in bot_teams
            )
            or len({team.casefold() for team in bot_teams}) != len(bot_teams)
            or any(not isinstance(raw.get(field), str) or not raw[field] for field in text_fields)
        ):
            raise InstallerError(f"Definição inválida do modo KTX: {key!r}.")
        if raw["default_map"].casefold() not in {name.casefold() for name in suggested_maps}:
            raise InstallerError(f"Mapa padrão ausente nas sugestões do modo KTX {key}.")
        if (
            bot_teams
            and re.fullmatch(r"[1-9][0-9]*", str(raw["recommended_players"]))
            and int(raw["recommended_players"]) % len(bot_teams) != 0
        ):
            raise InstallerError(
                f"Formação de equipes incompatível com o modo KTX {key}."
            )
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
            key_bindings=tuple(
                (str(entry[0]), str(entry[1]), str(entry[2]))
                for entry in key_bindings
            ),
            launch_settings=tuple(
                (str(entry[0]), str(entry[1])) for entry in launch_settings
            ),
            entry_config=entry_config,
            map_requirements=tuple(
                policies[value]
                for value in (
                    [*default_bot_policies, *raw_mode_policies]
                    if bots else raw_mode_policies
                )
            ),
            bots=bots,
            bot_teams=tuple(str(team) for team in bot_teams),
        ))
    return tuple(modes)


def load_ktx_menu_groups(project_root: Path) -> tuple[KtxMenuGroupSpec, ...]:
    catalog = read_ktx_mode_catalog(project_root)
    raw_groups = catalog.get("menu_groups")
    if not isinstance(raw_groups, list) or not raw_groups:
        raise InstallerError("Catálogo de modos KTX não declara grupos de navegação.")
    mode_ids = {mode.key for mode in load_ktx_modes(project_root)}
    groups: list[KtxMenuGroupSpec] = []
    identities: set[str] = set()
    covered: set[str] = set()
    for raw in raw_groups:
        if not isinstance(raw, dict):
            raise InstallerError("Grupo de navegação KTX inválido.")
        key = raw.get("id")
        label = raw.get("label")
        description = raw.get("description")
        members = raw.get("modes")
        if (
            not isinstance(key, str)
            or re.fullmatch(r"[a-z][a-z0-9-]{0,31}", key) is None
            or key in identities
            or not isinstance(label, str)
            or not label
            or not isinstance(description, str)
            or not description
            or not isinstance(members, list)
            or not members
            or not all(isinstance(member, str) and member in mode_ids for member in members)
            or len(set(members)) != len(members)
        ):
            raise InstallerError(f"Grupo de navegação KTX inválido: {key!r}.")
        identities.add(key)
        covered.update(members)
        groups.append(KtxMenuGroupSpec(key, label, description, tuple(members)))
    if covered != mode_ids:
        missing = ", ".join(sorted(mode_ids - covered))
        raise InstallerError(f"Modos KTX sem grupo de navegação: {missing}.")
    return tuple(groups)


def player_count_label(value: str) -> str:
    if value == "1":
        return "1 jogador"
    return f"{value} jogadores"


def ktx_options_cli_arguments(options: KtxLaunchOptions) -> list[str]:
    arguments: list[str] = []
    if options.fill_bots:
        arguments.append("--fill-bots")
    elif options.bots:
        arguments.extend(["--bots", str(options.bots)])
    if options.bots or options.fill_bots:
        arguments.extend(["--bot-skill", str(options.bot_skill)])
        if options.bot_names_profile != "default":
            arguments.extend(["--bot-names", options.bot_names_profile])
        if options.bot_team is not None:
            arguments.extend(["--bot-team", options.bot_team])
        if options.bot_weapon is not None:
            arguments.extend(["--bot-weapon", options.bot_weapon])
        if options.bot_health is not None:
            arguments.extend(["--bot-health", str(options.bot_health)])
        if options.bot_break_on_death is True:
            arguments.append("--bot-break-on-death")
        elif options.bot_break_on_death is False:
            arguments.append("--no-bot-break-on-death")
    if options.ctf_hook is not None:
        arguments.extend(["--ctf-hook", options.ctf_hook])
    if options.ctf_runes is not None:
        arguments.extend(["--ctf-runes", options.ctf_runes])
    if options.ctf_based_spawn:
        arguments.append("--ctf-based-spawn")
    if options.race_style is not None:
        arguments.extend(["--race-style", options.race_style])
    if options.race_scoring is not None:
        arguments.extend(["--race-scoring", options.race_scoring])
    if options.race_pacemaker is not None:
        arguments.extend(["--race-pacemaker", str(options.race_pacemaker)])
    if options.race_hide_players:
        arguments.append("--race-hide-players")
    return arguments


def public_command(arguments: list[str]) -> str:
    launcher = "x86qw.cmd" if os.name == "nt" else "./x86qw.sh"
    command = [launcher, *arguments]
    return subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)


def ktx_summary_lines(options: KtxLaunchOptions) -> list[str]:
    lines: list[str] = []
    if options.fill_bots:
        lines.append(f"  Bots    | preencher servidor · habilidade {options.bot_skill}")
    elif options.bots:
        lines.append(f"  Bots    | {options.bots} · habilidade {options.bot_skill}")
    else:
        lines.append("  Bots    | sem bots")
    if options.bots or options.fill_bots:
        names = {
            "default": "KTX Default",
            "x86qw": "x86QW aleatório",
            "personal": "lista pessoal",
        }.get(options.bot_names_profile, options.bot_names_profile)
        lines.append(f"  Nomes   | {names}")
        if options.bot_team is not None:
            lines.append(f"  Equipe  | {options.bot_team}")
        if options.bot_weapon is not None:
            lines.append(f"  Arma    | {options.bot_weapon}")
        if options.bot_health is not None:
            lines.append(f"  Vida    | {options.bot_health} HP")
        if options.bot_break_on_death is not None:
            lines.append(
                "  Morte   | "
                + ("encerra a tentativa" if options.bot_break_on_death else "mantém a tentativa")
            )
    if options.ctf_hook is not None:
        lines.append(
            "  CTF     | gancho " + options.ctf_hook
            + f" · runas {options.ctf_runes or 'padrão'}"
            + (" · spawn na base" if options.ctf_based_spawn else "")
        )
    if options.race_style is not None:
        race = options.race_style
        if options.race_scoring is not None:
            race += f" · pontuação {options.race_scoring}"
        if options.race_pacemaker is not None:
            race += f" · pacemaker {options.race_pacemaker}"
        if options.race_hide_players:
            race += " · jogadores ocultos"
        lines.append(f"  Race    | {race}")
    return lines


def play_summary_text(
    game: LocalGameSpec,
    mode: KtxModeSpec | None,
    map_name: str,
    runtime_label: str,
    options: KtxLaunchOptions,
) -> str:
    lines = ["Resumo da partida", f"  Jogo    | {game.label}"]
    if mode is not None:
        lines.append(f"  Modo    | {mode.label}")
    lines.extend((f"  Mapa    | {map_name}", f"  Cliente | {runtime_label}"))
    if mode is not None:
        lines.extend(ktx_summary_lines(options))
    cli = ["play", game.key]
    if mode is not None:
        cli.extend(["--mode", mode.key])
        cli.extend(ktx_options_cli_arguments(options))
    cli.extend(["--map", map_name])
    lines.extend(("", "Comando equivalente:", "  " + public_command(cli)))
    return "\n".join(lines)


def print_play_summary(
    game: LocalGameSpec,
    mode: KtxModeSpec | None,
    map_name: str,
    runtime_label: str,
    options: KtxLaunchOptions,
) -> None:
    print("\n" + play_summary_text(game, mode, map_name, runtime_label, options))


def validate_frogbot_name(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .'-]{0,12}", value) is None
        or len(value) > 13
    ):
        raise InstallerError(
            f"Nome Frogbot inválido em {label}: use de 1 a 13 caracteres ASCII."
        )
    return value


def validate_frogbot_name_document(
    document: object,
    *,
    profile: str,
    label: str,
) -> tuple[FrogbotIdentity, ...]:
    if (
        not isinstance(document, dict)
        or document.get("format") != 1
        or document.get("game") != "ktx"
        or document.get("profile") != profile
        or document.get("prefix") != "/"
        or document.get("color") != "quake-high-bit"
    ):
        raise InstallerError(f"Catálogo de nomes Frogbot inválido: {label}")
    raw_identities: list[object]
    if profile == "x86qw":
        if document.get("theme") != "one-piece" or document.get("randomize") is not True:
            raise InstallerError(f"Catálogo de nomes Frogbot inválido: {label}")
        groups = document.get("groups")
        if not isinstance(groups, list) or not groups:
            raise InstallerError(f"Catálogo de nomes Frogbot vazio: {label}")
        raw_identities = []
        group_ids: set[str] = set()
        for group in groups:
            identifier = group.get("id") if isinstance(group, dict) else None
            characters = group.get("characters") if isinstance(group, dict) else None
            if (
                not isinstance(identifier, str)
                or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", identifier) is None
                or identifier in group_ids
                or not isinstance(characters, list)
                or not characters
            ):
                raise InstallerError(f"Grupo de nomes Frogbot inválido: {label}")
            group_ids.add(identifier)
            raw_identities.extend(characters)
    else:
        personal_characters = document.get("characters")
        legacy_names = document.get("names")
        if isinstance(personal_characters, list) and legacy_names is None:
            raw_identities = personal_characters
        elif isinstance(legacy_names, list) and personal_characters is None:
            raw_identities = legacy_names
        else:
            raise InstallerError(f"Lista pessoal de nomes Frogbot inválida: {label}")
    identities: list[FrogbotIdentity] = []
    for value in raw_identities:
        if isinstance(value, str) and profile == "personal":
            identities.append(FrogbotIdentity(validate_frogbot_name(value, label)))
            continue
        if not isinstance(value, dict) or set(value) != {"name"}:
            raise InstallerError(f"Identidade Frogbot inválida: {label}")
        name = validate_frogbot_name(value.get("name"), label)
        identities.append(FrogbotIdentity(name))
    result = tuple(identities)
    if len({identity.name.casefold() for identity in result}) != len(result):
        raise InstallerError(f"Lista de nomes Frogbot contém duplicatas: {label}")
    return result


def load_x86qw_frogbot_names(project_root: Path) -> tuple[FrogbotIdentity, ...]:
    if core.ZIPAPP_PATH is not None:
        document = core.read_zipapp_json(
            core.ZIPAPP_PATH,
            RUNTIME_KTX_BOT_NAME_CATALOG,
            "Catálogo de nomes Frogbot",
        )
        label = RUNTIME_KTX_BOT_NAME_CATALOG
    else:
        path = project_root / DEVELOPMENT_KTX_BOT_NAME_CATALOG
        label = str(path)
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise InstallerError(f"Catálogo de nomes Frogbot inválido: {path}") from error
    return validate_frogbot_name_document(document, profile="x86qw", label=label)


def load_personal_frogbot_names(target: Path, relative: str) -> tuple[FrogbotIdentity, ...]:
    path = target.joinpath(*PurePosixPath(relative).parts)
    if path.is_symlink() or not path.is_file():
        raise InstallerError(
            f"Lista pessoal de nomes Frogbot ausente ou insegura: {path}. "
            "Execute repair ou edite o arquivo criado pelo bootstrap."
        )
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InstallerError(f"Lista pessoal de nomes Frogbot inválida: {path}") from error
    return validate_frogbot_name_document(document, profile="personal", label=str(path))


def requested_frogbot_names(
    options: KtxLaunchOptions,
    mode: KtxModeSpec | None = None,
) -> int:
    if not options.fill_bots:
        return options.bots
    limit = ktx_mode_bot_limit(mode) if mode is not None else None
    return limit if limit is not None else 8


def resolve_frogbot_name_profile(
    project_root: Path,
    target: Path,
    game: LocalGameSpec,
    options: KtxLaunchOptions,
    mode: KtxModeSpec | None = None,
    *,
    generator: random.Random | random.SystemRandom | None = None,
) -> KtxLaunchOptions:
    count = requested_frogbot_names(options, mode)
    if options.bot_names_profile == "default" or count == 0:
        return replace(options, bot_name_pool=())
    if options.bot_names_profile == "x86qw":
        pool = load_x86qw_frogbot_names(project_root)
        randomizer = generator or random.SystemRandom()
        pool = tuple(randomizer.sample(pool, len(pool)))
    elif options.bot_names_profile == "personal":
        if game.bot_names_personal_config is None:
            raise InstallerError(f"{game.label} não declara uma lista pessoal de bots.")
        pool = load_personal_frogbot_names(target, game.bot_names_personal_config)
    else:
        raise InstallerError(f"Perfil de nomes Frogbot desconhecido: {options.bot_names_profile}")
    if len(pool) < count:
        raise InstallerError(
            f"O perfil {options.bot_names_profile} possui {len(pool)} nome(s), "
            f"mas este lançamento pode criar {count} Frogbot(s)."
        )
    return replace(options, bot_name_pool=pool)


def quake_colored_frogbot_name(name: str) -> str:
    # RGB sequences are forbidden in QuakeWorld player names. Encode the
    # legacy high-bit glyphs explicitly so the QVM receives at most 15 bytes.
    colored = "".join(
        "$xa0" if character == " " else f"$x{ord(character) | 0x80:02x}"
        for character in name
    )
    return f"/$xa0{colored}"


def frogbot_identity(value: FrogbotIdentity | str) -> FrogbotIdentity:
    if isinstance(value, FrogbotIdentity):
        return value
    return FrogbotIdentity(validate_frogbot_name(value, "opções de lançamento"))


def ktx_bot_name_settings(
    options: KtxLaunchOptions,
    mode: KtxModeSpec | None = None,
) -> tuple[tuple[str, str], ...]:
    count = requested_frogbot_names(options, mode)
    if not options.bot_name_pool or count == 0:
        return ()
    pool = options.bot_name_pool
    if len(pool) < count:
        raise InstallerError("O perfil de nomes não cobre todos os Frogbots solicitados.")
    prefixes = ("k_fb_name", "k_fb_name_team", "k_fb_name_enemy")
    settings: list[tuple[str, str]] = []
    for prefix in prefixes:
        for index in range(count):
            # A bot keeps the same identity if KTX classifies it as generic,
            # allied or enemy. Together with the QVM's global bot index, this
            # prevents cross-role duplicates even in large rosters.
            identity = frogbot_identity(pool[index])
            settings.append((
                f"{prefix}_{index}", quake_colored_frogbot_name(identity.name),
            ))
    return tuple(settings)


def quake_colored_frogbot_bytes(name: str) -> bytes:
    return b"/\xa0" + bytes(
        ord(character) | 0x80
        for character in name
    )


def ktx_bot_name_binary_settings(
    options: KtxLaunchOptions,
    mode: KtxModeSpec | None = None,
) -> tuple[tuple[str, bytes], ...]:
    count = requested_frogbot_names(options, mode)
    if not options.bot_name_pool or count == 0:
        return ()
    pool = options.bot_name_pool
    if len(pool) < count:
        raise InstallerError("O perfil de nomes não cobre todos os Frogbots solicitados.")
    prefixes = ("k_fb_name", "k_fb_name_team", "k_fb_name_enemy")
    settings: list[tuple[str, bytes]] = []
    for prefix in prefixes:
        for index in range(count):
            identity = frogbot_identity(pool[index])
            settings.append((
                f"{prefix}_{index}", quake_colored_frogbot_bytes(identity.name),
            ))
    return tuple(settings)


def write_ktx_runtime_config(
    target: Path,
    settings: tuple[tuple[str, str], ...],
    startup_commands: tuple[str, ...] = (),
) -> KtxRuntimeConfig:
    directory = target / "qw"
    if directory.is_symlink() or not directory.is_dir():
        raise InstallerError(f"Diretório de configuração do ezQuake inválido: {directory}")
    if any(
        not command or any(ord(character) < 32 for character in command)
        for command in startup_commands
    ):
        raise InstallerError("Configuração efêmera KTX contém comando inválido.")
    payload = "".join((
        "// x86QW: ephemeral KTX launch configuration\n",
        # Older launchers allowed these user cvars to reach config.cfg. Clear
        # them before the QVM registers the KTX defaults or this launch sets a
        # temporary custom pool.
        "unset_re ^k_fb_name_\n",
        *(f"set_ex {name} $qt{value}$qt\n" for name, value in settings),
        *(f"{command}\n" for command in startup_commands),
    )).encode("ascii")
    descriptor = -1
    temporary_name = ""
    try:
        descriptor, temporary = private_fs.private_mkstemp(
            prefix="x86qw-ktx-session-", suffix=".cfg", directory=directory,
        )
        temporary_name = str(temporary)
        pending = memoryview(payload)
        while pending:
            written = os.write(descriptor, pending)
            if written == 0:
                raise OSError("gravação incompleta da configuração efêmera")
            pending = pending[written:]
        os.fsync(descriptor)
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
            descriptor = -1
        if temporary_name:
            try:
                Path(temporary_name).unlink()
            except OSError:
                pass
        raise InstallerError(
            f"Não foi possível preparar a sessão KTX: {error}"
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    path = Path(temporary_name)
    lease: object | None = None
    try:
        private_fs.validate_private_file(path)
        lease = private_fs.hold_private_path(path, directory=False)
        metadata = path.stat(follow_symlinks=False)
    except OSError as error:
        if lease is not None:
            try:
                lease.close()
            except OSError:
                pass
        try:
            private_fs.unlink_private_file(path)
        except OSError:
            pass
        raise InstallerError(
            f"Não foi possível validar a sessão KTX temporária: {error}"
        ) from error
    return KtxRuntimeConfig(path, metadata.st_dev, metadata.st_ino, lease)


def remove_ktx_runtime_config(config: KtxRuntimeConfig) -> bool:
    valid = False
    try:
        metadata = config.path.stat(follow_symlinks=False)
    except FileNotFoundError:
        valid = True
    except OSError:
        valid = False
    else:
        valid = (
            metadata.st_dev == config.device
            and metadata.st_ino == config.inode
            and stat.S_ISREG(metadata.st_mode)
            and not config.path.is_symlink()
        )
    try:
        config.lease.close()
    except OSError:
        return False
    if not valid:
        return False
    if not lexists(config.path):
        return True
    try:
        private_fs.unlink_private_file(
            config.path,
            expected_identity=(config.device, config.inode),
        )
    except OSError:
        return False
    return True


def ktx_external_assets(target: Path) -> frozenset[str]:
    """Discover safe user-editable KTX map assets outside the managed PK3."""
    locations = (
        (target / "qw/bots/maps", "bots/maps", ".bot"),
        (target / "qw/race/routes", "race/routes", ".route"),
        (target / "id1/maps/ctf", "id1/maps/ctf", ".ent"),
    )
    assets: set[str] = set()
    for directory, virtual_root, suffix in locations:
        if not directory.is_dir() or directory.is_symlink():
            continue
        for path in directory.iterdir():
            if (
                path.is_file()
                and not path.is_symlink()
                and path.suffix.casefold() == suffix
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.#-]{0,63}", path.stem)
                and valid_personal_ktx_asset(path, suffix)
            ):
                assets.add(f"{virtual_root}/{path.name}".casefold())
    return frozenset(assets)


def valid_personal_ktx_asset(path: Path, suffix: str) -> bool:
    """Reject route/config payloads that could execute unrelated commands."""
    try:
        metadata = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= 1024 * 1024:
            return False
        payload = path.read_bytes()
    except OSError:
        return False
    if b"\0" in payload or any(
        byte < 32 and byte not in {9, 10, 13} for byte in payload
    ):
        return False
    lines = [
        line.lstrip()
        for line in payload.decode("latin-1").splitlines()
        if line.strip() and not line.lstrip().startswith("//")
    ]
    if suffix == ".ent":
        # Entity overrides are engine data, not console scripts. Require the
        # normal entity envelope and reject console command separators.
        text = "\n".join(lines)
        return (
            text.startswith("{")
            and text.endswith("}")
            and text.count('"classname" "item_flag_team1"') == 1
            and text.count('"classname" "item_flag_team2"') == 1
            and ";" not in text
        )
    commands = [line.split(None, 1)[0] for line in lines]
    if any(";" in line for line in lines):
        return False
    if suffix == ".bot":
        allowed = {
            "CreateMarker", "SetGoal", "SetMarkerFlag", "SetMarkerPath",
            "SetMarkerPathAngleHint", "SetMarkerPathFlags",
            "SetMarkerViewOfs", "SetRocketJumpPathFields", "SetZone",
        }
        return bool(commands) and "CreateMarker" in commands and all(
            command in allowed for command in commands
        )
    if suffix == ".route":
        allowed = {
            "race_add_route_node", "race_route_add_end", "race_route_add_start",
            "race_set_node_size", "race_set_route_falsestart_mode",
            "race_set_route_name", "race_set_route_timeout",
            "race_set_route_weapon_mode", "race_set_teleport_flags_by_name",
        }
        return (
            bool(commands)
            and all(command in allowed for command in commands)
            and "race_add_route_node" in commands
            and commands.count("race_route_add_start")
            == commands.count("race_route_add_end")
        )
    return False


# Compatibility names for integrations that used the Frogbot-specific helper.
FrogbotRuntimeConfig = KtxRuntimeConfig
write_frogbot_runtime_config = write_ktx_runtime_config
remove_frogbot_runtime_config = remove_ktx_runtime_config


def ktx_mode_help_alias(mode: KtxModeSpec) -> str:
    del mode
    return "x86qw_ktx_mode_help"


def ktx_key_alias_commands(
    mode: KtxModeSpec,
    options: KtxLaunchOptions,
) -> tuple[str, ...]:
    commands: list[str] = []
    for key in KTX_CONTEXT_KEYS:
        alias_key = key.casefold()
        commands.extend((
            f"tempalias x86qw_ktx_key_{alias_key} $qt$qt",
            f"tempalias x86qw_ktx_key_help_{alias_key} $qt$qt",
        ))
    for key, command, description in mode.key_bindings:
        alias_key = key.casefold()
        colored_key = "".join(f"^{character}" for character in key)
        commands.extend((
            f"tempalias x86qw_ktx_key_{alias_key} {command}",
            "tempalias x86qw_ktx_key_help_"
            f"{alias_key} echo {colored_key}$x20$x7c$x20{description}",
        ))
    if ktx_bot_options_requested(options):
        commands.extend(ktx_bot_management_alias_commands(mode, options))
    return tuple(commands)


def ktx_bot_management_alias_commands(
    mode: KtxModeSpec,
    options: KtxLaunchOptions,
) -> tuple[str, ...]:
    """Create a bounded, reversible runtime roster and skill state machine."""
    initial_count = requested_frogbot_names(options, mode)
    fixed_limit = ktx_mode_bot_limit(mode)
    maximum = fixed_limit if fixed_limit is not None else 31
    commands: list[str] = []
    explicit_team = f" {options.bot_team}" if options.bot_team is not None else ""
    for count in range(maximum + 1):
        if count < maximum:
            action = (
                "x86qw_ktx_bot_add_command;"
                f"x86qw_ktx_bot_roster_{count + 1}"
            )
        else:
            action = "echo Limite de Frogbots deste modo atingido"
        commands.append(
            f"tempalias x86qw_ktx_bot_add_{count} {quote_console_command(action)}"
        )
        if count > 0:
            action = (
                "cmd botcmd removebot;"
                f"x86qw_ktx_bot_roster_{count - 1}"
            )
        else:
            action = "echo Nenhum Frogbot para remover"
        commands.append(
            f"tempalias x86qw_ktx_bot_remove_{count} {quote_console_command(action)}"
        )
        roster = (
            f"tempalias x86qw_ktx_bot_add x86qw_ktx_bot_add_{count};"
            f"tempalias x86qw_ktx_bot_remove x86qw_ktx_bot_remove_{count}"
        )
        commands.append(
            f"tempalias x86qw_ktx_bot_roster_{count} {quote_console_command(roster)}"
        )
    if options.bot_skill == "random":
        random_message = "echo Habilidade aleatoria ativa para cada novo Frogbot"
        commands.extend((
            "tempalias x86qw_ktx_bot_add_command "
            f"{quote_console_command(f'cmd botcmd addbot random{explicit_team}')}",
            f"tempalias x86qw_ktx_bot_skill_down {quote_console_command(random_message)}",
            f"tempalias x86qw_ktx_bot_skill_up {quote_console_command(random_message)}",
        ))
    else:
        for skill in range(1, 21):
            lower = max(1, skill - 1)
            higher = min(20, skill + 1)
            down_action = (
                "echo Habilidade dos proximos Frogbots: "
                f"{lower};cmd botcmd skill {lower};x86qw_ktx_bot_skill_{lower}"
            )
            up_action = (
                "echo Habilidade dos proximos Frogbots: "
                f"{higher};cmd botcmd skill {higher};x86qw_ktx_bot_skill_{higher}"
            )
            skill_setup = ";".join((
                f"tempalias x86qw_ktx_bot_skill_down x86qw_ktx_bot_skill_down_{skill}",
                f"tempalias x86qw_ktx_bot_skill_up x86qw_ktx_bot_skill_up_{skill}",
                "tempalias x86qw_ktx_bot_add_command "
                f"cmd botcmd addbot {skill}{explicit_team}",
            ))
            commands.extend((
                f"tempalias x86qw_ktx_bot_skill_down_{skill} "
                f"{quote_console_command(down_action)}",
                f"tempalias x86qw_ktx_bot_skill_up_{skill} "
                f"{quote_console_command(up_action)}",
                f"tempalias x86qw_ktx_bot_skill_{skill} "
                f"{quote_console_command(skill_setup)}",
            ))
        commands.append(f"x86qw_ktx_bot_skill_{int(options.bot_skill)}")
    commands.extend((
        f"x86qw_ktx_bot_roster_{initial_count}",
        "tempalias x86qw_ktx_bot_help exec x86qw-ktx-help-bots.cfg",
    ))
    return tuple(commands)


def quote_console_command(command: str) -> str:
    """Keep an ezQuake command body inside one command-line argument."""
    if '"' in command or any(ord(character) < 32 for character in command):
        raise InstallerError("Comando interno do ezQuake contém caracteres inválidos.")
    return f'"{command}"'


def ktx_chunked_setup_alias_commands(
    commands: tuple[str, ...], *, maximum_body: int = 700,
) -> tuple[str, ...]:
    """Store a long post-map setup in bounded temporary aliases."""
    chunks: list[list[str]] = []
    current: list[str] = []
    current_length = 0
    for command in commands:
        if len(command) > maximum_body:
            raise InstallerError("Comando de inicialização KTX excede o limite seguro.")
        added = len(command) + (1 if current else 0)
        if current and current_length + added > maximum_body:
            chunks.append(current)
            current = []
            current_length = 0
            added = len(command)
        current.append(command)
        current_length += added
    if current:
        chunks.append(current)
    aliases = tuple(
        f"x86qw_ktx_launch_setup_{index}"
        for index in range(1, len(chunks) + 1)
    )
    definitions = list(
        f"tempalias {alias} {quote_console_command(';'.join(chunk))}"
        for alias, chunk in zip(aliases, chunks)
    )
    current_aliases = aliases
    level = 1
    while len(";".join(current_aliases)) > maximum_body:
        parent_aliases: list[str] = []
        group: list[str] = []
        group_length = 0
        for alias in current_aliases:
            added = len(alias) + (1 if group else 0)
            if group and group_length + added > maximum_body:
                parent = f"x86qw_ktx_launch_group_{level}_{len(parent_aliases) + 1}"
                definitions.append(
                    f"tempalias {parent} {quote_console_command(';'.join(group))}"
                )
                parent_aliases.append(parent)
                group = []
                group_length = 0
                added = len(alias)
            group.append(alias)
            group_length += added
        if group:
            parent = f"x86qw_ktx_launch_group_{level}_{len(parent_aliases) + 1}"
            definitions.append(
                f"tempalias {parent} {quote_console_command(';'.join(group))}"
            )
            parent_aliases.append(parent)
        current_aliases = tuple(parent_aliases)
        level += 1
    definitions.append(
        "tempalias x86qw_ktx_launch_setup "
        f"{quote_console_command(';'.join(current_aliases))}"
    )
    return tuple(definitions)


def ktx_bot_options_requested(options: KtxLaunchOptions) -> bool:
    return any((
        options.bots,
        options.fill_bots,
        options.bot_skill != 5,
        options.bot_team is not None,
        options.bot_weapon is not None,
        options.bot_health is not None,
        options.bot_break_on_death is not None,
    ))


def active_ktx_map_requirements(
    mode: KtxModeSpec,
    options: KtxLaunchOptions,
) -> tuple[KtxMapRequirement, ...]:
    """Return the map assets required by the actual launch context."""
    frogbots = ktx_bot_options_requested(options)
    return tuple(
        requirement
        for requirement in mode.map_requirements
        if requirement.when == "always"
        or (requirement.when == "frogbots" and frogbots)
    )


def required_ktx_map_assets(
    mode: KtxModeSpec,
    options: KtxLaunchOptions,
) -> tuple[str, ...]:
    return tuple(
        requirement.asset for requirement in active_ktx_map_requirements(mode, options)
    )


def without_frogbots(options: KtxLaunchOptions) -> KtxLaunchOptions:
    """Clear every transient Frogbot choice when the menu says Sem bots."""
    return replace(
        options,
        bots=0,
        fill_bots=False,
        bot_skill=5,
        bot_team=None,
        bot_weapon=None,
        bot_health=None,
        bot_break_on_death=None,
        bot_names_profile="default",
        bot_name_pool=(),
    )


def ktx_mode_bot_limit(mode: KtxModeSpec) -> int | None:
    """Return the remaining fixed slots after the local human player."""
    if re.fullmatch(r"[1-9][0-9]*", mode.recommended_players) is None:
        return None
    return max(0, min(31, int(mode.recommended_players) - 1))


def ktx_mode_roster_description(mode: KtxModeSpec) -> str:
    if mode.bot_teams and re.fullmatch(r"[1-9][0-9]*", mode.recommended_players):
        players_per_team = int(mode.recommended_players) // len(mode.bot_teams)
        return f"{len(mode.bot_teams)} equipes de {players_per_team} jogadores"
    return f"completar {mode.recommended_players} jogadores"


def validate_ktx_bot_count(mode: KtxModeSpec, options: KtxLaunchOptions) -> None:
    limit = ktx_mode_bot_limit(mode)
    requested = requested_frogbot_names(options, mode)
    if limit is not None and requested > limit:
        noun = "Frogbot" if limit == 1 else "Frogbots"
        raise InstallerError(
            f"{mode.label} aceita no máximo {limit} {noun} com um jogador humano."
        )


def ktx_bot_team_sequence(
    mode: KtxModeSpec,
    options: KtxLaunchOptions,
) -> tuple[str | None, ...]:
    """Balance selected bots against the local player in the declared teams."""
    count = requested_frogbot_names(options, mode)
    if options.bot_team is not None:
        return (options.bot_team,) * count
    if not mode.bot_teams:
        return (None,) * count
    populations = [1, *(0 for _team in mode.bot_teams[1:])]
    result: list[str] = []
    for _index in range(count):
        selected = min(range(len(mode.bot_teams)), key=lambda index: populations[index])
        result.append(mode.bot_teams[selected])
        populations[selected] += 1
    return tuple(result)


def ktx_launch_commands(
    mode: KtxModeSpec,
    map_name: str,
    assets: frozenset[str],
    options: KtxLaunchOptions,
) -> tuple[str, ...]:
    commands: list[str] = []
    for requirement in active_ktx_map_requirements(mode, options):
        asset = requirement.asset.replace("{map}", map_name.casefold()).casefold()
        if asset not in assets:
            raise InstallerError(
                f"O mapa {map_name} não possui o recurso {requirement.label} "
                f"exigido pelo modo {mode.label} ({asset})."
            )
    bot_options = ktx_bot_options_requested(options)
    if bot_options:
        if not mode.bots:
            raise InstallerError(f"Bots Frogbot não são compatíveis com o modo {mode.label}.")
        validate_ktx_bot_count(mode, options)
        if options.bot_team is not None and not options.bots:
            raise InstallerError("--bot-team exige --bots; o comando fill distribui as equipes.")
        if (
            options.bot_team is not None
            and mode.bot_teams
            and options.bot_team.casefold() not in {
                team.casefold() for team in mode.bot_teams
            }
        ):
            expected = ", ".join(mode.bot_teams)
            raise InstallerError(
                f"A equipe {options.bot_team} não pertence ao modo {mode.label}; "
                f"use uma destas: {expected}."
            )
        if mode.key != "tot" and any((
            options.bot_weapon is not None,
            options.bot_health is not None,
            options.bot_break_on_death is not None,
        )):
            raise InstallerError(
                "--bot-weapon, --bot-health e --[no-]bot-break-on-death "
                "só podem ser usados com o modo KTX tot."
            )
        bot_count = requested_frogbot_names(options, mode)
        if not options.fill_bots or mode.bot_teams:
            required_clients = bot_count + 1
            commands.extend((
                f"if ($k_maxclients < {required_clients}) then k_maxclients {required_clients}",
                f"if ($maxclients < {required_clients}) then maxclients {required_clients}",
            ))
        if mode.bot_teams and options.bot_team is None:
            commands.append(f"team {mode.bot_teams[0]}")
        commands.append(f"cmd botcmd skill {options.bot_skill}")
        if options.bot_health is not None:
            commands.append(f"cmd botcmd health {options.bot_health}")
        if options.bot_weapon is not None:
            commands.append(f"cmd botcmd weapon {options.bot_weapon}")
        if options.fill_bots and not mode.bot_teams:
            commands.append(f"cmd botcmd fill {options.bot_skill}")
        else:
            team_sequence = ktx_bot_team_sequence(mode, options)
            for index, selected_team in enumerate(team_sequence):
                # Without an explicit override, let KTX apply its native team
                # colors and role-aware names while the patched gamecode uses
                # the declared usermode's team count for balancing.
                team = (
                    f" {selected_team}"
                    if options.bot_team is not None and selected_team is not None
                    else ""
                )
                commands.append(f"cmd botcmd addbot {options.bot_skill}{team}")
                if index + 1 < len(team_sequence):
                    commands.extend(("wait",) * FROGBOT_ADD_WAIT_FRAMES)
        if options.bot_name_pool:
            # set_ex creates user cvars in ezQuake. Remove them after KTX has
            # copied the names so they never leak into the player's config.
            # The final client command still needs a server frame to reach the
            # local QVM, just like the commands between successive bots.
            commands.extend(("wait",) * FROGBOT_ADD_WAIT_FRAMES)
            setting_names = [
                name for name, _value in ktx_bot_name_settings(options, mode)
            ]
            for offset in range(0, len(setting_names), 12):
                commands.append("unset " + " ".join(
                    setting_names[offset:offset + 12]
                ))

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
        path = PurePosixPath(member)
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
                plan = scan_archive(package)
            except (ArchiveError, OSError) as error:
                raise InstallerError(f"Pacote de mapas inválido: {package}") from error
            for member in plan.members:
                if member.kind == "file" and (
                    name := self.map_name_from_member(member.path.as_posix())
                ):
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
    ) -> LocalGameSpec | None:
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
            allow_back=True,
        )
        if selected is None:
            return None
        return next(game for game in games if game.key == selected)

    def choose_ktx_mode(
        self,
        modes: tuple[KtxModeSpec, ...],
        requested: str | None = None,
        *,
        activity: str = "jogar",
    ) -> KtxModeSpec | None:
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
        root_breadcrumb = f"x86QW › {'Hospedar' if activity == 'hospedar' else 'Jogar'} › KTX"
        groups = load_ktx_menu_groups(self.project_root)
        while True:
            group_key = navigation.select_one(
                "Que tipo de modo deseja explorar?",
                (
                    *(
                        navigation.MenuOption(
                            group.key, group.label, group.description,
                            f"{len(group.modes)} modos",
                        )
                        for group in groups
                    ),
                    navigation.MenuOption(
                        "all", "Todos os modos", "catálogo KTX completo",
                        f"{len(modes)} modos com busca",
                    ),
                ),
                breadcrumb=root_breadcrumb,
                allow_back=True,
            )
            if group_key is None:
                return None
            group = next((item for item in groups if item.key == group_key), None)
            visible = modes if group is None else tuple(
                mode for mode in modes if mode.key in group.modes
            )
            selected = navigation.select_one(
                "Qual modo KTX deseja " + activity + "?",
                (
                    navigation.MenuOption(
                        mode.key,
                        mode.label,
                        player_count_label(mode.recommended_players),
                        mode.description,
                        aliases=mode.aliases,
                    )
                    for mode in visible
                ),
                breadcrumb=root_breadcrumb + " › " + (
                    group.label if group is not None else "Todos os modos"
                ),
                subtitle="Use a busca para localizar um modo neste grupo.",
                searchable=True,
                allow_back=True,
            )
            if selected is not None:
                return next(mode for mode in modes if mode.key == selected)

    @staticmethod
    def show_map_names(maps: list[str]) -> None:
        print("\nTodos os mapas disponíveis:")
        for offset in range(0, len(maps), 6):
            print("  " + "  ".join(f"{name:<16}" for name in maps[offset:offset + 6]).rstrip())

    def ktx_archive_members(self) -> frozenset[str]:
        package = self.target / "qw/ktx.pk3"
        try:
            plan = scan_archive(package)
            managed = frozenset(
                member.path.as_posix().casefold()
                for member in plan.members
                if member.kind == "file"
            )
            return managed | ktx_external_assets(self.target)
        except (ArchiveError, OSError) as error:
            raise InstallerError(f"Não foi possível consultar os recursos do KTX: {package}") from error

    @staticmethod
    def ktx_required_assets(
        mode: KtxModeSpec,
        options: KtxLaunchOptions,
        map_name: str,
    ) -> tuple[str, ...]:
        return tuple(
            asset.replace("{map}", map_name.casefold())
            for asset in required_ktx_map_assets(mode, options)
        )

    def choose_local_map(
        self,
        game: LocalGameSpec,
        *,
        default_map: str | None = None,
        suggested_maps: tuple[str, ...] | None = None,
        label: str | None = None,
        requested_map: str | None = None,
        required_asset: str | None = None,
        required_assets: tuple[str, ...] = (),
        available_assets: frozenset[str] | None = None,
        breadcrumb: str | None = None,
    ) -> str | None:
        maps = self.local_map_names(game.gamedir)
        effective_assets = (
            (required_asset,) if required_asset is not None else ()
        ) + required_assets
        if effective_assets:
            assert available_assets is not None
            maps = [
                name for name in maps
                if all(
                    asset.replace("{map}", name.casefold()).casefold() in available_assets
                    for asset in effective_assets
                )
            ]
            if not maps:
                raise InstallerError(
                    f"Nenhum mapa instalado possui o recurso exigido por {label or game.label}: "
                    f"{', '.join(effective_assets)}."
                )
        lookup = {name.casefold(): name for name in maps}
        requested_default = default_map or game.default_map
        requested_suggestions = suggested_maps or game.suggested_maps
        display_label = label or game.label
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
        default = lookup.get(requested_default.casefold())
        if default is None:
            default = suggestions[0] if suggestions else (maps[0] if maps else None)
        if default is None:
            raise InstallerError(
                f"Nenhum mapa compatível está disponível para {display_label}."
            )
        ordered = suggestions + [name for name in maps if name not in suggestions]
        default_index = ordered.index(default)
        selected = navigation.select_one(
            f"Escolha o mapa para {display_label}",
            (
                navigation.MenuOption(
                    name,
                    name,
                    "sugerido" if name in suggestions else "",
                    "recursos compatíveis e instalados" if effective_assets else "mapa instalado",
                )
                for name in ordered
            ),
            breadcrumb=breadcrumb or f"x86QW › {display_label} › Mapa",
            subtitle=(
                f"{len(maps)} mapa(s) compatível(is) com esta configuração. "
                "Use a busca para filtrar."
            ),
            default=default_index,
            searchable=True,
            allow_back=True,
        )
        if selected is None:
            return None
        return selected

    def choose_ktx_launch_options(
        self,
        mode: KtxModeSpec,
        options: KtxLaunchOptions | None = None,
        *,
        activity: str = "Jogar",
    ) -> KtxLaunchOptions | None:
        """Collect only the options that are meaningful for the selected KTX mode."""
        selected = options or KtxLaunchOptions()
        breadcrumb = f"x86QW › {activity} › KTX › {mode.label}"
        if mode.key == "race":
            while True:
                style = navigation.select_one(
                    "Formato da corrida",
                    (
                        navigation.MenuOption("solo", "Solo", "um corredor por vez"),
                        navigation.MenuOption("simultaneous", "Largada simultânea", "todos percorrem a rota juntos"),
                        navigation.MenuOption("match", "Match competitivo", "rodadas com sistema de pontuação"),
                    ),
                    breadcrumb=breadcrumb,
                    default=0,
                    allow_back=True,
                )
                if style is None:
                    return None
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
                        allow_back=True,
                    )
                    if scoring is None:
                        continue
                while True:
                    pacemaker = navigation.select_one(
                        "Pacemaker",
                        (
                            navigation.MenuOption("none", "Nenhum", "correr sem referência"),
                            *(navigation.MenuOption(str(rank), f"Posição {rank}", "usar esse tempo do ranking") for rank in range(1, 11)),
                        ),
                        breadcrumb=breadcrumb,
                        searchable=True,
                        allow_back=True,
                    )
                    if pacemaker is None:
                        break
                    hide_players = navigation.confirm(
                        "Ocultar os demais corredores?",
                        breadcrumb=breadcrumb,
                        default=False,
                        allow_back=True,
                    )
                    if hide_players is None:
                        continue
                    return replace(
                        selected,
                        race_style=style,
                        race_scoring=scoring,
                        race_pacemaker=None if pacemaker == "none" else int(pacemaker),
                        race_hide_players=hide_players,
                    )
        if mode.key == "ctf":
            while True:
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
                    allow_back=True,
                )
                if hook is None:
                    return None
                while True:
                    runes = navigation.select_one(
                        "Runas",
                        (
                            navigation.MenuOption("on", "Ativadas", "regras completas de CTF"),
                            navigation.MenuOption("off", "Desativadas", "CTF sem powerups de runa"),
                        ),
                        breadcrumb=breadcrumb,
                        allow_back=True,
                    )
                    if runes is None:
                        break
                    based_spawn = navigation.confirm(
                        "Usar spawn baseado na base?",
                        breadcrumb=breadcrumb,
                        default=False,
                        allow_back=True,
                    )
                    if based_spawn is None:
                        continue
                    return replace(
                        selected,
                        ctf_hook=hook,
                        ctf_runes=runes,
                        ctf_based_spawn=based_spawn,
                    )
        if mode.bots:
            fixed_bot_limit = ktx_mode_bot_limit(mode)
            if fixed_bot_limit is None:
                bot_menu_options = (
                    navigation.MenuOption("none", "Sem bots", "iniciar somente com jogadores humanos"),
                    navigation.MenuOption("1", "1 bot", "adicionar um oponente"),
                    navigation.MenuOption("2", "2 bots", "partida pequena"),
                    navigation.MenuOption("4", "4 bots", "partida intermediária"),
                    navigation.MenuOption(
                        "fill", "Preencher servidor", "até oito Frogbots neste modo aberto",
                    ),
                    navigation.MenuOption("custom", "Quantidade personalizada", "escolher de 1 a 31"),
                )
            else:
                bot_menu_options = (
                    navigation.MenuOption("none", "Sem bots", "iniciar somente com jogadores humanos"),
                    navigation.MenuOption(
                        str(fixed_bot_limit),
                        "1 bot" if fixed_bot_limit == 1 else f"{fixed_bot_limit} bots",
                        ktx_mode_roster_description(mode),
                    ),
                )
            while True:
                bot_choice = navigation.select_one(
                    "Adicionar Frogbots?",
                    bot_menu_options,
                    breadcrumb=breadcrumb,
                    allow_back=True,
                )
                if bot_choice is None:
                    return None
                if bot_choice == "none":
                    return without_frogbots(selected)
                chosen_custom = bot_choice == "custom"
                if chosen_custom:
                    bot_choice = navigation.select_one(
                        "Quantidade de Frogbots",
                        (
                            navigation.MenuOption(str(number), str(number))
                            for number in range(1, (fixed_bot_limit or 31) + 1)
                        ),
                        breadcrumb=breadcrumb + " › Frogbots",
                        searchable=True,
                        allow_back=True,
                    )
                    if bot_choice is None:
                        continue
                while True:
                    skill = navigation.select_one(
                        "Habilidade dos Frogbots",
                        (
                            navigation.MenuOption(
                                "random", "Aleatória", "cada bot recebe habilidade de 1 a 20",
                            ),
                            *(navigation.MenuOption(
                                str(level), str(level),
                                "iniciante" if level <= 4 else "intermediária" if level <= 10 else "avançada",
                            ) for level in range(1, 21)),
                        ),
                        breadcrumb=breadcrumb + " › Frogbots",
                        default=5,
                        searchable=True,
                        allow_back=True,
                    )
                    if skill is None:
                        if chosen_custom:
                            bot_choice = navigation.select_one(
                                "Quantidade de Frogbots",
                                (
                                    navigation.MenuOption(str(number), str(number))
                                    for number in range(1, (fixed_bot_limit or 31) + 1)
                                ),
                                breadcrumb=breadcrumb + " › Frogbots",
                                searchable=True,
                                allow_back=True,
                            )
                            if bot_choice is None:
                                break
                            continue
                        break
                    names_profile = navigation.select_one(
                        "Nomes dos Frogbots",
                        (
                            navigation.MenuOption(
                                "default", "KTX Default", "nomes originais sem customização",
                            ),
                            navigation.MenuOption(
                                "x86qw", "x86QW aleatório",
                                "Chapéus de Palha, lendas e grandes potências",
                            ),
                            navigation.MenuOption(
                                "personal", "Lista pessoal",
                                "usar qw/x86qw-frogbot-names.json na ordem declarada",
                            ),
                        ),
                        breadcrumb=breadcrumb + " › Frogbots",
                        default=1,
                        allow_back=True,
                    )
                    if names_profile is None:
                        continue
                    configured = replace(
                        selected,
                        bots=0 if bot_choice == "fill" else int(bot_choice),
                        fill_bots=bot_choice == "fill",
                        bot_skill="random" if skill == "random" else int(skill),
                        bot_names_profile=names_profile,
                    )
                    if mode.key != "tot":
                        return configured
                    weapon = navigation.select_one(
                        "Arma dos Frogbots no ToT",
                        (
                            navigation.MenuOption(
                                "default", "Padrão do mapa", "usar a configuração ToT carregada",
                            ),
                            navigation.MenuOption("random", "Aleatória", "sortear entre as armas"),
                            *(navigation.MenuOption(str(number), f"Arma {number}") for number in range(1, 9)),
                        ),
                        breadcrumb=breadcrumb + " › Frogbots › ToT",
                        allow_back=True,
                    )
                    if weapon is None:
                        continue
                    health = navigation.select_one(
                        "Vida inicial dos Frogbots no ToT",
                        (
                            navigation.MenuOption(
                                "default", "Padrão do mapa", "usar a configuração ToT carregada",
                            ),
                            *(navigation.MenuOption(str(number), str(number), "HP") for number in range(1, 301)),
                        ),
                        breadcrumb=breadcrumb + " › Frogbots › ToT",
                        default=0,
                        searchable=True,
                        allow_back=True,
                    )
                    if health is None:
                        continue
                    break_on_death = navigation.select_one(
                        "Encerrar tentativa quando o jogador morrer?",
                        (
                            navigation.MenuOption(
                                "default", "Padrão do mapa", "preservar dm4, e1m2 ou schloss",
                            ),
                            navigation.MenuOption("on", "Sim", "encerrar a tentativa"),
                            navigation.MenuOption("off", "Não", "continuar a tentativa"),
                        ),
                        breadcrumb=breadcrumb + " › Frogbots › ToT",
                        allow_back=True,
                    )
                    if break_on_death is None:
                        continue
                    return replace(
                        configured,
                        bot_weapon=None if weapon == "default" else weapon,
                        bot_health=None if health == "default" else int(health),
                        bot_break_on_death=(
                            None if break_on_death == "default"
                            else break_on_death == "on"
                        ),
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
                "Nenhum mod local gerenciado está instalado. Reexecute o bootstrap x86QW "
                "no mesmo destino e adicione ao menos KTX."
        )
        game: LocalGameSpec | None = None
        ktx_mode: KtxModeSpec | None = None
        ktx_assets: frozenset[str] | None = None
        launch_options = ktx_options or KtxLaunchOptions()
        base_launch_options = launch_options
        previous_mode_key: str | None = None
        map_name: str | None = None
        runtime_choice: tuple[str, Path] | None = None
        state = "game"
        while True:
            if state == "game":
                game = self.choose_local_game(games, game_key)
                if game is None:
                    return
                uses_mode_catalog = game.mode_catalog is not None
                if mode_key is not None and not uses_mode_catalog:
                    raise InstallerError("--mode só pode ser usado com o jogo KTX.")
                if (
                    not uses_mode_catalog
                    and ktx_options is not None
                    and ktx_options != KtxLaunchOptions()
                ):
                    raise InstallerError(
                        "Opções de bots, CTF e Race só podem ser usadas com o jogo KTX."
                    )
                installed_component = self.installed_component_for_game(game)
                if installed_component is None:
                    raise InstallerError(f"O componente de {game.label} não está mais instalado.")
                self.verify_component(installed_component)
                ktx_mode = None
                launch_options = ktx_options or KtxLaunchOptions()
                state = "mode" if uses_mode_catalog else "map"
                continue
            assert game is not None
            uses_mode_catalog = game.mode_catalog is not None
            if state == "mode":
                ktx_mode = self.choose_ktx_mode(
                    load_ktx_modes(self.project_root), mode_key,
                )
                if ktx_mode is None:
                    state = "game"
                    continue
                if previous_mode_key is not None and previous_mode_key != ktx_mode.key:
                    launch_options = base_launch_options
                previous_mode_key = ktx_mode.key
                console.success(f"Modo KTX selecionado: {ktx_mode.label}.")
                state = "options" if configure_interactively else "map"
                continue
            if state == "options":
                assert ktx_mode is not None
                chosen_options = self.choose_ktx_launch_options(ktx_mode, launch_options)
                if chosen_options is None:
                    state = "mode"
                    continue
                launch_options = chosen_options
                state = "map"
                continue
            if state == "map":
                if uses_mode_catalog:
                    assert ktx_mode is not None
                    launch_options = resolve_frogbot_name_profile(
                        self.project_root, self.target, game, launch_options, ktx_mode,
                    )
                    required_assets = required_ktx_map_assets(
                        ktx_mode, launch_options,
                    )
                    ktx_assets = (
                        self.ktx_archive_members() if required_assets else frozenset()
                    )
                    map_name = self.choose_local_map(
                        game,
                        default_map=ktx_mode.default_map,
                        suggested_maps=ktx_mode.suggested_maps,
                        label=f"KTX · {ktx_mode.label}",
                        requested_map=map_key,
                        required_assets=required_assets,
                        available_assets=ktx_assets,
                        breadcrumb=f"x86QW › Jogar › KTX › {ktx_mode.label} › Mapa",
                    )
                else:
                    map_name = self.choose_local_map(
                        game, requested_map=map_key,
                        breadcrumb=f"x86QW › Jogar › {game.label} › Mapa",
                    )
                if map_name is None:
                    state = "options" if uses_mode_catalog and configure_interactively else (
                        "mode" if uses_mode_catalog else "game"
                    )
                    continue
                state = "runtime"
                continue
            runtime_choice = self.choose_host_runtime(
                breadcrumb=(
                    f"x86QW › Jogar › KTX › {ktx_mode.label} › Cliente"
                    if ktx_mode is not None
                    else f"x86QW › Jogar › {game.label} › Cliente"
                ),
            )
            if runtime_choice is None:
                state = "map"
                continue
            if configure_interactively:
                label, _runtime = runtime_choice
                summary = play_summary_text(
                    game, ktx_mode, map_name, label, launch_options,
                )
                confirmed = navigation.confirm(
                    "Iniciar esta partida?",
                    breadcrumb=(
                        f"x86QW › Jogar › KTX › {ktx_mode.label} › Confirmação"
                        if ktx_mode is not None
                        else f"x86QW › Jogar › {game.label} › Confirmação"
                    ),
                    subtitle="\n" + summary,
                    description="abrir o jogo com as escolhas acima",
                    default=True,
                    allow_back=True,
                )
                if confirmed is None:
                    runtime_choice = None
                    state = "runtime"
                    continue
                if not confirmed:
                    console.info("Partida cancelada; nenhum cliente foi aberto.")
                    return
            break
        assert game is not None
        assert map_name is not None
        self.verify_local_play_support(games)
        label, runtime = runtime_choice
        arguments = [
            "+sb_listcache", "0", "+spectator", "0",
            "+bind", "F12", "quit",
        ]
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
        ktx_startup_commands: tuple[str, ...] = ()
        if uses_mode_catalog:
            assert ktx_mode is not None
            assert ktx_assets is not None
            bot_name_settings = ktx_bot_name_settings(launch_options, ktx_mode)
            key_alias_commands = (
                "tempalias ktx_mode echo "
                f"x86QW KTX preset: {ktx_mode.label} [{ktx_mode.key}]",
                *ktx_key_alias_commands(ktx_mode, launch_options),
            )
            post_map_commands = (
                *ktx_launch_commands(
                    ktx_mode, map_name, ktx_assets, launch_options,
                ),
                ktx_mode_help_alias(ktx_mode),
            )
            setup_commands = (*key_alias_commands, *post_map_commands)
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
            for name, value in ktx_mode.launch_settings:
                arguments.extend([f"+{name}", value])
            setup_body = ";".join((
                "unalias x86qw_ktx_launch_setup", *setup_commands,
            ))
            event = {
                "ffa": "on_enter_ffa",
                "tot": "on_enter_ffa",
                "ctf": "on_enter_ctf",
            }.get(ktx_mode.usermode, "on_enter")
            if ktx_mode.entry_config is None:
                event_body = "exec x86qw-ktx.cfg;x86qw_ktx_launch_setup"
            else:
                event_body = f"exec {ktx_mode.entry_config}"
            if (
                ktx_bot_options_requested(launch_options)
                or len(setup_body) > KTX_INLINE_SETUP_LIMIT
            ):
                # Keep the long, frame-separated addbot sequence outside the
                # engine's bounded startup command line. The ephemeral file is
                # read before the map and removed immediately after startup.
                ktx_startup_commands = (
                    *(
                        key_alias_commands
                        if ktx_bot_options_requested(launch_options)
                        else ()
                    ),
                    *ktx_chunked_setup_alias_commands(
                        post_map_commands
                        if ktx_bot_options_requested(launch_options)
                        else setup_commands
                    ),
                    f"tempalias {event} {quote_console_command(event_body)}",
                )
                arguments.extend([
                    "+exec", KTX_RUNTIME_CONFIG_PLACEHOLDER,
                ])
            else:
                event_argument = (
                    quote_console_command(event_body)
                    if ktx_mode.entry_config is None
                    else event_body
                )
                arguments.extend([
                    "+tempalias", "x86qw_ktx_launch_setup",
                    quote_console_command(setup_body),
                    "+tempalias", event,
                    event_argument,
                ])
            arguments.extend(["+set", "k_defmap", map_name])
            arguments.extend(["+set", "k_defmode", ktx_mode.usermode])
        arguments.extend(["+map", map_name])
        if (
            ktx_mode is not None
            and ktx_mode.key == "tot"
            and launch_options.bot_break_on_death is not None
        ):
            arguments.extend([
                "+k_fb_break_on_death",
                "1" if launch_options.bot_break_on_death else "0",
            ])
        arguments.extend(game.post_map_arguments)
        # This is an invariant of the launcher, not a suggested profile bind.
        # Keep it after every managed and personal config so no mod can leave
        # the user without the universal emergency exit.
        arguments.extend(["+bind", "F12", "quit"])
        selection = f"{game.label} · {ktx_mode.label}" if ktx_mode is not None else game.label
        console.info(f"Abrindo {selection} no mapa {map_name}...")
        runtime_config = None
        if uses_mode_catalog and ktx_startup_commands:
            runtime_config = write_ktx_runtime_config(
                self.target, bot_name_settings, ktx_startup_commands,
            )
            arguments = [
                argument.replace(
                    KTX_RUNTIME_CONFIG_PLACEHOLDER, runtime_config.path.name,
                )
                for argument in arguments
            ]
        try:
            process = self.launch_runtime(runtime, arguments)
            if runtime_config is not None and isinstance(process, subprocess.Popen):
                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise InstallerError(
                            "O ezQuake encerrou antes de carregar a configuração KTX."
                        )
                    time.sleep(0.05)
        finally:
            if (
                runtime_config is not None
                and not remove_ktx_runtime_config(runtime_config)
            ):
                console.warning(
                    "Configuração efêmera KTX preservada porque "
                    f"sua identidade mudou: {runtime_config.path}"
                )
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
                plan = scan_archive(package, required_members=("qwprogs.dat",))
                return read_archive_member(plan, "qwprogs.dat")
            except (ArchiveError, KeyError, OSError) as error:
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


def bot_skill(value: str) -> int | str:
    if value.casefold() == "random":
        return "random"
    return bounded_integer(1, 20)(value)


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
        help="preenche as vagas do modo KTX; modos abertos usam até 8 Frogbots",
    )
    parser.add_argument(
        "--bot-skill", type=bot_skill, default=None, metavar="1-20|random",
        help="define a habilidade dos bots ou sorteia 1-20 por bot (padrão: 5)",
    )
    if not dedicated:
        parser.add_argument(
            "--bot-team", metavar="EQUIPE",
            help="coloca os bots de --bots numa equipe (máximo: 9 caracteres)",
        )
    parser.add_argument(
        "--bot-weapon", choices=("random", *map(str, range(1, 9))), metavar="ARMA",
        help="no ToT, limita os bots à arma 1-8 ou random",
    )
    parser.add_argument(
        "--bot-health", type=bounded_integer(1, 300), metavar="HP",
        help="no ToT, define a vida dos bots entre 1 e 300",
    )
    break_on_death = parser.add_mutually_exclusive_group()
    break_on_death.add_argument(
        "--bot-break-on-death", dest="bot_break_on_death", action="store_true",
        help="no ToT, encerra a tentativa quando o jogador humano morre",
    )
    break_on_death.add_argument(
        "--no-bot-break-on-death", dest="bot_break_on_death", action="store_false",
        help="no ToT, mantém a tentativa quando o jogador humano morre",
    )
    parser.set_defaults(bot_break_on_death=None)
    parser.add_argument(
        "--bot-names", choices=("default", "x86qw", "personal"), default="default",
        help="seleciona nomes padrão KTX, x86QW aleatórios ou a lista pessoal",
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
    bot_names_profile = namespace.bot_names
    bot_modifiers = any((
        namespace.bot_skill is not None,
        bot_team is not None,
        namespace.bot_weapon is not None,
        namespace.bot_health is not None,
        namespace.bot_break_on_death is not None,
        bot_names_profile != "default",
    ))
    if bot_modifiers and not (namespace.bots or namespace.fill_bots):
        parser.error("opções --bot-* exigem --bots ou --fill-bots")
    if bot_team is not None and re.fullmatch(
        r"[A-Za-z0-9_-]{1,9}", bot_team,
    ) is None:
        parser.error("--bot-team aceita 1 a 9 letras, números, _ ou -")
    ktx_specific = any((
        namespace.bots,
        namespace.fill_bots,
        namespace.bot_skill is not None,
        bot_team is not None,
        namespace.bot_weapon is not None,
        namespace.bot_health is not None,
        namespace.bot_break_on_death,
        bot_names_profile != "default",
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
        bot_skill=namespace.bot_skill if namespace.bot_skill is not None else 5,
        bot_team=bot_team,
        bot_weapon=namespace.bot_weapon,
        bot_health=namespace.bot_health,
        bot_break_on_death=namespace.bot_break_on_death,
        bot_names_profile=bot_names_profile,
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


def main(
    arguments: list[str] | None = None, *, propagate_menu_exit: bool = False,
) -> int:
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
    except navigation.MenuExit:
        if propagate_menu_exit:
            raise
        console.info("Menu encerrado; o jogo não foi iniciado.")
        return 0
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
