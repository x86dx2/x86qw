"""Descoberta limitada de upstreams publicos por HTTPS."""

from __future__ import annotations

import json
import math
import re
import time
import urllib.parse
from dataclasses import dataclass
from html.parser import HTMLParser

try:
    from .components import validate_portable_relative_path
    from .downloader import BoundedMetadata, MAX_ARTIFACT_BYTES, download, safe_url_for_log
except ImportError:  # Execucao direta
    from components import validate_portable_relative_path
    from downloader import BoundedMetadata, MAX_ARTIFACT_BYTES, download, safe_url_for_log


USER_AGENT = "x86qw-maintenance/1"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
SAFE_GITHUB_REPOSITORY = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9][A-Za-z0-9._-]{0,99}$"
)
SAFE_GITHUB_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")
DISCOVERY_MAX_BYTES = 4 * 1024 * 1024
REMOTE_ASSET_MAX_BYTES = MAX_ARTIFACT_BYTES
GITHUB_API_DEADLINE_SECONDS = 60.0
GITHUB_TREE_MAX_ENTRIES = 50_000
GITHUB_API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": USER_AGENT,
}
GITHUB_TREE_TYPES = {
    "blob": frozenset({"100644", "100755"}),
    "tree": frozenset({"040000"}),
}


@dataclass(frozen=True)
class GitTreeEntry:
    path: str
    sha1: str
    size: int


class GitHubCommitMetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "meta":
            return
        values = {name.casefold(): value for name, value in attrs}
        if values.get("property") == "og:url" and isinstance(values.get("content"), str):
            self.urls.append(str(values["content"]))


def _github_repository_name(repository: str) -> str:
    """Return ``owner/repository`` without accepting another network origin."""

    if isinstance(repository, str) and SAFE_GITHUB_REPOSITORY.fullmatch(repository):
        return repository
    if (
        not isinstance(repository, str)
        or not repository
        or any(character == "\\" or ord(character) < 32 or character.isspace() for character in repository)
    ):
        raise ValueError("o upstream precisa identificar um repositorio publico do GitHub")
    try:
        parsed = urllib.parse.urlsplit(repository)
        port = parsed.port
    except ValueError as error:
        raise ValueError("o upstream precisa identificar um repositorio publico do GitHub") from error
    if (
        parsed.scheme.casefold() != "https"
        or parsed.hostname is None
        or parsed.hostname.casefold() != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.query
        or parsed.fragment
        or "%" in parsed.path
    ):
        raise ValueError("o upstream precisa identificar um repositorio publico do GitHub")
    path = parsed.path.removeprefix("/").removesuffix("/")
    if path.endswith(".git"):
        path = path[:-4]
    if SAFE_GITHUB_REPOSITORY.fullmatch(path) is None:
        raise ValueError("o upstream precisa identificar um repositorio publico do GitHub")
    return path


def _github_head_ref(value: str) -> tuple[str, str]:
    """Return the API ref path and the exact ref expected in the response."""

    if not isinstance(value, str):
        raise ValueError("referencia GitHub invalida")
    normalized = value.removeprefix("refs/")
    if "/" not in normalized:
        normalized = f"heads/{normalized}"
    if (
        SAFE_GITHUB_REF.fullmatch(normalized) is None
        or not normalized.startswith("heads/")
        or normalized == "heads/"
        or ".." in normalized
        or "@{" in normalized
        or "//" in normalized
        or normalized.endswith(("/", ".", ".lock"))
        or any(part in ("", ".", "..") for part in normalized.split("/"))
    ):
        raise ValueError("referencia GitHub invalida")
    return normalized, f"refs/{normalized}"


def _remaining_deadline(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if not math.isfinite(remaining) or remaining <= 0:
        raise ValueError("o prazo total da consulta ao GitHub foi excedido")
    return remaining


def _github_api_document(
    repository: str,
    endpoint: str,
    *,
    deadline: float,
    label: str,
) -> dict[str, object]:
    result = download(BoundedMetadata(
        url=f"https://api.github.com/repos/{repository}/{endpoint}",
        maximum_size=DISCOVERY_MAX_BYTES,
        deadline_seconds=_remaining_deadline(deadline),
        headers=GITHUB_API_HEADERS,
        label=label,
    ))
    if result.data is None:
        raise ValueError(f"a API do GitHub nao retornou {label}")
    try:
        document = json.loads(result.data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"a API do GitHub retornou {label} invalido") from error
    if not isinstance(document, dict):
        raise ValueError(f"a API do GitHub retornou {label} invalido")
    return document


def _github_ref_target(
    document: dict[str, object],
    *,
    repository: str,
    expected_ref: str,
) -> str:
    target = document.get("object")
    if (
        document.get("ref") != expected_ref
        or not isinstance(target, dict)
        or target.get("type") != "commit"
        or not isinstance(target.get("sha"), str)
        or HEX40.fullmatch(str(target["sha"])) is None
    ):
        raise ValueError("a API do GitHub retornou uma referencia invalida")
    revision = str(target["sha"])
    if target.get("url") != f"https://api.github.com/repos/{repository}/git/commits/{revision}":
        raise ValueError("a API do GitHub retornou uma referencia invalida")
    return revision


def _github_commit_tree(
    document: dict[str, object],
    *,
    repository: str,
    expected_revision: str,
) -> str:
    tree = document.get("tree")
    if (
        document.get("sha") != expected_revision
        or not isinstance(tree, dict)
        or not isinstance(tree.get("sha"), str)
        or HEX40.fullmatch(str(tree["sha"])) is None
    ):
        raise ValueError("a API do GitHub retornou um commit invalido")
    tree_sha = str(tree["sha"])
    if tree.get("url") != f"https://api.github.com/repos/{repository}/git/trees/{tree_sha}":
        raise ValueError("a API do GitHub retornou um commit invalido")
    return tree_sha


def github_ref_revision(repository: str, ref: str) -> str:
    repository_name = _github_repository_name(repository)
    api_ref, expected_ref = _github_head_ref(ref)
    deadline = time.monotonic() + GITHUB_API_DEADLINE_SECONDS
    document = _github_api_document(
        repository_name,
        "git/ref/" + urllib.parse.quote(api_ref, safe="/"),
        deadline=deadline,
        label="referencia do GitHub",
    )
    return _github_ref_target(
        document,
        repository=repository_name,
        expected_ref=expected_ref,
    )


def _validated_github_tree(
    document: dict[str, object],
    *,
    expected_revision: str,
) -> list[GitTreeEntry]:
    raw_entries = document.get("tree")
    if (
        document.get("sha") != expected_revision
        or document.get("truncated") is not False
        or not isinstance(raw_entries, list)
        or len(raw_entries) > GITHUB_TREE_MAX_ENTRIES
    ):
        raise ValueError("a API do GitHub retornou uma arvore incompleta ou invalida")

    entries: list[GitTreeEntry] = []
    portable_paths: dict[str, str] = {}
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            raise ValueError("a API do GitHub retornou uma entrada de arvore invalida")
        path = validate_portable_relative_path(
            raw_entry.get("path"), "GitHub tree path",
        )
        entry_type = raw_entry.get("type")
        mode = raw_entry.get("mode")
        sha1 = raw_entry.get("sha")
        if (
            not isinstance(entry_type, str)
            or entry_type not in GITHUB_TREE_TYPES
            or mode not in GITHUB_TREE_TYPES[entry_type]
            or not isinstance(sha1, str)
            or HEX40.fullmatch(sha1) is None
        ):
            raise ValueError("a API do GitHub retornou uma entrada de arvore invalida")
        portable_key = path.casefold()
        if portable_key in portable_paths:
            raise ValueError("a API do GitHub retornou caminhos de arvore duplicados")
        portable_paths[portable_key] = path
        size = raw_entry.get("size")
        if entry_type == "blob":
            if type(size) is not int or size < 0 or size > MAX_ARTIFACT_BYTES:
                raise ValueError("a API do GitHub retornou um tamanho de blob invalido")
            entries.append(GitTreeEntry(path, sha1, size))
        elif size is not None:
            raise ValueError("a API do GitHub retornou tamanho em entrada sem blob")
    return entries


def github_recursive_tree(repository: str, branch: str) -> tuple[str, list[GitTreeEntry]]:
    """Read one complete recursive GitHub tree without native Git ingress."""

    repository_name = _github_repository_name(repository)
    api_ref, expected_ref = _github_head_ref(branch)
    deadline = time.monotonic() + GITHUB_API_DEADLINE_SECONDS
    reference = _github_api_document(
        repository_name,
        "git/ref/" + urllib.parse.quote(api_ref, safe="/"),
        deadline=deadline,
        label="referencia do GitHub",
    )
    revision = _github_ref_target(
        reference,
        repository=repository_name,
        expected_ref=expected_ref,
    )
    commit = _github_api_document(
        repository_name,
        f"git/commits/{revision}",
        deadline=deadline,
        label="commit do GitHub",
    )
    tree_sha = _github_commit_tree(
        commit,
        repository=repository_name,
        expected_revision=revision,
    )
    document = _github_api_document(
        repository_name,
        f"git/trees/{tree_sha}?recursive=1",
        deadline=deadline,
        label="arvore recursiva do GitHub",
    )
    return revision, _validated_github_tree(document, expected_revision=tree_sha)


def github_latest_release(repository: str) -> str:
    repository = _github_repository_name(repository)
    url = f"https://github.com/{repository}/releases/latest"
    result = download(BoundedMetadata(
        url=url,
        maximum_size=DISCOVERY_MAX_BYTES,
        deadline_seconds=60,
        headers={"User-Agent": USER_AGENT},
        label="release latest do GitHub",
        method="HEAD",
    ))
    final_path = urllib.parse.urlsplit(result.url).path
    match = re.search(r"/releases/tag/([^/]+)$", final_path)
    if match is None:
        raise ValueError(f"o upstream nao publicou uma release latest: {repository}")
    return urllib.parse.unquote(match.group(1))


def github_commit_revision(repository: str, abbreviation: str) -> str:
    repository = _github_repository_name(repository)
    if not re.fullmatch(r"[0-9a-f]{7,40}", abbreviation):
        raise ValueError(f"commit abreviado invalido: {abbreviation}")
    url = f"https://github.com/{repository}/commit/{abbreviation}"
    result = download(BoundedMetadata(
        url=url,
        maximum_size=DISCOVERY_MAX_BYTES,
        deadline_seconds=60,
        headers={"User-Agent": USER_AGENT},
        label="página de commit do GitHub",
    ))
    assert result.data is not None
    document = result.data
    parser = GitHubCommitMetaParser()
    parser.feed(document.decode("utf-8", "replace"))
    expected_path = re.compile(
        rf"^/{re.escape(repository)}/commit/([0-9a-f]{{40}})$"
    )
    revisions: set[str] = set()
    for url in parser.urls:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme and (
            parsed.scheme.casefold() != "https"
            or parsed.hostname is None
            or parsed.hostname.casefold() != "github.com"
        ):
            continue
        match = expected_path.fullmatch(parsed.path)
        if match is not None:
            revisions.add(match.group(1))
    if len(revisions) != 1:
        raise ValueError(f"nao foi possivel resolver publicamente o commit {abbreviation}: {repository}")
    return revisions.pop()


def remote_content_length(url: str) -> int:
    result = download(BoundedMetadata(
        url=url,
        maximum_size=REMOTE_ASSET_MAX_BYTES,
        deadline_seconds=60,
        headers={"User-Agent": USER_AGENT},
        label="metadados do artefato remoto",
        method="HEAD",
    ))
    length = next(
        (value for name, value in result.headers.items() if name.casefold() == "content-length"),
        None,
    )
    if not isinstance(length, str) or not length.isdigit() or int(length) <= 0:
        raise ValueError(
            "o upstream nao informou o tamanho do artefato: "
            f"{safe_url_for_log(url)}"
        )
    return int(length)
