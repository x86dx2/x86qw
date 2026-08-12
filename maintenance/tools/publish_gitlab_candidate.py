#!/usr/bin/env python3
"""Publish the candidate installer to the GitLab generic mirror once."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from x86qw_runtime.io.downloader import DownloadError
    from .publish_gitlab_packages import artifact_url, remote_sha256, upload
    from .release_candidate import CandidateError, verify_candidate
    from .validate_catalog import validate_catalog
except ImportError:  # Execucao direta
    from x86qw_runtime.io.downloader import DownloadError
    from publish_gitlab_packages import artifact_url, remote_sha256, upload
    from release_candidate import CandidateError, verify_candidate
    from validate_catalog import validate_catalog


class GitLabPublisherError(RuntimeError):
    """The GitLab mirror cannot be proven identical to the candidate."""


def _catalog_record(candidate: Path, version: str) -> dict[str, Any]:
    path = candidate / "catalog.json"
    if path.is_symlink() or not path.is_file():
        raise GitLabPublisherError("catálogo do candidato ausente")
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
        validate_catalog(catalog)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise GitLabPublisherError("catálogo do candidato inválido") from error
    packages = catalog.get("packages")
    assert isinstance(packages, list)
    records = [
        package for package in packages
        if isinstance(package, dict)
        and package.get("component") == "installer"
        and package.get("package") == "x86qw-installer"
        and package.get("version") == version
    ]
    if len(records) != 1:
        raise GitLabPublisherError(f"instalador do candidato não é único: {version}")
    return records[0]


def publish_candidate(
    *,
    candidate: Path,
    publish: bool = False,
) -> dict[str, object]:
    candidate = Path(candidate)
    try:
        manifest = verify_candidate(candidate)
    except CandidateError as error:
        raise GitLabPublisherError(f"candidato inválido: {error}") from error
    version = manifest.get("version")
    if not isinstance(version, str):
        raise GitLabPublisherError("versão do candidato ausente")
    record = _catalog_record(candidate, version)
    filename = record.get("filename")
    if not isinstance(filename, str) or filename != f"x86qw-installer-{version}.zip":
        raise GitLabPublisherError("nome do instalador diverge da versão")
    path = candidate / "installer" / filename
    if path.is_symlink() or not path.is_file():
        raise GitLabPublisherError(f"instalador do candidato ausente: {path}")
    expected = (record.get("size"), record.get("sha256"))
    if type(expected[0]) is not int or expected[0] <= 0 or not isinstance(expected[1], str):
        raise GitLabPublisherError("pin do instalador inválido")
    actual_size = path.stat().st_size
    digest_builder = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest_builder.update(chunk)
    digest = digest_builder.hexdigest()
    if (actual_size, digest) != expected:
        raise GitLabPublisherError("instalador local diverge do catálogo")
    url = artifact_url(record)
    urls = record.get("urls")
    if not isinstance(urls, list) or url not in urls:
        raise GitLabPublisherError("URL GitLab calculada não está declarada no catálogo")
    if not publish:
        return {
            "format": 1,
            "project": "x86qw",
            "status": "planned",
            "filename": filename,
            "url": url,
            "size": actual_size,
            "sha256": digest,
        }

    remote = remote_sha256(url, int(expected[0]), str(expected[1]))
    if remote is None:
        upload(path, record)
        remote = remote_sha256(url, int(expected[0]), str(expected[1]))
    if remote != (int(expected[0]), str(expected[1])):
        raise GitLabPublisherError("mirror GitLab não converge para os bytes do candidato")
    return {
        "format": 1,
        "project": "x86qw",
        "status": "published",
        "filename": filename,
        "url": url,
        "size": int(expected[0]),
        "sha256": str(expected[1]),
    }


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--publish", action="store_true")
    options = parser.parse_args(arguments)
    try:
        result = publish_candidate(candidate=options.candidate, publish=options.publish)
    except (DownloadError, OSError, GitLabPublisherError, ValueError) as error:
        print(f"[ERRO] {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
