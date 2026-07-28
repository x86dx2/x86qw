#!/usr/bin/env python3
"""Canonical declaration of components consumed by x86QW."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = ROOT / "distribution/inventory/component-policy.json"


def load_component_policy(path: Path = DEFAULT_POLICY) -> dict[str, dict[str, object]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("format") != 1 or document.get("project") != "x86qw":
        raise ValueError(f"invalid component policy: {path}")
    components = document.get("components")
    if not isinstance(components, dict) or not components:
        raise ValueError(f"component policy has no components: {path}")
    for name, component in components.items():
        if not isinstance(name, str) or not name or not isinstance(component, dict):
            raise ValueError(f"invalid component declaration: {name!r}")
        consumers = component.get("consumers")
        prefixes = component.get("distribution_prefixes")
        if not isinstance(consumers, list) or not consumers or not all(isinstance(item, str) and item for item in consumers):
            raise ValueError(f"component has no explicit consumer: {name}")
        if not isinstance(prefixes, list) or not prefixes or not all(isinstance(item, str) and item.endswith("/") for item in prefixes):
            raise ValueError(f"component has invalid distribution prefixes: {name}")
    return components


def require_component(components: dict[str, dict[str, object]], name: str, path: str | None = None) -> None:
    component = components.get(name)
    if component is None:
        raise ValueError(f"component is not consumed by x86QW: {name}")
    if path is not None:
        prefixes = component["distribution_prefixes"]
        assert isinstance(prefixes, list)
        if not any(path.startswith(prefix) for prefix in prefixes):
            raise ValueError(f"distribution path is outside the declared consumer scope for {name}: {path}")


def component_for_distribution_path(components: dict[str, dict[str, object]], path: str) -> str | None:
    matches = [
        name for name, component in components.items()
        if any(path.startswith(prefix) for prefix in component["distribution_prefixes"])
    ]
    if len(matches) > 1:
        raise ValueError(f"distribution path belongs to multiple components: {path}")
    return matches[0] if matches else None
