"""Pure parsing and validation for gameplay catalog documents."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from x86qw_runtime.catalogs import games_by_id
from x86qw_runtime.errors import InstallerError

from .models import (
    KtxMapRequirement,
    KtxMenuGroupSpec,
    KtxModeSpec,
    LocalGameSpec,
)


KTX_CONTEXT_KEYS = ("F5", "F6", "H", "I", "M", "X", "Z", "F11")


def parse_local_games(document: dict[str, object]) -> tuple[LocalGameSpec, ...]:
    """Project a validated games document into immutable launcher values."""
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


def parse_ktx_modes(catalog: dict[str, object]) -> tuple[KtxModeSpec, ...]:
    """Validate a KTX mode catalog and preserve its declared ordering."""
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
                and re.fullmatch(
                    r"[A-Za-z0-9 ,.áàâãéêíóôõúçÁÀÂÃÉÊÍÓÔÕÚÇ-]{1,72}", entry[1],
                ) is not None
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
            or not {"F5", "F6", "F11"}.issubset({entry[0] for entry in key_bindings})
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
            or not all(isinstance(value, str) and value in policies for value in raw_mode_policies)
            or len(raw_mode_policies) != len(set(raw_mode_policies))
            or any(policies[value].when == "frogbots" for value in raw_mode_policies)
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
            raise InstallerError(f"Formação de equipes incompatível com o modo KTX {key}.")
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
                (str(entry[0]), str(entry[1]), str(entry[2])) for entry in key_bindings
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


def parse_ktx_menu_groups(
    catalog: dict[str, object],
    modes: tuple[KtxModeSpec, ...],
) -> tuple[KtxMenuGroupSpec, ...]:
    """Validate KTX navigation groups against an already parsed mode set."""
    raw_groups = catalog.get("menu_groups")
    if not isinstance(raw_groups, list) or not raw_groups:
        raise InstallerError("Catálogo de modos KTX não declara grupos de navegação.")
    mode_ids = {mode.key for mode in modes}
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
