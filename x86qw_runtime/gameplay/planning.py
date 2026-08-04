"""Deterministic KTX command planning without filesystem or process access."""

from __future__ import annotations

import re
from dataclasses import replace

from x86qw_runtime.errors import InstallerError

from .catalogs import KTX_CONTEXT_KEYS
from .models import FrogbotIdentity, KtxLaunchOptions, KtxMapRequirement, KtxModeSpec


FROGBOT_ADD_WAIT_FRAMES = 8
KTX_BOT_NAME_PREFIXES = ("k_fb_name", "k_fb_name_team", "k_fb_name_enemy")
KTX_BOT_NAME_SLOTS = 32


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


def requested_frogbot_names(
    options: KtxLaunchOptions,
    mode: KtxModeSpec | None = None,
) -> int:
    if not options.fill_bots:
        return options.bots
    limit = ktx_mode_bot_limit(mode) if mode is not None else None
    return limit if limit is not None else 8


def quake_colored_frogbot_name(name: str) -> str:
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
    settings: list[tuple[str, str]] = []
    for prefix in KTX_BOT_NAME_PREFIXES:
        for index in range(count):
            identity = frogbot_identity(pool[index])
            settings.append((
                f"{prefix}_{index}", quake_colored_frogbot_name(identity.name),
            ))
    return tuple(settings)


def quake_colored_frogbot_bytes(name: str) -> bytes:
    return b"/\xa0" + bytes(ord(character) | 0x80 for character in name)


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
    settings: list[tuple[str, bytes]] = []
    for prefix in KTX_BOT_NAME_PREFIXES:
        for index in range(count):
            identity = frogbot_identity(pool[index])
            settings.append((
                f"{prefix}_{index}", quake_colored_frogbot_bytes(identity.name),
            ))
    return tuple(settings)


def ktx_bot_name_cleanup_commands() -> tuple[str, ...]:
    names = tuple(
        f"{prefix}_{index}"
        for prefix in KTX_BOT_NAME_PREFIXES
        for index in range(KTX_BOT_NAME_SLOTS)
    )
    return tuple(
        "unset " + " ".join(names[offset:offset + 12])
        for offset in range(0, len(names), 12)
    )


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
            action = "x86qw_ktx_bot_add_command;" f"x86qw_ktx_bot_roster_{count + 1}"
        else:
            action = "echo Limite de Frogbots deste modo atingido"
        commands.append(
            f"tempalias x86qw_ktx_bot_add_{count} {quote_console_command(action)}"
        )
        if count > 0:
            action = "cmd botcmd removebot;" f"x86qw_ktx_bot_roster_{count - 1}"
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


def ktx_chunked_setup_alias_plan(
    commands: tuple[str, ...], *, maximum_body: int = 700,
) -> tuple[tuple[str, ...], str]:
    """Store a long setup in bounded aliases and expose its direct entry."""
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
        f"x86qw_ktx_launch_setup_{index}" for index in range(1, len(chunks) + 1)
    )
    definitions = []
    for alias, chunk in zip(aliases, chunks):
        body = ";".join(chunk).replace('"', "$qt")
        definitions.append(f"tempalias {alias} {quote_console_command(body)}")
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
    return tuple(definitions), ";".join(current_aliases)


def ktx_chunked_setup_alias_commands(
    commands: tuple[str, ...], *, maximum_body: int = 700,
) -> tuple[str, ...]:
    """Store a long post-map setup behind its compatibility entry alias."""
    definitions, invocation = ktx_chunked_setup_alias_plan(
        commands, maximum_body=maximum_body,
    )
    return (*definitions,
        "tempalias x86qw_ktx_launch_setup "
        f"{quote_console_command(invocation)}"
    )


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
                team = (
                    f" {selected_team}"
                    if options.bot_team is not None and selected_team is not None
                    else ""
                )
                commands.append(f"cmd botcmd addbot {options.bot_skill}{team}")
                if index + 1 < len(team_sequence):
                    commands.extend(("wait",) * FROGBOT_ADD_WAIT_FRAMES)
        if options.bot_name_pool:
            commands.extend(("wait",) * FROGBOT_ADD_WAIT_FRAMES)
            setting_names = [name for name, _value in ktx_bot_name_settings(options, mode)]
            for offset in range(0, len(setting_names), 12):
                commands.append("unset " + " ".join(setting_names[offset:offset + 12]))

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
