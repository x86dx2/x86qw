#!/usr/bin/env python3
"""Fail-closed check for open P0/P1 GitHub issues.

Preparation and rehearsal may run while issues are open, but each public
mutation must query this boundary immediately before it starts.  All remote
bytes cross the canonical ``RemoteClient``/bounded-downloader boundary.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from x86qw_runtime.io.remote import RemoteClient


DEFAULT_API = "https://api.github.com"
PAGE_SIZE = 100
MAX_PAGE_BYTES = 2 * 1024 * 1024
MAX_PAGES = 20
MAX_ISSUES = PAGE_SIZE * MAX_PAGES
DEFAULT_DEADLINE_SECONDS = 30.0
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class BlockerCheckError(RuntimeError):
    """The remote result cannot be trusted as a complete issue set."""


class _QuietReporter:
    def detail(self, message: str) -> None:
        del message

    def warning(self, message: str) -> None:
        del message

    def download_progress(
        self, received: int, total: int | None, *, done: bool = False,
    ) -> None:
        del received, total, done


def _repository(value: str) -> str:
    if not isinstance(value, str) or not REPOSITORY_RE.fullmatch(value):
        raise BlockerCheckError("repositório GitHub inválido")
    return value


def _token(value: str | None) -> str:
    if not isinstance(value, str) or not value or any(
        char.isspace() or ord(char) < 0x20 for char in value
    ):
        raise BlockerCheckError("token GitHub ausente ou inválido")
    return value


def _validate_issue(issue: Any) -> dict[str, Any]:
    if not isinstance(issue, dict):
        raise BlockerCheckError("schema de issue do GitHub inválido")
    number = issue.get("number")
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        raise BlockerCheckError("schema de issue do GitHub inválido")
    if issue.get("state") != "open":
        raise BlockerCheckError("resposta de issues não está limitada a itens abertos")
    labels = issue.get("labels")
    if not isinstance(labels, list):
        raise BlockerCheckError("schema de labels do GitHub inválido")
    for label in labels:
        if not isinstance(label, dict) or not isinstance(label.get("name"), str):
            raise BlockerCheckError("schema de labels do GitHub inválido")
    return issue


def fetch_open_issues(
    repository: str,
    token: str,
    *,
    api_root: str = DEFAULT_API,
    client: RemoteClient | None = None,
    fetcher: Callable[[str, float], bytes] | None = None,
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
    max_pages: int = MAX_PAGES,
    max_issues: int = MAX_ISSUES,
) -> list[dict[str, Any]]:
    """Fetch a complete, schema-validated open-issue listing.

    ``fetcher`` exists only for deterministic unit tests. Production callers
    use ``RemoteClient`` and therefore the same bounded downloader as every
    other remote read in x86QW.
    """
    repository = _repository(repository)
    token = _token(token)
    if deadline_seconds <= 0 or max_pages <= 0 or max_issues <= 0:
        raise BlockerCheckError("limites da consulta de issues inválidos")
    if not isinstance(api_root, str) or not api_root.startswith("https://"):
        raise BlockerCheckError("endpoint da API do GitHub precisa ser HTTPS")
    if client is not None and fetcher is not None:
        raise BlockerCheckError("consulta de issues recebeu dois transportes")
    if fetcher is None:
        selected_client = client or RemoteClient(_QuietReporter())

        def fetcher(url: str, timeout: float) -> bytes:
            return selected_client.get(
                url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "x86qw-release-gate",
                },
                maximum_size=MAX_PAGE_BYTES,
                timeout=timeout,
                attempts=1,
            )
    deadline = time.monotonic() + deadline_seconds
    issues: list[dict[str, Any]] = []
    page = 1
    while True:
        if page > max_pages:
            raise BlockerCheckError("limite de paginação da API do GitHub excedido")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BlockerCheckError("prazo total da consulta de issues excedido")
        url = (
            f"{api_root.rstrip('/')}/repos/{repository}/issues"
            f"?state=open&per_page={PAGE_SIZE}&page={page}"
        )
        try:
            body = fetcher(url, max(0.1, min(10.0, remaining)))
        except BlockerCheckError:
            raise
        except Exception as error:
            # Do not expose the URL, token, or transport internals in a gate
            # failure; the caller only needs to know that the result is unsafe.
            raise BlockerCheckError("falha de rede na API do GitHub") from error
        if not isinstance(body, bytes) or len(body) > MAX_PAGE_BYTES:
            raise BlockerCheckError("resposta da API do GitHub excede o limite")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BlockerCheckError("JSON inválido na resposta da API do GitHub") from error
        if not isinstance(payload, list):
            raise BlockerCheckError("schema de issues do GitHub inválido")
        current = [_validate_issue(issue) for issue in payload]
        issues.extend(current)
        if len(issues) > max_issues:
            raise BlockerCheckError("quantidade de issues excede o limite seguro")
        # The canonical client intentionally exposes bytes, not transport
        # headers. A full page always requires a terminal empty page; if the
        # configured page limit is reached we fail closed instead of accepting
        # a potentially truncated result.
        if len(current) == PAGE_SIZE:
            if page >= max_pages:
                raise BlockerCheckError("limite de paginação da API do GitHub excedido")
            page += 1
            continue
        return issues


def find_blockers(issues: Sequence[Mapping[str, Any]]) -> list[int]:
    """Return exact P0/P1 issue numbers, excluding pull requests."""
    blockers: list[int] = []
    for raw_issue in issues:
        issue = _validate_issue(raw_issue)
        if "pull_request" in issue:
            continue
        labels = {label["name"] for label in issue["labels"]}
        if labels.intersection({"P0", "P1"}):
            blockers.append(issue["number"])
    return sorted(set(blockers))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument(
        "--token",
        default=os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"),
    )
    args = parser.parse_args(argv)
    try:
        token = _token(args.token)
        issues = fetch_open_issues(args.repo, token)
        blockers = find_blockers(issues)
    except BlockerCheckError as error:
        print(f"[ERRO] Gate P0/P1 inconclusivo: {error}", file=sys.stderr)
        return 2
    if blockers:
        numbers = ", ".join(str(number) for number in blockers)
        print(f"[ERRO] Issues P0/P1 abertas: {numbers}", file=sys.stderr)
        return 1
    print("[OK] Nenhuma issue P0/P1 aberta.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
