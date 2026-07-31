"""Load and validate declarative x86QW runtime, game and compatibility contracts."""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath


IDENTIFIER = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
VARIANT = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
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
            if platform.get("support") != "supported" or not isinstance(platform.get("test_required"), bool):
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
        for value in runtime["personal_configuration"]:
            if str(value).startswith("_x86qw/runtimes/"):
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
                str(entry.get("destination")): str(entry.get("path"))
                for entry in sources if isinstance(entry, dict)
            }
            for path in runtime["runtime_path"].values():
                source = declared.get(str(path))
                if source is None or not (project_root / source).is_file():
                    raise ValueError(f"runtime architecture has no artifact: {identifier}/{path}")
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
        "capabilities": load_capabilities(directory / "capabilities.json"),
        "runtimes": load_runtimes(directory / "runtimes.json"),
        "games": load_games(directory / "games.json"),
        "compatibility": load_compatibility(directory / "compatibility.json"),
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
