"""Descoberta de upstreams publicos sem API, conta ou token."""

from __future__ import annotations

import re
import subprocess
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


USER_AGENT = "x86qw-maintenance/1"
HEX40 = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class GitTreeEntry:
    path: str
    sha1: str


def run_git(arguments: list[str], *, binary: bool = False) -> str | bytes:
    result = subprocess.run(
        ["git", *arguments],
        check=False,
        capture_output=True,
        text=not binary,
    )
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", "replace") if binary else result.stderr
        raise ValueError(f"falha ao consultar upstream Git: {str(error).strip()}")
    return result.stdout


def git_remote_revision(repository: str, ref: str) -> str:
    output = str(run_git(["ls-remote", "--exit-code", repository, ref])).strip().splitlines()
    revisions = {line.split()[0] for line in output if len(line.split()) == 2}
    if len(revisions) != 1:
        raise ValueError(f"o upstream Git nao resolveu uma revisao unica para {ref}: {repository}")
    revision = revisions.pop()
    if not HEX40.fullmatch(revision):
        raise ValueError(f"o upstream Git retornou uma revisao invalida: {repository}")
    return revision


def git_remote_tree(repository: str, branch: str) -> tuple[str, list[GitTreeEntry]]:
    """Baixa apenas commits e arvores; os blobs permanecem no upstream."""
    with tempfile.TemporaryDirectory(prefix="x86qw-git-tree-") as temporary:
        checkout = Path(temporary) / "repository"
        run_git([
            "-c", "protocol.version=2", "clone", "--quiet", "--depth", "1",
            "--filter=blob:none", "--no-checkout", "--no-tags", "--single-branch",
            "--branch", branch, repository, str(checkout),
        ])
        revision = str(run_git(["-C", str(checkout), "rev-parse", "HEAD"])).strip()
        raw = run_git(["-C", str(checkout), "ls-tree", "-r", "-z", "HEAD"], binary=True)
    if not isinstance(raw, bytes) or not HEX40.fullmatch(revision):
        raise ValueError(f"arvore Git invalida: {repository}")
    entries: list[GitTreeEntry] = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, separator, encoded_path = record.partition(b"\t")
        fields = metadata.split()
        if not separator or len(fields) != 3 or fields[1] != b"blob":
            continue
        sha1 = fields[2].decode("ascii")
        if HEX40.fullmatch(sha1):
            entries.append(GitTreeEntry(encoded_path.decode("utf-8", "surrogateescape"), sha1))
    return revision, entries


def public_request(url: str, *, method: str = "HEAD") -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method=method)


def github_latest_release(repository: str) -> str:
    url = f"https://github.com/{repository}/releases/latest"
    with urllib.request.urlopen(public_request(url), timeout=60) as response:
        final_path = urllib.parse.urlsplit(response.geturl()).path
    match = re.search(r"/releases/tag/([^/]+)$", final_path)
    if match is None:
        raise ValueError(f"o upstream nao publicou uma release latest: {repository}")
    return urllib.parse.unquote(match.group(1))


def github_commit_revision(repository: str, abbreviation: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{7,40}", abbreviation):
        raise ValueError(f"commit abreviado invalido: {abbreviation}")
    url = f"https://github.com/{repository}/commit/{abbreviation}"
    with urllib.request.urlopen(public_request(url, method="GET"), timeout=60) as response:
        document = response.read()
    pattern = rb"/" + re.escape(repository.encode()) + rb"/commit/([0-9a-f]{40})"
    revisions = set(re.findall(pattern, document))
    if len(revisions) != 1:
        raise ValueError(f"nao foi possivel resolver publicamente o commit {abbreviation}: {repository}")
    return revisions.pop().decode("ascii")


def remote_content_length(url: str) -> int:
    with urllib.request.urlopen(public_request(url), timeout=60) as response:
        length = response.headers.get("Content-Length")
    if not isinstance(length, str) or not length.isdigit() or int(length) <= 0:
        raise ValueError(f"o upstream nao informou o tamanho do artefato: {url}")
    return int(length)
