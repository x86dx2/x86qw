#!/usr/bin/env python3
"""Publish and verify catalog artifacts in the x86QW GitLab generic registry."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

try:
    from .downloader import DownloadError, DownloadHTTPError, MAX_ARTIFACT_BYTES, PinnedArtifact, download
    from .validate_catalog import DEFAULT_CATALOG, validate_catalog
except ImportError:  # Execucao direta
    from downloader import DownloadError, DownloadHTTPError, MAX_ARTIFACT_BYTES, PinnedArtifact, download
    from validate_catalog import DEFAULT_CATALOG, validate_catalog


ROOT = Path(__file__).resolve().parents[2]
PROJECT_ID = 84813414
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


def local_artifact(package: dict[str, object], dist: Path, builds: Path) -> Path:
    filename = str(package["filename"])
    relative = package.get("distribution_path")
    if isinstance(relative, str):
        path = dist / relative
    elif package.get("channel") == "content":
        candidates = [path for path in builds.rglob(filename) if path.is_file() and not path.is_symlink()]
        if len(candidates) != 1:
            raise ValueError(f"expected one temporary build artifact for {filename}, found {len(candidates)}")
        path = candidates[0]
    else:
        raise ValueError(f"catalog package has no local distribution artifact: {filename}")
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"distribution artifact is missing: {path}")
    if path.stat().st_size != package["size"]:
        raise ValueError(f"local artifact size differs from catalog: {path}")
    digest = file_sha256(path)
    if digest != package["sha256"]:
        raise ValueError(f"local artifact SHA-256 differs from catalog: {path}")
    return path


def remote_sha256(url: str, expected_size: int, expected_sha256: str) -> tuple[int, str] | None:
    with tempfile.TemporaryDirectory(prefix="x86qw-mirror-verify-") as temporary:
        destination = Path(temporary) / "artifact"
        try:
            result = download(PinnedArtifact(
                url=url,
                destination=destination,
                expected_size=expected_size,
                expected_sha256=expected_sha256,
                maximum_size=MAX_ARTIFACT_BYTES,
                deadline_seconds=120,
                headers={"User-Agent": USER_AGENT},
                label="artefato do mirror GitLab",
            ))
        except DownloadHTTPError as error:
            if error.status == 404:
                return None
            raise
    return result.size, result.sha256


def upload(path: Path, package: dict[str, object]) -> None:
    token = os.environ.get("GLAB_TOKEN") or os.environ.get("GITLAB_TOKEN")
    if not token:
        raise ValueError("GitLab publication token is missing")
    if any(character in token for character in "\r\n\x00"):
        raise ValueError("GitLab publication token contains an invalid control character")
    # GitLab's documented generic-package upload uses PUT with --upload-file.
    # Feed the private header over stdin so the token never enters argv or logs.
    result = subprocess.run([
        "curl", "--disable", "--fail", "--silent", "--show-error",
        "--proto", "=https", "--proto-redir", "=https",
        "--connect-timeout", "15", "--max-time", "900",
        "--max-redirs", "0", "--output", os.devnull,
        "--write-out", "%{http_code}",
        "--request", "PUT", "--header", "@-", "--upload-file", str(path),
        artifact_url(package),
    ], input=f"PRIVATE-TOKEN: {token}\n", text=True, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, check=False)
    if result.returncode:
        raise ValueError(f"GitLab upload failed with curl exit code {result.returncode}")
    status = result.stdout.strip()
    if len(status) != 3 or not status.isascii() or not status.isdecimal() or not status.startswith("2"):
        raise ValueError(f"GitLab upload failed with HTTP status {status or 'unknown'}")


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
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--builds", type=Path, default=ROOT / "maintenance/build/packages")
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
    catalog_changed = False
    for index, package in enumerate(packages, 1):
        assert isinstance(package, dict)
        path = local_artifact(package, arguments.dist, arguments.builds)
        url = artifact_url(package)
        remote = remote_sha256(url, int(package["size"]), str(package["sha256"]))
        if remote is None:
            if not arguments.publish:
                raise ValueError(f"GitLab mirror is missing: {package['filename']}")
            print(f"[{index}/{len(packages)}] enviando {package['filename']}...", flush=True)
            upload(path, package)
            remote = remote_sha256(url, int(package["size"]), str(package["sha256"]))
        if remote != (package["size"], package["sha256"]):
            raise ValueError(f"GitLab mirror differs from catalog: {package['filename']}")
        if url not in package["urls"]:
            package["urls"].append(url)
            catalog_changed = True
        verified += 1
        print(f"[{index}/{len(packages)}] verificado {package['filename']}", flush=True)
    if arguments.register:
        if catalog_changed:
            catalog["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            write_catalog(arguments.catalog, catalog)
            print(f"catálogo atualizado com {verified} mirrors GitLab")
        else:
            print(f"catálogo já continha os {verified} mirrors GitLab")
    else:
        print(f"{verified} mirrors GitLab verificados; catálogo não alterado")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        DownloadError,
        OSError,
        ValueError,
        subprocess.CalledProcessError,
        json.JSONDecodeError,
    ) as error:
        print(f"GitLab mirror failed: {error}")
        raise SystemExit(1)
