"""Runtime-safe loaders and models for installed x86QW catalogs."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path, PurePosixPath


IDENTIFIER = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
COMPONENT_ID = IDENTIFIER
ALLOWED_COMPONENT_KINDS = frozenset({
    "core", "gameplay", "content", "addon", "documentation", "runtime",
    "service",
})
SERVICE_PLATFORMS = frozenset({
    "macos-arm64", "linux-amd64", "windows-x64",
})
WINDOWS_FORBIDDEN_PATH_CHARACTERS = frozenset('<>:"\\|?*')
WINDOWS_RESERVED_PATH_NAMES = frozenset({
    "CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
    *(f"COM{number}" for number in "¹²³"),
    *(f"LPT{number}" for number in "¹²³"),
})
PORTABLE_RELATIVE_PATH_MAX_UTF16_UNITS = 240
PORTABLE_PATH_COMPONENT_MAX_UTF16_UNITS = 255


def profile_fingerprint(selected: list[str]) -> str:
    payload = "".join(
        identifier + "\n" for identifier in sorted(selected)
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _utf16_units(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def validate_portable_relative_path(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or any(unicodedata.category(character).startswith("C") for character in value)
        or any(character in WINDOWS_FORBIDDEN_PATH_CHARACTERS for character in value)
    ):
        raise ValueError(f"invalid {label}: {value!r}")
    raw_parts = value.split("/")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in raw_parts):
        raise ValueError(f"unsafe {label}: {value}")
    for part in raw_parts:
        base_name = part.split(".", 1)[0].upper()
        if (
            unicodedata.normalize("NFC", part) != part
            or part.endswith((".", " "))
            or base_name in WINDOWS_RESERVED_PATH_NAMES
            or _utf16_units(part) > PORTABLE_PATH_COMPONENT_MAX_UTF16_UNITS
        ):
            raise ValueError(f"non-portable {label} component: {part!r}")
    if _utf16_units(value) > PORTABLE_RELATIVE_PATH_MAX_UTF16_UNITS:
        raise ValueError(
            f"{label} exceeds {PORTABLE_RELATIVE_PATH_MAX_UTF16_UNITS} UTF-16 code units"
        )
    return path.as_posix()


def _load_document(path: Path, collection: str) -> dict[str, object]:
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
    if (
        not isinstance(document, dict)
        or document.get("format") != 1
        or document.get("project") != "x86qw"
    ):
        raise ValueError("invalid capabilities catalog identity")
    return document


def load_runtimes(path: Path) -> dict[str, object]:
    return _load_document(path, "runtimes")


def load_games(path: Path) -> dict[str, object]:
    return _load_document(path, "games")


def load_compatibility(path: Path) -> dict[str, object]:
    return _load_document(path, "compatibility")


def _id_entries(
    document: dict[str, object], key: str, label: str,
) -> dict[str, dict[str, object]]:
    entries = document.get(key)
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{label} catalog is empty")
    result: dict[str, dict[str, object]] = {}
    for entry in entries:
        identifier = entry.get("id") if isinstance(entry, dict) else None
        if (
            not isinstance(identifier, str)
            or not IDENTIFIER.fullmatch(identifier)
            or identifier in result
        ):
            raise ValueError(f"invalid or duplicate {label} id: {identifier}")
        result[identifier] = entry
    return result


def runtimes_by_id(
    document: dict[str, object],
) -> dict[str, dict[str, object]]:
    return _id_entries(document, "runtimes", "runtime")


def games_by_id(
    document: dict[str, object],
) -> dict[str, dict[str, object]]:
    return _id_entries(document, "games", "game")


def components_by_id(
    catalog: dict[str, object],
) -> dict[str, dict[str, object]]:
    return {
        component["id"]: component
        for component in catalog["components"]  # type: ignore[index]
    }


def resolve_dependencies(
    catalog: dict[str, object], selected: list[str],
) -> list[str]:
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


def _validate_component_dependency_graph(
    dependencies: dict[str, list[str]],
) -> None:
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


def validate_component_catalog(catalog: object) -> None:
    if (
        not isinstance(catalog, dict)
        or catalog.get("format") != 1
        or catalog.get("project") != "x86qw-runtime"
    ):
        raise ValueError("invalid runtime component catalog identity")
    components = catalog.get("components")
    if not isinstance(components, list) or not components:
        raise ValueError("runtime component catalog is empty")
    identifiers: set[str] = set()
    dependencies: dict[str, list[str]] = {}
    for component in components:
        if not isinstance(component, dict):
            raise ValueError("invalid runtime component entry")
        identifier = component.get("id")
        if (
            not isinstance(identifier, str)
            or not COMPONENT_ID.fullmatch(identifier)
            or identifier in identifiers
        ):
            raise ValueError(f"invalid or duplicate runtime component id: {identifier}")
        identifiers.add(identifier)
        if component.get("kind") not in ALLOWED_COMPONENT_KINDS:
            raise ValueError(f"invalid runtime component kind: {identifier}")
        if not all(
            isinstance(component.get(field), str) and component[field]
            for field in ("label", "description")
        ):
            raise ValueError(f"runtime component lacks user-facing metadata: {identifier}")
        requires = component.get("requires")
        if not isinstance(requires, list) or not all(
            isinstance(item, str) for item in requires
        ):
            raise ValueError(f"invalid runtime dependencies: {identifier}")
        dependencies[identifier] = requires
        managed_files = component.get("managed_files")
        if (
            not isinstance(managed_files, list)
            or len(managed_files) != len(set(managed_files))
        ):
            raise ValueError(f"invalid runtime managed files: {identifier}")
        for path in managed_files:
            validate_portable_relative_path(path, "runtime managed path")
        platform_files = component.get("platform_files")
        if not isinstance(platform_files, list):
            raise ValueError(f"invalid runtime platform files: {identifier}")
        seen_platforms: set[str] = set()
        for entry in platform_files:
            if (
                not isinstance(entry, dict)
                or set(entry) != {"platform", "package_path", "install_path"}
            ):
                raise ValueError(f"invalid runtime platform file: {identifier}")
            platform = entry.get("platform")
            if platform not in SERVICE_PLATFORMS or platform in seen_platforms:
                raise ValueError(
                    f"invalid or duplicate runtime platform file: {identifier}/{platform}"
                )
            seen_platforms.add(str(platform))
            package_path = validate_portable_relative_path(
                entry.get("package_path"), "runtime package path",
            )
            install_path = validate_portable_relative_path(
                entry.get("install_path"), "runtime install path",
            )
            if not package_path.startswith(
                f"platforms/{identifier}/{platform}/"
            ):
                raise ValueError(
                    f"runtime platform file has an invalid package path: {identifier}/{platform}"
                )
            if install_path not in managed_files:
                raise ValueError(
                    f"runtime platform file is absent from managed files: {identifier}/{platform}"
                )
    for identifier, requires in dependencies.items():
        missing = set(requires) - identifiers
        if missing or identifier in requires:
            raise ValueError(f"invalid runtime dependency for {identifier}")
    _validate_component_dependency_graph(dependencies)
    namespaces = catalog.get("content_namespaces")
    if (
        not isinstance(namespaces, list)
        or not namespaces
        or len(namespaces) != len(set(namespaces))
        or not all(
            isinstance(item, str) and COMPONENT_ID.fullmatch(item)
            for item in namespaces
        )
    ):
        raise ValueError("invalid runtime content namespaces")
    profiles = catalog.get("profiles")
    if (
        not isinstance(profiles, dict)
        or set(profiles) != {"essential", "recommended", "complete"}
    ):
        raise ValueError("invalid runtime profiles")
    for name, selected in profiles.items():
        if (
            not isinstance(selected, list)
            or len(selected) != len(set(selected))
            or set(selected) - identifiers
        ):
            raise ValueError(f"invalid runtime profile: {name}")
        if set(resolve_dependencies(catalog, selected)) != set(selected):
            raise ValueError(f"runtime profile omits a dependency: {name}")
    if set(profiles["complete"]) != identifiers:
        raise ValueError("runtime complete profile must contain every component")
    history = catalog.get("profile_history")
    if not isinstance(history, dict) or set(history) != set(profiles):
        raise ValueError("invalid runtime profile history")
    for name, fingerprints in history.items():
        if (
            not isinstance(fingerprints, list)
            or not fingerprints
            or len(fingerprints) != len(set(fingerprints))
            or not all(
                isinstance(value, str)
                and re.fullmatch(r"[0-9a-f]{64}", value)
                for value in fingerprints
            )
            or profile_fingerprint(profiles[name]) not in fingerprints
        ):
            raise ValueError(f"invalid runtime profile history: {name}")


def load_component_catalog(path: Path) -> dict[str, object]:
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read runtime component catalog: {path}") from error
    validate_component_catalog(catalog)
    return catalog


def project_component_catalog(catalog: dict[str, object]) -> dict[str, object]:
    """Project the repository inventory into the installed component contract."""

    try:
        projected = {
            "format": 1,
            "project": "x86qw-runtime",
            "content_namespaces": list(catalog["content_namespaces"]),
            "profiles": catalog["profiles"],
            "profile_history": catalog["profile_history"],
            "components": [
                {
                    "id": component["id"],
                    "label": component["label"],
                    "kind": component["kind"],
                    "description": component["description"],
                    "requires": component["requires"],
                    "managed_files": list(dict.fromkeys(
                        entry.get("install_destination", entry["destination"])
                        for entry in component.get("project_sources", [])
                        if entry.get("mode") == "overlay"
                    )),
                    "platform_files": [
                        {
                            "platform": entry["platform"],
                            "package_path": entry["destination"],
                            "install_path": entry["install_destination"],
                        }
                        for entry in component.get("project_sources", [])
                        if "platform" in entry
                    ],
                }
                for component in catalog["components"]
            ],
        }
    except (KeyError, TypeError) as error:
        raise ValueError("invalid development component catalog") from error
    validate_component_catalog(projected)
    return projected


def load_development_component_catalog(path: Path) -> dict[str, object]:
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read component catalog: {path}") from error
    if not isinstance(catalog, dict):
        raise ValueError("invalid development component catalog")
    # Validate the exact runtime projection while preserving repository-only
    # source metadata required by development installs.
    project_component_catalog(catalog)
    return catalog
