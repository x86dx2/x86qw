#!/usr/bin/env python3
"""Validate versioned x86QW mirror recipes using only Python's stdlib."""

from __future__ import annotations

import json
import sys
from pathlib import Path, PurePosixPath

try:
    from .validate_catalog import ROOT, validate_package
except ImportError:  # Execucao direta
    from validate_catalog import ROOT, validate_package


DEFAULT_RECIPES = ROOT / "distribution/recipes"
FORMATS = {"tar.gz", "zip"}
REVIEW_STATES = {"blocked", "ready"}


def validate_recipe(recipe: object, label: str = "recipe") -> str:
    if not isinstance(recipe, dict):
        raise ValueError(f"{label} must be a JSON object")
    if recipe.get("format") != 1 or recipe.get("project") != "x86qw":
        raise ValueError(f"{label} has an unsupported identity or format")
    if recipe.get("kind") != "mirror":
        raise ValueError(f"{label}.kind must be mirror")

    package = recipe.get("package")
    validate_package(package, f"{label}.package", require_reviewed=False)
    assert isinstance(package, dict)
    if package.get("artifact_format") not in FORMATS:
        raise ValueError(f"{label}.package.artifact_format is invalid")

    expected_members = package.get("expected_members")
    if not isinstance(expected_members, list) or not expected_members:
        raise ValueError(f"{label}.package.expected_members must not be empty")
    if not all(isinstance(member, str) and member for member in expected_members):
        raise ValueError(f"{label}.package.expected_members must contain paths")
    if len(expected_members) != len(set(expected_members)):
        raise ValueError(f"{label}.package.expected_members contains duplicates")
    for member in expected_members:
        path = PurePosixPath(member)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"{label}.package.expected_members contains an unsafe path")

    review = recipe.get("review")
    if not isinstance(review, dict) or review.get("status") not in REVIEW_STATES:
        raise ValueError(f"{label}.review.status is invalid")
    if not isinstance(review.get("notes"), str) or not review["notes"].strip():
        raise ValueError(f"{label}.review.notes must not be empty")
    reviewed = package["redistribution_reviewed"]
    if (review["status"] == "ready") != reviewed:
        raise ValueError(f"{label} review status and redistribution flag disagree")
    return review["status"]


def recipe_paths(root: Path = DEFAULT_RECIPES) -> list[Path]:
    return sorted(root.rglob("*.json"))


def main(argv: list[str]) -> int:
    paths = [Path(argument) for argument in argv[1:]] or recipe_paths()
    if not paths:
        raise ValueError("no recipes found")
    counts = {state: 0 for state in REVIEW_STATES}
    for path in paths:
        state = validate_recipe(json.loads(path.read_text(encoding="utf-8")), str(path))
        counts[state] += 1
    print(f"recipes valid: {len(paths)} ({counts['ready']} ready, {counts['blocked']} blocked)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"recipes invalid: {error}", file=sys.stderr)
        raise SystemExit(1)
