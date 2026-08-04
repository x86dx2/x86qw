"""Immutable gameplay values shared by launcher adapters."""

from __future__ import annotations

from dataclasses import dataclass


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
class KtxMapRequirement:
    key: str
    asset: str
    when: str
    label: str


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
