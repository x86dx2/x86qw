"""Load and validate declarative x86QW runtime, game and compatibility contracts."""

from __future__ import annotations

import json
import re
import struct
import sys
import tarfile
from pathlib import Path, PurePosixPath

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from x86qw_runtime.contracts.schema import ContractError, SchemaKind, validate_document_versions


IDENTIFIER = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
VARIANT = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
PLATFORM_SUPPORT_STATES = frozenset({"supported", "conditional"})
REQUIRED_RUNTIME_FIELDS = frozenset({
    "id", "label", "kind", "protocols", "capabilities", "component", "channels",
    "platforms", "architectures", "executable", "runtime_path", "configuration",
    "personal_configuration", "logs", "demos", "arguments", "environment",
    "dependencies", "readiness", "smoke_test",
})
REQUIRED_PLATFORM_FIELDS = frozenset({
    "system", "architecture", "variant", "executable", "runtime_path", "format",
    "permissions", "support", "origin", "test_required",
})
REQUIRED_GAME_FIELDS = frozenset({
    "id", "label", "protocol", "component", "gamedir", "profile", "marker",
    "gamecode", "gamecode_type", "default_map", "suggested_maps", "client_runtimes",
    "server_runtimes", "pre_map_arguments", "post_map_arguments", "managed_config",
    "personal_config", "required_capabilities", "smoke_test",
})


def _safe_path(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError(f"invalid {label}: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe {label}: {value}")
    return value


def _load(path: Path, collection: str) -> dict[str, object]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read {collection} catalog: {path}") from error
    if (
        not isinstance(document, dict)
        or document.get("format") != 1
        or document.get("project") != "x86qw"
        or not isinstance(document.get(collection), list)
    ):
        raise ValueError(f"invalid {collection} catalog identity")
    return document


def load_capabilities(path: Path) -> dict[str, object]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read capabilities catalog: {path}") from error
    if not isinstance(document, dict) or document.get("format") != 1 or document.get("project") != "x86qw":
        raise ValueError("invalid capabilities catalog identity")
    return document


def load_runtimes(path: Path) -> dict[str, object]:
    return _load(path, "runtimes")


def load_games(path: Path) -> dict[str, object]:
    return _load(path, "games")


def load_compatibility(path: Path) -> dict[str, object]:
    return _load(path, "compatibility")


def _id_entries(document: dict[str, object], key: str, label: str) -> dict[str, dict[str, object]]:
    entries = document.get(key)
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{label} catalog is empty")
    result: dict[str, dict[str, object]] = {}
    for entry in entries:
        identifier = entry.get("id") if isinstance(entry, dict) else None
        if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier) or identifier in result:
            raise ValueError(f"invalid or duplicate {label} id: {identifier}")
        result[identifier] = entry
    return result


def _string_list(value: object, label: str, *, allow_empty: bool = True) -> list[str]:
    if (
        not isinstance(value, list)
        or (not allow_empty and not value)
        or not all(isinstance(item, str) and item for item in value)
        or len(value) != len(set(value))
    ):
        raise ValueError(f"invalid {label}")
    return value


def _validate_cycles(dependencies: dict[str, list[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(identifier: str) -> None:
        if identifier in visiting:
            raise ValueError(f"runtime dependency cycle at {identifier}")
        if identifier in visited:
            return
        visiting.add(identifier)
        for dependency in dependencies[identifier]:
            visit(dependency)
        visiting.remove(identifier)
        visited.add(identifier)

    for identifier in dependencies:
        visit(identifier)


def _pak_map_names(path: Path) -> set[str]:
    payload = path.read_bytes()
    if len(payload) < 12 or payload[:4] != b"PACK":
        raise ValueError(f"invalid Quake PAK: {path}")
    offset, size = struct.unpack_from("<II", payload, 4)
    if size % 64 or offset + size > len(payload):
        raise ValueError(f"invalid Quake PAK directory: {path}")
    maps: set[str] = set()
    for cursor in range(offset, offset + size, 64):
        raw_name = payload[cursor:cursor + 56].split(b"\0", 1)[0]
        try:
            name = raw_name.decode("ascii")
        except UnicodeDecodeError as error:
            raise ValueError(f"invalid Quake PAK member: {path}") from error
        match = re.fullmatch(r"maps/([A-Za-z0-9][A-Za-z0-9_.-]{0,63})\.bsp", name)
        if match:
            maps.add(match.group(1).casefold())
    return maps


def validate_ktx_mode_catalog(
    project_root: Path,
    component_catalog: dict[str, object],
    *,
    mode_catalog: dict[str, object] | None = None,
) -> None:
    """Cross-check KTX modes against the exact routes, maps and CTF assets."""
    catalog_path = project_root / "dist/mods/ktx/1.47/x86qw/catalog/modes.json"
    if mode_catalog is None:
        try:
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(f"cannot read KTX mode catalog: {catalog_path}") from error
    else:
        catalog = mode_catalog
    if not isinstance(catalog, dict):
        raise ValueError("invalid KTX mode catalog identity")
    policies = catalog.get("map_asset_policies") if isinstance(catalog, dict) else None
    defaults = catalog.get("defaults") if isinstance(catalog, dict) else None
    modes = catalog.get("modes") if isinstance(catalog, dict) else None
    if (
        catalog.get("format") != 1
        or catalog.get("game") != "ktx"
        or not isinstance(policies, dict)
        or not isinstance(defaults, dict)
        or not isinstance(modes, list)
        or not modes
    ):
        raise ValueError("invalid KTX mode catalog identity")
    for key, policy in policies.items():
        asset = policy.get("asset") if isinstance(policy, dict) else None
        if (
            not isinstance(key, str)
            or not IDENTIFIER.fullmatch(key)
            or not isinstance(asset, str)
            or "{map}" not in asset
        ):
            raise ValueError(f"invalid KTX map policy: {key}")
        _safe_path(asset.replace("{map}", "map"), "KTX map policy")
    expected_policies = {
        "frogbot-route": ("bots/maps/{map}.bot", "frogbots"),
        "ctf-entities": ("id1/maps/ctf/{map}.ent", "always"),
        "race-route": ("race/routes/{map}.route", "always"),
    }
    if set(policies) != set(expected_policies) or any(
        (policies[key].get("asset"), policies[key].get("when")) != value
        for key, value in expected_policies.items()
    ):
        raise ValueError("KTX map policies disagree with the runtime contract")

    source = project_root / "dist/mods/ktx/1.47/source/ktx-1.47.tar.gz"
    try:
        with tarfile.open(source, "r:gz") as archive:
            names = archive.getnames()
            bot_routes = {
                PurePosixPath(name).stem.casefold()
                for name in names
                if "/resources/example-configs/ktx/bots/maps/" in name
                and name.endswith(".bot")
            }
            race_routes = {
                PurePosixPath(name).stem.casefold()
                for name in names
                if "/resources/example-configs/ktx/race/routes/" in name
                and name.endswith(".route")
            }
            tot_configs = {
                PurePosixPath(name).stem.casefold()
                for name in names
                if "/resources/example-configs/ktx/configs/usermodes/tot/" in name
                and name.endswith(".cfg")
            }
            usermodes = {
                match.group(1).casefold()
                for name in names
                if (match := re.search(
                    r"/resources/example-configs/ktx/configs/usermodes/"
                    r"([^/]+)/default\.cfg$",
                    name,
                ))
            }
            command_source_name = next(
                (name for name in names if name.endswith("/src/commands.c")),
                None,
            )
            if command_source_name is None:
                raise ValueError("KTX command registry is missing")
            command_source_file = archive.extractfile(command_source_name)
            if command_source_file is None:
                raise ValueError("cannot read KTX command registry")
            command_source = command_source_file.read().decode("latin-1")
            server_commands = {
                match.group(1).casefold()
                for match in re.finditer(r'^\s*\{\s*"([A-Za-z0-9_]+)"\s*,', command_source, re.M)
            }
            bot_commands = {
                "CreateMarker", "SetGoal", "SetMarkerFlag", "SetMarkerPath",
                "SetMarkerPathAngleHint", "SetMarkerPathFlags",
                "SetMarkerViewOfs", "SetRocketJumpPathFields", "SetZone",
            }
            race_commands = {
                "race_add_route_node", "race_route_add_end",
                "race_route_add_start", "race_set_node_size",
                "race_set_route_falsestart_mode", "race_set_route_name",
                "race_set_route_timeout", "race_set_route_weapon_mode",
                "race_set_teleport_flags_by_name",
            }
            for name in names:
                if not (
                    ("/resources/example-configs/ktx/bots/maps/" in name and name.endswith(".bot"))
                    or ("/resources/example-configs/ktx/race/routes/" in name and name.endswith(".route"))
                ):
                    continue
                member = archive.getmember(name)
                if not member.isfile() or not 0 < member.size <= 1024 * 1024:
                    raise ValueError(f"unsafe KTX route member: {name}")
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise ValueError(f"cannot read KTX route member: {name}")
                payload = extracted.read()
                if b"\0" in payload or any(
                    byte < 32 and byte not in {9, 10, 13} for byte in payload
                ):
                    raise ValueError(f"KTX route contains control bytes: {name}")
                commands = [
                    line.lstrip().split(None, 1)[0]
                    for line in payload.decode("latin-1").splitlines()
                    if line.strip() and not line.lstrip().startswith("//")
                ]
                if any(
                    ";" in line
                    for line in payload.decode("latin-1").splitlines()
                    if line.strip() and not line.lstrip().startswith("//")
                ):
                    raise ValueError(f"KTX route contains a command separator: {name}")
                allowed = bot_commands if name.endswith(".bot") else race_commands
                if not commands or any(command not in allowed for command in commands):
                    raise ValueError(f"KTX route contains an unknown directive: {name}")
                if name.endswith(".bot") and "CreateMarker" not in commands:
                    raise ValueError(f"KTX Frogbot route has no markers: {name}")
                if name.endswith(".route") and (
                    commands.count("race_route_add_start")
                    != commands.count("race_route_add_end")
                    or "race_add_route_node" not in commands
                ):
                    raise ValueError(f"KTX Race route is structurally incomplete: {name}")
    except (OSError, tarfile.TarError) as error:
        raise ValueError("cannot inspect KTX 1.47 source assets") from error
    if len(bot_routes) != 77 or len(race_routes) != 54:
        raise ValueError("KTX route inventory changed without catalog review")

    essential_maps: set[str] = set()
    for filename in ("pak0.pak", "pak1.pak"):
        essential_maps.update(_pak_map_names(project_root / "dist/game-data/id1" / filename))
    manifest = json.loads((project_root / "dist/manifest.json").read_text(encoding="utf-8"))
    manifest_files = manifest.get("files")
    if not isinstance(manifest_files, dict):
        raise ValueError("invalid distribution manifest for KTX map validation")
    managed_maps = essential_maps | {
        PurePosixPath(path).stem.casefold()
        for path in manifest_files
        if re.fullmatch(r"distributions/nquake/[^/]+/non-gpl/qw/maps/[^/]+\.bsp", path)
    }

    components = component_catalog.get("components")
    ktx_component = next(
        (entry for entry in components if isinstance(entry, dict) and entry.get("id") == "ktx"),
        None,
    ) if isinstance(components, list) else None
    if not isinstance(ktx_component, dict):
        raise ValueError("KTX component missing during mode validation")
    ctf_entities = {
        PurePosixPath(str(entry.get("destination"))).stem.casefold()
        for entry in ktx_component.get("sources", [])
        if isinstance(entry, dict)
        and re.fullmatch(r"id1/maps/ctf/[^/]+\.ent", str(entry.get("destination")))
    }
    entry_configs = {
        PurePosixPath(str(entry.get("destination"))).name.casefold()
        for entry in ktx_component.get("project_sources", [])
        if isinstance(entry, dict)
        and re.fullmatch(r"qw/x86qw-ktx-mode-[a-z0-9-]+\.cfg", str(entry.get("destination")))
    }

    default_bot_policies = defaults.get("frogbot_map_policies")
    if default_bot_policies != ["frogbot-route"]:
        raise ValueError("KTX Frogbot map policy default is inconsistent")
    mode_ids: set[str] = set()
    for mode in modes:
        identifier = mode.get("id") if isinstance(mode, dict) else None
        suggestions = mode.get("suggested_maps") if isinstance(mode, dict) else None
        if (
            not isinstance(identifier, str)
            or identifier in mode_ids
            or not isinstance(suggestions, list)
            or not suggestions
            or mode.get("default_map") not in suggestions
            or not all(isinstance(value, str) for value in suggestions)
            or len(folded := {value.casefold() for value in suggestions}) != len(suggestions)
        ):
            raise ValueError(f"invalid KTX mode map contract: {identifier}")
        mode_ids.add(identifier)
        usermode = mode.get("usermode")
        if not isinstance(usermode, str) or (
            usermode.casefold() not in usermodes
            and usermode.casefold() not in server_commands
        ):
            raise ValueError(f"KTX mode references missing usermode: {identifier}")
        entry_config = mode.get("entry_config")
        if entry_config is not None and (
            not isinstance(entry_config, str)
            or entry_config.casefold() not in entry_configs
        ):
            raise ValueError(f"KTX mode references missing entry config: {identifier}")
        command_values: list[object] = []
        for field, command_index in (("help_commands", 0), ("key_bindings", 1)):
            entries = mode.get(field)
            if not isinstance(entries, list):
                raise ValueError(f"KTX mode has invalid {field}: {identifier}")
            command_values.extend(
                entry[command_index]
                for entry in entries
                if isinstance(entry, list) and len(entry) > command_index
            )
        direct_client_commands = {"break", "hmstats", "join", "observe", "toggleready"}
        for command in command_values:
            if not isinstance(command, str) or not command:
                raise ValueError(f"KTX mode has an invalid help command: {identifier}")
            words = command.split()
            if words[0].casefold() == "cmd":
                if len(words) < 2 or words[1].casefold() not in server_commands:
                    raise ValueError(f"KTX mode references missing server command: {identifier}")
            elif command.casefold() not in direct_client_commands:
                raise ValueError(f"KTX mode references unsupported client command: {identifier}")
        if str(mode["default_map"]).casefold() not in essential_maps:
            raise ValueError(f"KTX mode default is unavailable in Essential: {identifier}")
        if mode.get("bots", True):
            if str(mode["default_map"]).casefold() not in bot_routes:
                raise ValueError(f"KTX mode default lacks Frogbot route: {identifier}")
            if not folded & bot_routes & managed_maps:
                raise ValueError(f"KTX mode has no managed Frogbot map: {identifier}")
        mode_policies = mode.get("map_policies", [])
        if not isinstance(mode_policies, list) or any(value not in policies for value in mode_policies):
            raise ValueError(f"KTX mode references invalid map policy: {identifier}")
        expected_mode_policies = {
            "ctf": ["ctf-entities"],
            "race": ["race-route"],
        }.get(identifier, [])
        if mode_policies != expected_mode_policies:
            raise ValueError(f"KTX mode map policy is inconsistent: {identifier}")
        if identifier == "ctf" and folded != ctf_entities:
            raise ValueError("KTX CTF suggestions disagree with managed ENT files")
        if identifier == "race" and folded != race_routes:
            raise ValueError("KTX Race suggestions disagree with shipped routes")
        if identifier == "tot" and folded != tot_configs:
            raise ValueError("KTX ToT suggestions disagree with map-specific configs")


def validate_inventory(
    capabilities: dict[str, object],
    runtimes: dict[str, object],
    games: dict[str, object],
    compatibility: dict[str, object],
    *,
    component_catalog: dict[str, object] | None = None,
    project_root: Path | None = None,
    public_catalog: dict[str, object] | None = None,
) -> None:
    for document, kind in (
        (capabilities, SchemaKind.CATALOG),
        (runtimes, SchemaKind.CATALOG),
        (games, SchemaKind.CATALOG),
        (compatibility, SchemaKind.CATALOG),
    ):
        try:
            # Repository inventories are the canonical source for the next
            # release line.  They must carry explicit schema and CLI bounds;
            # installed 0.x zipapps still use the legacy-tolerant runtime
            # loaders above.
            validate_document_versions(document, kind=kind, allow_legacy=False)
        except ContractError as error:
            raise ValueError(f"invalid {kind} contract in runtime inventory") from error
    if component_catalog is not None and project_root is not None:
        validate_ktx_mode_catalog(project_root, component_catalog)
    for document, key in ((runtimes, "runtimes"), (games, "games"), (compatibility, "compatibility")):
        if document.get("format") != 1 or document.get("project") != "x86qw" or not isinstance(document.get(key), list):
            raise ValueError(f"invalid {key} catalog identity")
    if capabilities.get("format") != 1 or capabilities.get("project") != "x86qw":
        raise ValueError("invalid capabilities catalog identity")

    runtime_kinds = set(_string_list(capabilities.get("runtime_kinds"), "runtime kinds", allow_empty=False))
    compatibility_kinds = set(_string_list(capabilities.get("compatibility_kinds"), "compatibility kinds", allow_empty=False))
    systems = set(_string_list(capabilities.get("systems"), "systems", allow_empty=False))
    architectures = set(_string_list(capabilities.get("architectures"), "architectures", allow_empty=False))
    executable_formats = set(_string_list(capabilities.get("executable_formats"), "executable formats", allow_empty=False))
    _string_list(capabilities.get("commands"), "CLI commands", allow_empty=False)
    host_systems = capabilities.get("host_systems")
    architecture_aliases = capabilities.get("architecture_aliases")
    platform_labels = capabilities.get("platform_labels")
    if (
        not isinstance(host_systems, dict)
        or set(host_systems.values()) != systems
        or not isinstance(architecture_aliases, dict)
        or set(architecture_aliases) != architectures
        or not all(isinstance(value, list) and value for value in architecture_aliases.values())
        or not isinstance(platform_labels, dict)
    ):
        raise ValueError("platform host mappings are invalid")
    protocol_entries = _id_entries(capabilities, "protocols", "protocol")
    capability_entries = _id_entries(capabilities, "capabilities", "capability")
    protocol_ids = set(protocol_entries)
    capability_ids = set(capability_entries)

    component_ids: set[str] = set()
    component_entries: dict[str, dict[str, object]] = {}
    if component_catalog is not None:
        client = component_catalog.get("client")
        if isinstance(client, dict) and isinstance(client.get("id"), str):
            component_ids.add(client["id"])
        raw_components = component_catalog.get("components")
        if not isinstance(raw_components, list):
            raise ValueError("invalid component catalog for runtime validation")
        for component in raw_components:
            if isinstance(component, dict) and isinstance(component.get("id"), str):
                component_ids.add(component["id"])
                component_entries[component["id"]] = component

    runtime_entries = _id_entries(runtimes, "runtimes", "runtime")
    dependencies: dict[str, list[str]] = {}
    for identifier, runtime in runtime_entries.items():
        if not REQUIRED_RUNTIME_FIELDS <= runtime.keys():
            raise ValueError(f"runtime lacks required fields: {identifier}")
        if runtime.get("kind") not in runtime_kinds:
            raise ValueError(f"invalid runtime kind: {identifier}")
        if not isinstance(runtime.get("label"), str) or not runtime["label"]:
            raise ValueError(f"runtime lacks label: {identifier}")
        protocols = _string_list(runtime.get("protocols"), f"runtime protocols: {identifier}", allow_empty=False)
        runtime_capabilities = _string_list(runtime.get("capabilities"), f"runtime capabilities: {identifier}", allow_empty=False)
        if set(protocols) - protocol_ids or set(runtime_capabilities) - capability_ids:
            raise ValueError(f"runtime references unknown protocol or capability: {identifier}")
        component = runtime.get("component")
        if not isinstance(component, str) or not IDENTIFIER.fullmatch(component):
            raise ValueError(f"runtime has invalid component: {identifier}")
        if component_ids and component not in component_ids:
            raise ValueError(f"runtime references missing component: {identifier}")
        _string_list(runtime.get("channels"), f"runtime channels: {identifier}", allow_empty=False)
        platforms = runtime.get("platforms")
        if not isinstance(platforms, list) or not platforms:
            raise ValueError(f"runtime has no platforms: {identifier}")
        variants: set[str] = set()
        declared_architectures: set[str] = set()
        for platform in platforms:
            if not isinstance(platform, dict) or not REQUIRED_PLATFORM_FIELDS <= platform.keys():
                raise ValueError(f"invalid runtime platform: {identifier}")
            variant = platform.get("variant")
            if not isinstance(variant, str) or not VARIANT.fullmatch(variant) or variant in variants:
                raise ValueError(f"invalid or duplicate runtime variant: {identifier}")
            variants.add(variant)
            system = platform.get("system")
            architecture = platform.get("architecture")
            if system not in systems or architecture not in architectures or platform.get("format") not in executable_formats:
                raise ValueError(f"runtime platform uses an unknown value: {identifier}/{variant}")
            declared_architectures.add(str(architecture))
            if (
                platform.get("support") not in PLATFORM_SUPPORT_STATES
                or not isinstance(platform.get("test_required"), bool)
            ):
                raise ValueError(f"runtime platform support is not explicit: {identifier}/{variant}")
            for field in ("executable", "runtime_path"):
                _safe_path(platform.get(field), f"runtime {field}")
            if variant not in platform_labels:
                raise ValueError(f"runtime platform lacks a public label: {identifier}/{variant}")
        executable = runtime.get("executable")
        runtime_path = runtime.get("runtime_path")
        if not isinstance(executable, dict) or not isinstance(runtime_path, dict) or set(executable) != variants or set(runtime_path) != variants:
            raise ValueError(f"runtime variant maps are incomplete: {identifier}")
        for variant in variants:
            _safe_path(executable[variant], "runtime executable")
            _safe_path(runtime_path[variant], "runtime path")
        if set(_string_list(runtime.get("architectures"), f"runtime architectures: {identifier}")) != declared_architectures:
            raise ValueError(f"runtime architectures disagree with platforms: {identifier}")
        for field in ("configuration", "personal_configuration", "logs", "demos"):
            for value in _string_list(runtime.get(field), f"runtime {field}: {identifier}"):
                _safe_path(value, f"runtime {field}")
        runtime_paths = {str(value) for value in runtime["runtime_path"].values()}
        for value in runtime["personal_configuration"]:
            if str(value) in runtime_paths:
                raise ValueError(f"personal configuration is inside immutable runtime payload: {identifier}")
        arguments = runtime.get("arguments")
        if not isinstance(arguments, dict) or arguments.get("shell") is not False or not isinstance(arguments.get("base"), list):
            raise ValueError(f"runtime arguments must be typed and shell-free: {identifier}")
        if not isinstance(runtime.get("environment"), dict) or not isinstance(runtime.get("readiness"), dict):
            raise ValueError(f"runtime environment or readiness is invalid: {identifier}")
        if not isinstance(runtime.get("smoke_test"), str) or not runtime["smoke_test"]:
            raise ValueError(f"runtime lacks smoke test: {identifier}")
        runtime_dependencies = _string_list(runtime.get("dependencies"), f"runtime dependencies: {identifier}")
        dependencies[identifier] = runtime_dependencies
    for identifier, required in dependencies.items():
        if set(required) - set(runtime_entries) or identifier in required:
            raise ValueError(f"runtime dependency is inconsistent: {identifier}")
    _validate_cycles(dependencies)

    game_entries = _id_entries(games, "games", "game")
    for identifier, game in game_entries.items():
        if not REQUIRED_GAME_FIELDS <= game.keys():
            raise ValueError(f"game lacks required fields: {identifier}")
        if game.get("protocol") not in protocol_ids:
            raise ValueError(f"game has invalid protocol: {identifier}")
        component = game.get("component")
        if not isinstance(component, str) or (component_ids and component not in component_ids):
            raise ValueError(f"game references missing component: {identifier}")
        for field in ("gamedir", "profile"):
            if not isinstance(game.get(field), str) or not IDENTIFIER.fullmatch(game[field]):
                raise ValueError(f"game has invalid {field}: {identifier}")
        for field in ("marker", "gamecode", "managed_config", "personal_config"):
            _safe_path(game.get(field), f"game {field}")
        bot_names_personal_config = game.get("bot_names_personal_config")
        if bot_names_personal_config is not None:
            _safe_path(bot_names_personal_config, "game bot names personal config")
        if game["managed_config"] == game["personal_config"]:
            raise ValueError(f"personal configuration overlaps managed payload: {identifier}")
        clients = _string_list(game.get("client_runtimes"), f"game client runtimes: {identifier}", allow_empty=False)
        servers = _string_list(game.get("server_runtimes"), f"game server runtimes: {identifier}", allow_empty=False)
        if any(runtime_entries.get(item, {}).get("kind") != "client" for item in clients):
            raise ValueError(f"game references invalid client runtime: {identifier}")
        if any(runtime_entries.get(item, {}).get("kind") != "server" for item in servers):
            raise ValueError(f"game references invalid server runtime: {identifier}")
        required = _string_list(game.get("required_capabilities"), f"game capabilities: {identifier}", allow_empty=False)
        if set(required) - capability_ids:
            raise ValueError(f"game references unknown capability: {identifier}")
        if not isinstance(game.get("suggested_maps"), list) or game.get("default_map") not in game["suggested_maps"]:
            raise ValueError(f"game map defaults are inconsistent: {identifier}")
        for field in ("pre_map_arguments", "post_map_arguments"):
            if not isinstance(game.get(field), list) or not all(isinstance(item, str) for item in game[field]):
                raise ValueError(f"game arguments are invalid: {identifier}")
        for field in (
            "client_game_arguments", "pre_connect_arguments",
            "client_compatibility_arguments", "dedicated_arguments",
        ):
            if not isinstance(game.get(field), list) or not all(isinstance(item, str) for item in game[field]):
                raise ValueError(f"game typed arguments are invalid: {identifier}/{field}")
        play_support = game.get("play_support_gamecode")
        if play_support is not None:
            _safe_path(play_support, "play support gamecode")
        for field in ("local_server_settings", "dedicated_settings"):
            values = game.get(field)
            if (
                not isinstance(values, list)
                or not all(
                    isinstance(item, list)
                    and len(item) == 2
                    and all(isinstance(value, str) and value for value in item)
                    for item in values
                )
            ):
                raise ValueError(f"game typed settings are invalid: {identifier}/{field}")
        if component_catalog is not None:
            component_entry = component_entries.get(str(component), {})
            sources = component_entry.get("project_sources", [])
            if not isinstance(sources, list):
                raise ValueError(f"game component sources are invalid: {identifier}")
            destinations = {
                entry.get("destination"): entry.get("mode")
                for entry in sources if isinstance(entry, dict)
            }
            if destinations.get(game["personal_config"]) != "default":
                raise ValueError(f"personal game config is not a preserved default: {identifier}")
            if (
                bot_names_personal_config is not None
                and destinations.get(bot_names_personal_config) != "default"
            ):
                raise ValueError(
                    f"personal bot names config is not a preserved default: {identifier}"
                )

    raw_compatibility = compatibility.get("compatibility")
    if not isinstance(raw_compatibility, list) or not raw_compatibility:
        raise ValueError("compatibility catalog is empty")
    seen_pairs: set[tuple[str, str]] = set()
    client_matrix: dict[str, set[str]] = {identifier: set() for identifier in game_entries}
    server_matrix: dict[str, set[str]] = {identifier: set() for identifier in game_entries}
    for entry in raw_compatibility:
        if not isinstance(entry, dict):
            raise ValueError("invalid compatibility entry")
        kind = entry.get("kind")
        runtime_id = entry.get("runtime")
        if kind not in compatibility_kinds or runtime_id not in runtime_entries:
            raise ValueError("compatibility references invalid kind or runtime")
        if runtime_entries[str(runtime_id)]["kind"] != kind:
            raise ValueError(f"compatibility kind disagrees with runtime: {runtime_id}")
        pair = (str(kind), str(runtime_id))
        if pair in seen_pairs:
            raise ValueError(f"duplicate compatibility entry: {runtime_id}")
        seen_pairs.add(pair)
        listed_games = _string_list(entry.get("games"), f"compatibility games: {runtime_id}")
        if set(listed_games) - set(game_entries) or entry.get("protocol") not in runtime_entries[str(runtime_id)]["protocols"]:
            raise ValueError(f"compatibility references invalid game or protocol: {runtime_id}")
        for game_id in listed_games:
            if kind == "client":
                client_matrix[game_id].add(str(runtime_id))
            elif kind == "server":
                server_matrix[game_id].add(str(runtime_id))
    for identifier, game in game_entries.items():
        if client_matrix[identifier] != set(game["client_runtimes"]):
            raise ValueError(f"client compatibility disagrees with game: {identifier}")
        if server_matrix[identifier] != set(game["server_runtimes"]):
            raise ValueError(f"server compatibility disagrees with game: {identifier}")

    if component_catalog is not None and project_root is not None:
        for identifier, runtime in runtime_entries.items():
            if runtime["component"] == "ezquake":
                continue
            component = component_entries[str(runtime["component"])]
            sources = component.get("project_sources", [])
            declared = {
                str(entry.get("platform")): entry
                for entry in sources
                if isinstance(entry, dict) and entry.get("platform") is not None
            }
            for platform in runtime["platforms"]:
                variant = str(platform["variant"])
                entry = declared.get(variant)
                path = str(platform["runtime_path"])
                if (
                    entry is None
                    or entry.get("install_destination") != path
                    or not (project_root / str(entry.get("path"))).is_file()
                ):
                    raise ValueError(f"runtime architecture has no artifact: {identifier}/{variant}")
    if public_catalog is not None:
        packages = public_catalog.get("packages")
        if not isinstance(packages, list):
            raise ValueError("invalid public package catalog")
        for identifier in ("ezquake-stable", "ezquake-nightly"):
            runtime = runtime_entries[identifier]
            channel = runtime["channels"][0]
            for platform in runtime["platforms"]:
                if not any(
                    isinstance(package, dict)
                    and package.get("component") == "ezquake"
                    and package.get("channel") == channel
                    and package.get("platform") == platform["system"]
                    and package.get("architecture") == platform["architecture"]
                    for package in packages
                ):
                    raise ValueError(f"client architecture has no public artifact: {identifier}/{platform['variant']}")


def load_inventory(
    directory: Path,
    *,
    component_catalog: dict[str, object] | None = None,
    project_root: Path | None = None,
    public_catalog: dict[str, object] | None = None,
) -> dict[str, dict[str, object]]:
    documents = {
        "capabilities": load_capabilities(directory / "capabilities.json", allow_legacy=False),
        "runtimes": load_runtimes(directory / "runtimes.json", allow_legacy=False),
        "games": load_games(directory / "games.json", allow_legacy=False),
        "compatibility": load_compatibility(directory / "compatibility.json", allow_legacy=False),
    }
    validate_inventory(
        documents["capabilities"], documents["runtimes"], documents["games"],
        documents["compatibility"], component_catalog=component_catalog,
        project_root=project_root, public_catalog=public_catalog,
    )
    return documents


def runtimes_by_id(document: dict[str, object]) -> dict[str, dict[str, object]]:
    return _id_entries(document, "runtimes", "runtime")


def games_by_id(document: dict[str, object]) -> dict[str, dict[str, object]]:
    return _id_entries(document, "games", "game")


# Runtime-facing JSON loading and identity indexes are canonical in the public
# runtime package. Repository-only cross-validation remains in this module.
from x86qw_runtime.catalogs import (  # noqa: E402
    games_by_id as games_by_id,
    load_capabilities as load_capabilities,
    load_compatibility as load_compatibility,
    load_games as load_games,
    load_runtimes as load_runtimes,
    runtimes_by_id as runtimes_by_id,
)
