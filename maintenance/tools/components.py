"""Validation and selection helpers for the x86QW component catalog."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath


COMPONENT_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ALLOWED_KINDS = {"core", "gameplay", "content", "addon", "documentation"}
ALLOWED_MODES = {"overlay", "default", "preserve"}
ALLOWED_ORIGINS = {"reference", "release"}


def profile_fingerprint(selected: list[str]) -> str:
    payload = "".join(identifier + "\n" for identifier in sorted(selected)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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
        raise ValueError(f"cannot read component catalog: {path}") from error
    validate_catalog(catalog)
    return catalog


def validate_catalog(catalog: object) -> None:
    if not isinstance(catalog, dict) or catalog.get("format") != 1 or catalog.get("project") != "x86qw":
        raise ValueError("invalid component catalog identity")
    client = catalog.get("client")
    if not isinstance(client, dict) or client.get("id") != "ezquake" or client.get("channels") != ["stable", "nightly"]:
        raise ValueError("the active x86QW client must be ezQuake stable and nightly")
    namespaces = catalog.get("content_namespaces")
    if (
        not isinstance(namespaces, list)
        or not namespaces
        or len(namespaces) != len(set(namespaces))
        or not all(isinstance(item, str) and COMPONENT_ID.fullmatch(item) for item in namespaces)
    ):
        raise ValueError("invalid content component namespaces")
    source = catalog.get("reference_source")
    if (
        not isinstance(source, dict)
        or not isinstance(source.get("repository"), str)
        or not isinstance(source.get("ref"), str)
    ):
        raise ValueError("invalid nQuake reference source")
    components = catalog.get("components")
    if not isinstance(components, list) or not components:
        raise ValueError("component catalog is empty")

    identifiers: set[str] = set()
    selectors: list[tuple[str, str, set[str]]] = []
    dependencies: dict[str, list[str]] = {}
    project_destinations: set[str] = set()
    for component in components:
        if not isinstance(component, dict):
            raise ValueError("invalid component entry")
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
            if source_entry.get("origin", "reference") not in ALLOWED_ORIGINS:
                raise ValueError(f"invalid source origin in {identifier}: {source_path}")
            exclusions = source_entry.get("exclude", [])
            if not isinstance(exclusions, list):
                raise ValueError(f"invalid exclusions in {identifier}: {source_path}")
            excluded = {_safe_path(item, "excluded path") for item in exclusions}
            if any(item != source_path and not item.startswith(source_path + "/") for item in excluded):
                raise ValueError(f"exclusion is outside its source in {identifier}: {source_path}")
            selectors.append((identifier, source_path, excluded))
        project_sources = component.get("project_sources", [])
        if not isinstance(project_sources, list):
            raise ValueError(f"invalid project sources: {identifier}")
        for source_entry in project_sources:
            if not isinstance(source_entry, dict):
                raise ValueError(f"invalid project source entry: {identifier}")
            source_path = _safe_path(source_entry.get("path"), "project source path")
            destination = _safe_path(source_entry.get("destination"), "project destination path")
            if not source_path.startswith("dist/"):
                raise ValueError(f"project source is outside the distribution: {source_path}")
            if source_entry.get("mode") not in {"overlay", "default"}:
                raise ValueError(f"invalid project install mode in {identifier}: {source_path}")
            folded = destination.casefold()
            if folded in project_destinations:
                raise ValueError(f"duplicate project destination: {destination}")
            project_destinations.add(folded)
        project_overrides = component.get("project_overrides", [])
        if not isinstance(project_overrides, list):
            raise ValueError(f"invalid project overrides: {identifier}")
        override_targets: set[str] = set()
        for override in project_overrides:
            if not isinstance(override, dict):
                raise ValueError(f"invalid project override: {identifier}")
            source_path = _safe_path(override.get("path"), "project override path")
            target = _safe_path(override.get("target"), "project override target")
            if not source_path.startswith("dist/"):
                raise ValueError(f"project override is outside the distribution: {source_path}")
            folded_target = target.casefold()
            if folded_target in override_targets:
                raise ValueError(f"duplicate project override target: {target}")
            override_targets.add(folded_target)
            matches = [
                entry for entry in sources
                if target == entry["path"] or target.startswith(entry["path"] + "/")
            ]
            if len(matches) != 1 or matches[0]["mode"] == "preserve":
                raise ValueError(f"project override has no runtime target in {identifier}: {target}")

    for identifier, requires in dependencies.items():
        missing = set(requires) - identifiers
        if missing or identifier in requires:
            raise ValueError(f"invalid dependency for {identifier}: {sorted(missing or {identifier})[0]}")
    _validate_dependency_graph(dependencies)

    compatibility = catalog.get("compatibility")
    if not isinstance(compatibility, dict) or compatibility.get("policy") != "common-baseline":
        raise ValueError("component catalog must declare the common stable/nightly baseline")
    compatible_clients = compatibility.get("clients")
    if (
        not isinstance(compatible_clients, dict)
        or set(compatible_clients) != {"stable", "nightly"}
        or not all(isinstance(version, str) and version for version in compatible_clients.values())
    ):
        raise ValueError("invalid stable/nightly compatibility versions")
    covered = compatibility.get("covered_components")
    if not isinstance(covered, list) or len(covered) != len(set(covered)) or set(covered) != identifiers:
        raise ValueError("stable/nightly compatibility must cover every component")
    verified = compatibility.get("verified")
    if not isinstance(verified, list) or not verified or not all(isinstance(item, str) and item for item in verified):
        raise ValueError("invalid stable/nightly verification scope")

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
        raise ValueError("complete profile must contain every x86QW component")
    history = catalog.get("profile_history")
    if not isinstance(history, dict) or set(history) != set(profiles):
        raise ValueError("catalog must preserve profile history")
    claimed: dict[str, str] = {}
    for name, fingerprints in history.items():
        if (
            not isinstance(fingerprints, list)
            or not fingerprints
            or len(fingerprints) != len(set(fingerprints))
            or not all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) for value in fingerprints)
        ):
            raise ValueError(f"invalid profile history: {name}")
        current = profile_fingerprint(profiles[name])
        if current not in fingerprints:
            raise ValueError(f"profile history omits current definition: {name}")
        for fingerprint in fingerprints:
            previous = claimed.setdefault(fingerprint, name)
            if previous != name:
                raise ValueError(f"profile history is ambiguous: {previous} and {name}")


def _validate_dependency_graph(dependencies: dict[str, list[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(identifier: str) -> None:
        if identifier in visiting:
            raise ValueError(f"cyclic component dependency: {identifier}")
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
            raise ValueError(f"unknown component: {identifier}")
        if identifier in seen:
            return
        for dependency in components[identifier]["requires"]:  # type: ignore[index]
            add(dependency)
        seen.add(identifier)
        resolved.append(identifier)

    for identifier in selected:
        add(identifier)
    return resolved


def source_roots(catalog: dict[str, object], origin: str = "reference") -> list[str]:
    roots = {
        entry["path"]
        for component in catalog["components"]  # type: ignore[index]
        for entry in component["sources"]
        if entry.get("origin", "reference") == origin
    }
    return sorted(roots)


def component_for_source(catalog: dict[str, object], path: str, origin: str | None = None) -> str | None:
    matches: list[str] = []
    for component in catalog["components"]:  # type: ignore[index]
        for entry in component["sources"]:
            if origin is not None and entry.get("origin", "reference") != origin:
                continue
            root = entry["path"]
            if path != root and not path.startswith(root + "/"):
                continue
            if _excluded(path, entry.get("exclude", [])):
                continue
            matches.append(component["id"])
    unique = set(matches)
    if len(unique) > 1:
        raise ValueError(f"source belongs to multiple components: {path}")
    return next(iter(unique), None)


def destination_for_source(
    component: dict[str, object], path: str, origin: str | None = None,
) -> tuple[str, str]:
    matches: list[tuple[str, str]] = []
    for entry in component["sources"]:  # type: ignore[index]
        if origin is not None and entry.get("origin", "reference") != origin:
            continue
        root = entry["path"]
        if path != root and not path.startswith(root + "/"):
            continue
        if _excluded(path, entry.get("exclude", [])):
            continue
        suffix = path.removeprefix(root).lstrip("/")
        destination = entry["destination"]
        matches.append((f"{destination}/{suffix}" if suffix else destination, entry["mode"]))
    if len(matches) != 1:
        raise ValueError(f"source has no unique destination: {path}")
    return matches[0]


def _excluded(path: str, exclusions: object) -> bool:
    assert isinstance(exclusions, list)
    return any(path == item or path.startswith(str(item) + "/") for item in exclusions)


def validate_tree_partition(
    catalog: dict[str, object], paths: list[str], origin: str = "reference",
) -> dict[str, list[str]]:
    components = components_by_id(catalog)
    selected_components = {
        identifier for identifier, component in components.items()
        if any(entry.get("origin", "reference") == origin for entry in component["sources"])
    }
    partition = {identifier: [] for identifier in selected_components}
    destinations: dict[str, tuple[str, str]] = {}
    for path in paths:
        identifier = component_for_source(catalog, path, origin)
        if identifier is None:
            raise ValueError(f"reference source is not assigned to a component: {path}")
        destination, _ = destination_for_source(components[identifier], path, origin)
        folded = destination.casefold()
        previous = destinations.get(folded)
        if previous is not None and previous != (identifier, path):
            raise ValueError(f"component destination collision: {destination}")
        destinations[folded] = (identifier, path)
        partition[identifier].append(path)
    empty = [identifier for identifier, assigned in partition.items() if not assigned]
    if empty:
        raise ValueError(f"component selects no files: {empty[0]}")
    return partition
