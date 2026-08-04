"""Pure, idempotent migrations for persisted x86QW runtime contracts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .catalogs import profile_fingerprint
from .state import InstallState, parse_install_state


def _replace_component_ids(
    values: tuple[str, ...],
    *,
    replacements: Mapping[str, str],
    removals: frozenset[str],
) -> list[str]:
    migrated: list[str] = []
    for identifier in values:
        if identifier in removals:
            continue
        current = replacements.get(identifier, identifier)
        if current not in migrated:
            migrated.append(current)
    return migrated


def migrate_install_state(
    state: InstallState,
    *,
    replacements: Mapping[str, str],
    removals: Iterable[str],
    allowed_profiles: Iterable[str],
    allowed_capabilities: Iterable[str],
) -> InstallState:
    """Return the current state shape without mutating the accepted source."""

    removed = frozenset(removals)
    document = state.to_document()
    requested = _replace_component_ids(
        state.requested_components,
        replacements=replacements,
        removals=removed,
    )
    recorded = _replace_component_ids(
        state.recorded_components,
        replacements=replacements,
        removals=removed,
    )
    known = _replace_component_ids(
        state.known_components,
        replacements=replacements,
        removals=removed,
    )
    document.update({
        "format": 2,
        "requested_components": requested,
        "recorded_components": recorded,
        "known_components": known,
        "capabilities": list(state.capabilities),
        "component_fingerprint": profile_fingerprint(recorded),
    })
    return parse_install_state(
        document,
        allowed_profiles=allowed_profiles,
        allowed_capabilities=allowed_capabilities,
    )
