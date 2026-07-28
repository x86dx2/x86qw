#!/usr/bin/env python3
"""Publish and verify catalog artifacts in the x86QW GitLab generic registry."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from validate_catalog import DEFAULT_CATALOG, validate_catalog


ROOT = Path(__file__).resolve().parents[1]
PROJECT_ID = 84856335
API_ROOT = f"https://gitlab.com/api/v4/projects/{PROJECT_ID}/packages/generic"
USER_AGENT = "x86qw-gitlab-mirror/1"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def artifact_url(package: dict[str, object]) -> str:
    package_name = str(package.get("package", package["component"]))
    quoted = [
        urllib.parse.quote(value, safe="+._-")
        for value in (package_name, str(package["version"]), str(package["filename"]))
    ]
    return f"{API_ROOT}/{'/'.join(quoted)}"


def local_artifact(package: dict[str, object], archive: Path, dist: Path) -> Path:
    filename = str(package["filename"])
    if package.get("channel") == "content" and isinstance(package.get("package"), str):
        matches = list(dist.rglob(filename))
    else:
        matches = list(archive.rglob(filename))
    if len(matches) != 1 or not matches[0].is_file():
        raise ValueError(f"expected exactly one local artifact for {filename}, found {len(matches)}")
    path = matches[0]
    if path.stat().st_size != package["size"]:
        raise ValueError(f"local artifact size differs from catalog: {path}")
    digest = file_sha256(path)
    if digest != package["sha256"]:
        raise ValueError(f"local artifact SHA-256 differs from catalog: {path}")
    return path


def remote_sha256(url: str) -> tuple[int, str] | None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            digest = hashlib.sha256()
            size = 0
            while block := response.read(1024 * 1024):
                size += len(block)
                digest.update(block)
            return size, digest.hexdigest()
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def upload(path: Path, package: dict[str, object]) -> None:
    package_name = str(package.get("package", package["component"]))
    endpoint = "/".join(
        urllib.parse.quote(value, safe="+._-")
        for value in (package_name, str(package["version"]), str(package["filename"]))
    )
    subprocess.run([
        "glab", "api", "--silent", "--method", "PUT",
        "--header", "Content-Type: application/octet-stream",
        "--input", str(path),
        f"projects/{PROJECT_ID}/packages/generic/{endpoint}",
    ], check=True)


def write_catalog(path: Path, catalog: dict[str, object]) -> None:
    validate_catalog(catalog)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".catalog-", suffix=".json", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(catalog, output, ensure_ascii=False, indent=2)
            output.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--archive", type=Path, default=ROOT / "archive")
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--publish", action="store_true", help="envia artefatos ausentes usando a autenticação do glab")
    parser.add_argument("--register", action="store_true", help="adiciona URLs GitLab ao catálogo após verificar tudo")
    arguments = parser.parse_args()
    if arguments.register and not arguments.publish:
        parser.error("--register exige --publish")
    catalog = json.loads(arguments.catalog.read_text(encoding="utf-8"))
    validate_catalog(catalog)
    packages = catalog["packages"]
    assert isinstance(packages, list)
    verified = 0
    for index, package in enumerate(packages, 1):
        assert isinstance(package, dict)
        path = local_artifact(package, arguments.archive, arguments.dist)
        url = artifact_url(package)
        remote = remote_sha256(url)
        if remote is None:
            if not arguments.publish:
                raise ValueError(f"GitLab mirror is missing: {package['filename']}")
            print(f"[{index}/{len(packages)}] enviando {package['filename']}...", flush=True)
            upload(path, package)
            remote = remote_sha256(url)
        if remote != (package["size"], package["sha256"]):
            raise ValueError(f"GitLab mirror differs from catalog: {package['filename']}")
        if url not in package["urls"]:
            package["urls"].append(url)
        verified += 1
        print(f"[{index}/{len(packages)}] verificado {package['filename']}", flush=True)
    if arguments.register:
        catalog["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        write_catalog(arguments.catalog, catalog)
        print(f"catálogo atualizado com {verified} mirrors GitLab")
    else:
        print(f"{verified} mirrors GitLab verificados; catálogo não alterado")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        print(f"GitLab mirror failed: {error}")
        raise SystemExit(1)
