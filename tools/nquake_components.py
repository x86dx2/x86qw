"""Validation and selection helpers for the nQuake component catalog."""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath


COMPONENT_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ALLOWED_KINDS = {"core", "gameplay", "content", "addon", "documentation"}
ALLOWED_MODES = {"overlay", "default"}


def _safe_path(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError(f"invalid {label}: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError(f"unsafe {label}: {value}")
    return value


def load_catalog(path: Path) -> dict[str, object]:
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read nQuake component catalog: {path}") from error
    validate_catalog(catalog)
    return catalog


def validate_catalog(catalog: object) -> None:
    if not isinstance(catalog, dict) or catalog.get("format") != 1 or catalog.get("project") != "x86qw":
        raise ValueError("invalid nQuake component catalog identity")
    client = catalog.get("client")
    if not isinstance(client, dict) or client.get("id") != "ezquake" or client.get("channels") != ["stable", "nightly"]:
        raise ValueError("the active nQuake client must be ezQuake stable and nightly")
    source = catalog.get("source")
    if (
        not isinstance(source, dict)
        or not isinstance(source.get("repository"), str)
        or not isinstance(source.get("ref"), str)
    ):
        raise ValueError("invalid nQuake reference source")
    components = catalog.get("components")
    if not isinstance(components, list) or not components:
        raise ValueError("nQuake component catalog is empty")

    identifiers: set[str] = set()
    selectors: list[tuple[str, str, set[str]]] = []
    dependencies: dict[str, list[str]] = {}
    for component in components:
        if not isinstance(component, dict):
            raise ValueError("invalid nQuake component entry")
        identifier = component.get("id")
        if not isinstance(identifier, str) or not COMPONENT_ID.fullmatch(identifier) or identifier in identifiers:
            raise ValueError(f"invalid or duplicate component id: {identifier}")
        identifiers.add(identifier)
        if component.get("kind") not in ALLOWED_KINDS:
            raise ValueError(f"invalid component kind: {identifier}")
        if not all(isinstance(component.get(field), str) and component[field] for field in ("label", "description")):
            raise ValueError(f"component lacks user-facing metadata: {identifier}")
        requires = component.get("requires")
        if not isinstance(requires, list) or not all(isinstance(item, str) for item in requires):
            raise ValueError(f"invalid dependencies: {identifier}")
        dependencies[identifier] = requires
        sources = component.get("sources")
        if not isinstance(sources, list) or not sources:
            raise ValueError(f"component has no sources: {identifier}")
        for source_entry in sources:
            if not isinstance(source_entry, dict):
                raise ValueError(f"invalid source entry: {identifier}")
            source_path = _safe_path(source_entry.get("path"), "source path")
            _safe_path(source_entry.get("destination"), "destination path")
            if source_entry.get("mode") not in ALLOWED_MODES:
                raise ValueError(f"invalid install mode in {identifier}: {source_path}")
            exclusions = source_entry.get("exclude", [])
            if not isinstance(exclusions, list):
                raise ValueError(f"invalid exclusions in {identifier}: {source_path}")
            excluded = {_safe_path(item, "excluded path") for item in exclusions}
            if any(item != source_path and not item.startswith(source_path + "/") for item in excluded):
                raise ValueError(f"exclusion is outside its source in {identifier}: {source_path}")
            selectors.append((identifier, source_path, excluded))

    for identifier, requires in dependencies.items():
        missing = set(requires) - identifiers
        if missing or identifier in requires:
            raise ValueError(f"invalid dependency for {identifier}: {sorted(missing or {identifier})[0]}")
    _validate_dependency_graph(dependencies)

    profiles = catalog.get("profiles")
    if not isinstance(profiles, dict) or set(profiles) != {"essential", "recommended", "complete"}:
        raise ValueError("catalog must define essential, recommended and complete profiles")
    for name, selected in profiles.items():
        if not isinstance(selected, list) or len(selected) != len(set(selected)) or set(selected) - identifiers:
            raise ValueError(f"invalid profile: {name}")
        resolved = set(resolve_dependencies(catalog, selected))
        if resolved != set(selected):
            raise ValueError(f"profile omits a dependency: {name}")
    if set(profiles["complete"]) != identifiers:
        raise ValueError("complete profile must contain every nQuake component")


def _validate_dependency_graph(dependencies: dict[str, list[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(identifier: str) -> None:
        if identifier in visiting:
            raise ValueError(f"cyclic nQuake component dependency: {identifier}")
        if identifier in visited:
            return
        visiting.add(identifier)
        for dependency in dependencies[identifier]:
            visit(dependency)
        visiting.remove(identifier)
        visited.add(identifier)

    for identifier in dependencies:
        visit(identifier)


def components_by_id(catalog: dict[str, object]) -> dict[str, dict[str, object]]:
    return {component["id"]: component for component in catalog["components"]}  # type: ignore[index]


def resolve_dependencies(catalog: dict[str, object], selected: list[str]) -> list[str]:
    components = components_by_id(catalog)
    resolved: list[str] = []
    seen: set[str] = set()

    def add(identifier: str) -> None:
        if identifier not in components:
            raise ValueError(f"unknown nQuake component: {identifier}")
        if identifier in seen:
            return
        for dependency in components[identifier]["requires"]:  # type: ignore[index]
            add(dependency)
        seen.add(identifier)
        resolved.append(identifier)

    for identifier in selected:
        add(identifier)
    return resolved


def source_roots(catalog: dict[str, object]) -> list[str]:
    roots = {
        entry["path"]
        for component in catalog["components"]  # type: ignore[index]
        for entry in component["sources"]
    }
    return sorted(roots)


def component_for_source(catalog: dict[str, object], path: str) -> str | None:
    matches: list[str] = []
    for component in catalog["components"]:  # type: ignore[index]
        for entry in component["sources"]:
            root = entry["path"]
            if path != root and not path.startswith(root + "/"):
                continue
            if path in entry.get("exclude", []):
                continue
            matches.append(component["id"])
    unique = set(matches)
    if len(unique) > 1:
        raise ValueError(f"nQuake source belongs to multiple components: {path}")
    return next(iter(unique), None)


def destination_for_source(component: dict[str, object], path: str) -> tuple[str, str]:
    matches: list[tuple[str, str]] = []
    for entry in component["sources"]:  # type: ignore[index]
        root = entry["path"]
        if path != root and not path.startswith(root + "/"):
            continue
        if path in entry.get("exclude", []):
            continue
        suffix = path.removeprefix(root).lstrip("/")
        destination = entry["destination"]
        matches.append((f"{destination}/{suffix}" if suffix else destination, entry["mode"]))
    if len(matches) != 1:
        raise ValueError(f"nQuake source has no unique destination: {path}")
    return matches[0]


def validate_tree_partition(catalog: dict[str, object], paths: list[str]) -> dict[str, list[str]]:
    partition = {identifier: [] for identifier in components_by_id(catalog)}
    destinations: dict[str, tuple[str, str]] = {}
    components = components_by_id(catalog)
    for path in paths:
        identifier = component_for_source(catalog, path)
        if identifier is None:
            raise ValueError(f"nQuake source is not assigned to a component: {path}")
        destination, _ = destination_for_source(components[identifier], path)
        folded = destination.casefold()
        previous = destinations.get(folded)
        if previous is not None and previous != (identifier, path):
            raise ValueError(f"nQuake destination collision: {destination}")
        destinations[folded] = (identifier, path)
        partition[identifier].append(path)
    empty = [identifier for identifier, assigned in partition.items() if not assigned]
    if empty:
        raise ValueError(f"nQuake component selects no files: {empty[0]}")
    return partition
