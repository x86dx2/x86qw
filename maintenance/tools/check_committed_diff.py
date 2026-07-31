#!/usr/bin/env python3
"""Validate whitespace in the committed range represented by a GitHub event."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


ZERO_SHA = "0" * 40


def checked_sha(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) != 40 or any(
        character not in "0123456789abcdefABCDEF" for character in value
    ):
        raise ValueError(f"SHA inválido para {label}: {value!r}")
    return value


def committed_diff_command(event_name: str, event: dict[str, object]) -> list[str]:
    if event_name == "pull_request":
        pull_request = event.get("pull_request")
        if not isinstance(pull_request, dict):
            raise ValueError("Evento de pull request sem metadados do PR.")
        base = pull_request.get("base")
        head = pull_request.get("head")
        if not isinstance(base, dict) or not isinstance(head, dict):
            raise ValueError("Evento de pull request sem base/head.")
        return [
            "git", "diff", "--check",
            checked_sha(base.get("sha"), "base"),
            checked_sha(head.get("sha"), "head"),
        ]
    if event_name == "push":
        before = checked_sha(event.get("before"), "before")
        after = checked_sha(event.get("after"), "after")
        if before != ZERO_SHA:
            return ["git", "diff", "--check", before, after]
    return ["git", "show", "--check", "--format=", "HEAD"]


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-name", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    parser.add_argument(
        "--event-file",
        type=Path,
        default=Path(os.environ["GITHUB_EVENT_PATH"]) if os.environ.get("GITHUB_EVENT_PATH") else None,
    )
    options = parser.parse_args(arguments)
    if options.event_file is None:
        parser.error("informe --event-file ou GITHUB_EVENT_PATH")
    try:
        event = json.loads(options.event_file.read_text(encoding="utf-8"))
        if not isinstance(event, dict):
            raise ValueError("Evento do GitHub precisa ser um objeto JSON.")
        command = committed_diff_command(options.event_name, event)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"Erro ao determinar o diff commitado: {error}", file=sys.stderr)
        return 2
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
