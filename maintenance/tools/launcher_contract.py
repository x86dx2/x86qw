"""Validate that installed launcher dispatch matches the public command list."""

from __future__ import annotations

import json
import re
from pathlib import Path


_ALIAS_LABELS = frozenset({"help", "-h", "--help", "-V", "--version"})
_SPECIAL_LABELS = frozenset({"", "''", "*"})


def _canonical_commands(project_root: Path) -> tuple[str, ...]:
    path = project_root / "maintenance/inventory/capabilities.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    commands = payload.get("commands")
    if not isinstance(commands, list) or not all(
        isinstance(command, str) and command for command in commands
    ):
        raise ValueError(f"invalid canonical command list: {path}")
    if len(set(commands)) != len(commands):
        raise ValueError(f"canonical command list contains duplicates: {path}")
    return tuple(commands)


def _shell_labels(source: str) -> list[str]:
    start = source.find('case "${1:-}" in')
    if start < 0:
        raise ValueError("x86qw.sh has no top-level command dispatch")
    end = source.find("\nesac", start)
    if end < 0:
        raise ValueError("x86qw.sh command dispatch has no closing esac")
    labels: list[str] = []
    for line in source[start:end].splitlines():
        match = re.match(r"^\s*([^()]+)\)\s*", line)
        if match:
            labels.extend(label.strip() for label in match.group(1).split("|"))
    return labels


def _batch_labels(source: str) -> list[str]:
    return re.findall(
        r'^\s*if\s+/I\s+"%~1"=="([^"]+)"\s+goto\s+\S+',
        source,
        flags=re.IGNORECASE | re.MULTILINE,
    )


def _validate_labels(
    launcher: str,
    labels: list[str],
    commands: tuple[str, ...],
) -> None:
    canonical = set(commands)
    invalid = sorted(
        label for label in labels
        if label not in canonical
        and label not in _ALIAS_LABELS
        and label not in _SPECIAL_LABELS
    )
    if invalid:
        raise ValueError(
            f"{launcher} dispatches undeclared commands: {', '.join(invalid)}"
        )

    public_labels = [label for label in labels if label in canonical]
    duplicates = sorted({label for label in public_labels if public_labels.count(label) > 1})
    missing = sorted(canonical.difference(public_labels))
    if duplicates or missing:
        details = []
        if missing:
            details.append("faltando=" + ",".join(missing))
        if duplicates:
            details.append("duplicados=" + ",".join(duplicates))
        raise ValueError(f"{launcher} diverge do contrato de comandos: {'; '.join(details)}")


def validate_public_launcher_contract(project_root: Path) -> None:
    """Fail closed when either installed launcher drifts from capabilities.json."""

    commands = _canonical_commands(project_root)
    for relative, parser in (
        ("dist/installer/bin/x86qw.sh", _shell_labels),
        ("dist/installer/bin/x86qw.cmd", _batch_labels),
    ):
        path = project_root / relative
        _validate_labels(relative, parser(path.read_text(encoding="utf-8")), commands)


__all__ = ["validate_public_launcher_contract"]
