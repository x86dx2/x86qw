#!/usr/bin/env python3
"""Download the current upstream x86QW archive with resumable SHA-256 inventory."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARCHIVE = ROOT / "archive"
USER_AGENT = "x86qw-archive/1"
REPOSITORIES = {
    "ezquake-source": (
        "https://github.com/QW-Group/ezquake-source.git",
        "components/ezquake/git/repository.git",
    ),
    "nquake-distfiles": (
        "https://github.com/nQuake/distfiles.git",
        "components/nquake/git/distfiles.git",
    ),
    "classicq": (
        "https://github.com/classicq/classicq.git",
        "components/classicq/git/repository.git",
    ),
    "unezquake": (
        "https://github.com/dusty-qw/unezquake.git",
        "components/unezquake/git/repository.git",
    ),
    "qwprot": (
        "https://github.com/QW-Group/qwprot.git",
        "dependencies/qw-group-qwprot/repository.git",
    ),
    "qwprot-community": (
        "https://github.com/QW-Community/qwprot.git",
        "dependencies/qw-community-qwprot/repository.git",
    ),
}
SUBMODULES = {
    "ezquake-source": {
        "src/qwprot": "QW-Group/qwprot",
        "vcpkg": "Microsoft/vcpkg",
    },
    "unezquake": {
        "src/qwprot": "QW-Community/qwprot",
        "vcpkg": "Microsoft/vcpkg",
    },
}
RELEASES = {
    "ezquake": "QW-Group/ezquake-source",
    "classicq": "classicq/classicq",
    "unezquake": "dusty-qw/unezquake",
}
NIGHTLIES = {
    "macos-universal": (
        "https://builds.quakeworld.nu/ezquake/snapshots/macOS/universal/",
        re.compile(r"^[0-9]{8}-[0-9]{6}_[0-9a-f]{7}_ezQuake-macOS-universal\.zip$"),
    ),
    "linux-x86_64": (
        "https://builds.quakeworld.nu/ezquake/snapshots/linux/x86_64/",
        re.compile(r"^[0-9]{8}-[0-9]{6}_[0-9a-f]{7}_ezQuake-x86_64\.AppImage$"),
    ),
    "windows-x64": (
        "https://builds.quakeworld.nu/ezquake/snapshots/windows/x64/",
        re.compile(r"^[0-9]{8}-[0-9]{6}_[0-9a-f]{7}_ezquake\.exe$"),
    ),
}


@dataclass(frozen=True)
class Asset:
    url: str
    path: str
    expected_size: int | None = None
    optional: bool = False


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def request_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read()


def links(url: str) -> list[str]:
    parser = LinkParser()
    parser.feed(request_bytes(url).decode("utf-8", "replace"))
    return parser.links


def safe_filename(href: str) -> str | None:
    path = urllib.parse.unquote(urllib.parse.urlsplit(href).path)
    name = PurePosixPath(path).name
    if not name or name in {".", ".."} or "/" in name or "\\" in name or "\0" in name:
        return None
    return name


def github_json(path: str) -> object:
    url = f"https://api.github.com/{path}"
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.load(response)


def release_variant(name: str) -> str:
    lowered = name.casefold()
    for variant in (
        "macos-universal", "macos-arm64", "linux-x86_64", "linux-amd64",
        "windows-x64", "windows-amd64",
    ):
        if variant in lowered:
            return variant
    return "source" if "source" in lowered else "metadata"


def release_path(component: str, tag: str, name: str) -> str:
    return f"components/{component}/releases/{tag}/{release_variant(name)}/{name}"


def nightly_path(platform: str, name: str) -> str:
    build = name.split("_ez", 1)[0]
    return f"components/ezquake/nightlies/{build}/{platform}/{name}"


def discover_release_assets() -> list[Asset]:
    assets: list[Asset] = []
    for label, repository in RELEASES.items():
        release = github_json(f"repos/{repository}/releases/latest")
        if not isinstance(release, dict) or not isinstance(release.get("tag_name"), str):
            raise ValueError(f"invalid latest release response for {repository}")
        tag = release["tag_name"]
        for item in release.get("assets", []):
            if not isinstance(item, dict):
                continue
            name = safe_filename(str(item.get("name", "")))
            url = item.get("browser_download_url")
            size = item.get("size")
            if name and isinstance(url, str) and isinstance(size, int) and size > 0:
                assets.append(Asset(url, release_path(label, tag, name), size))
    return assets


def discover_nightlies() -> list[Asset]:
    assets: list[Asset] = []
    for platform, (root, pattern) in NIGHTLIES.items():
        names = sorted({name for href in links(root) if (name := safe_filename(href)) and pattern.fullmatch(name)})
        if not names:
            raise ValueError(f"no nightly found for {platform}")
        name = names[-1]
        assets.extend((
            Asset(urllib.parse.urljoin(root, name), nightly_path(platform, name)),
            Asset(urllib.parse.urljoin(root, name + ".md5"), nightly_path(platform, name + ".md5")),
        ))
    return assets


def discover_maps() -> list[Asset]:
    assets = [
        Asset(f"https://maps.quakeworld.nu/{collection}/", f"content/maps/indexes/{collection}.html")
        for collection in ("all", "base", "core", "locs")
    ]
    for collection in ("all", "locs"):
        root = f"https://maps.quakeworld.nu/{collection}/"
        for href in links(root):
            name = safe_filename(href)
            if name and not urllib.parse.urlsplit(href).path.endswith("/"):
                destination = f"content/locs/{name}" if collection == "locs" else f"content/maps/all/{name}"
                assets.append(Asset(urllib.parse.urljoin(root, href), destination))
    return assets


def discover_gfx(workers: int) -> list[Asset]:
    first = links("https://gfx.quakeworld.nu/new/")
    visible_pages = [1, *(int(match.group(1)) for href in first if (match := re.fullmatch(r"/new/page/([0-9]+)/", href)))]
    page_numbers = list(range(1, max(visible_pages) + 1))
    page_urls = ["https://gfx.quakeworld.nu/new/" if page == 1 else f"https://gfx.quakeworld.nu/new/page/{page}/" for page in page_numbers]
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(workers, 8)) as pool:
        pages = list(pool.map(links, page_urls))

    details: dict[int, str] = {}
    pattern = re.compile(r"/details/([0-9]+)/([a-z0-9-]+)/")
    for page in pages:
        for href in page:
            if match := pattern.fullmatch(href):
                details[int(match.group(1))] = match.group(2)

    assets: list[Asset] = []
    for identifier, slug in sorted(details.items()):
        assets.extend((
            Asset(f"https://gfx.quakeworld.nu/details/{identifier}/{slug}/", f"content/gfx/details/{identifier}-{slug}.html"),
            Asset(f"https://gfx.quakeworld.nu/download/{identifier}/{slug}/", f"content/gfx/packages/{identifier}-{slug}.download"),
            Asset(f"https://gfx.quakeworld.nu/files/{identifier}.jpg", f"content/gfx/previews/{identifier}.jpg", optional=True),
        ))
    return assets


def discover_submodules(root: Path) -> list[Asset]:
    assets: list[Asset] = []
    for parent, submodules in SUBMODULES.items():
        repository = root / REPOSITORIES[parent][1]
        for submodule_path, upstream in submodules.items():
            output = subprocess.check_output(
                ["git", "-C", str(repository), "ls-tree", "HEAD", submodule_path], text=True
            ).strip()
            match = re.fullmatch(r"160000 commit ([0-9a-f]{40})\t.+", output)
            if not match:
                raise ValueError(f"invalid submodule reference in {parent}: {submodule_path}")
            commit = match.group(1)
            label = upstream.lower().replace("/", "-")
            assets.append(Asset(
                f"https://codeload.github.com/{upstream}/tar.gz/{commit}",
                f"dependencies/{label}/snapshots/{commit}.tar.gz",
            ))
    return assets


def discover_assets(root: Path, workers: int) -> list[Asset]:
    discovered = [
        *discover_release_assets(), *discover_nightlies(), *discover_maps(),
        *discover_gfx(workers), *discover_submodules(root),
    ]
    unique: dict[str, Asset] = {}
    for asset in discovered:
        if asset.path in unique and unique[asset.path].url != asset.url:
            raise ValueError(f"two URLs resolve to the same archive path: {asset.path}")
        unique[asset.path] = asset
    return sorted(unique.values(), key=lambda asset: asset.path)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_manifest(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"format": 1, "project": "x86qw", "captured_at": None, "files": {}, "repositories": {}}
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("format") != 1 or manifest.get("project") != "x86qw":
        raise ValueError(f"invalid archive manifest: {path}")
    if not isinstance(manifest.get("files"), dict) or not isinstance(manifest.get("repositories"), dict):
        raise ValueError(f"invalid archive manifest collections: {path}")
    return manifest


def write_manifest(path: Path, manifest: dict[str, object]) -> None:
    manifest["captured_at"] = utc_now()
    encoded = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".manifest-", suffix=".json", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(encoded)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def component_first_path(relative: str) -> str:
    parts = relative.split("/")
    if parts[0] == "releases" and len(parts) == 4:
        return release_path(parts[1], parts[2], parts[3])
    if parts[0] == "nightly" and len(parts) == 3:
        return nightly_path(parts[1], parts[2])
    if relative.startswith("maps/all/"):
        return "content/maps/all/" + relative.removeprefix("maps/all/")
    if relative.startswith("maps/indexes/"):
        return "content/maps/indexes/" + relative.removeprefix("maps/indexes/")
    if relative.startswith("maps/locs/"):
        return "content/locs/" + relative.removeprefix("maps/locs/")
    if relative.startswith("gfx/"):
        destination = "content/gfx/" + relative.removeprefix("gfx/")
        if destination.startswith("content/gfx/packages/") and destination.endswith(".zip"):
            destination = destination.removesuffix(".zip") + ".download"
        return destination
    if relative.startswith("source-dependencies/"):
        _, dependency, name = parts
        return f"dependencies/{dependency}/snapshots/{name}"
    return relative


def migrate_archive_layout(root: Path, manifest: dict[str, object]) -> tuple[int, int]:
    files = manifest["files"]
    repositories = manifest["repositories"]
    assert isinstance(files, dict) and isinstance(repositories, dict)
    migrated_files: dict[str, object] = {}
    moved_files = 0
    for old_relative, metadata in sorted(files.items()):
        new_relative = component_first_path(old_relative)
        if new_relative in migrated_files:
            raise ValueError(f"two archive entries resolve to the same path: {new_relative}")
        if new_relative != old_relative and not (isinstance(metadata, dict) and metadata.get("missing") is True):
            old_path = root / old_relative
            new_path = root / new_relative
            if old_path.exists() and new_path.exists():
                raise ValueError(f"cannot migrate conflicting archive path: {new_relative}")
            if old_path.exists():
                new_path.parent.mkdir(parents=True, exist_ok=True)
                old_path.replace(new_path)
                moved_files += 1
            elif not new_path.exists():
                raise ValueError(f"archive file is missing during migration: {old_relative}")
        migrated_files[new_relative] = metadata
    manifest["files"] = migrated_files

    moved_repositories = 0
    for name, (_, new_relative) in REPOSITORIES.items():
        old_path = root / "git" / f"{name}.git"
        new_path = root / new_relative
        if old_path.exists() and new_path.exists():
            raise ValueError(f"cannot migrate conflicting Git mirror: {new_relative}")
        if old_path.exists():
            new_path.parent.mkdir(parents=True, exist_ok=True)
            old_path.replace(new_path)
            moved_repositories += 1
        metadata = repositories.get(name)
        if isinstance(metadata, dict):
            metadata["path"] = new_relative

    manifest["layout"] = "component-first-v1"
    for legacy_name in ("releases", "nightly", "maps", "gfx", "source-dependencies", "git"):
        legacy_root = root / legacy_name
        if legacy_root.is_dir():
            for directory in sorted((path for path in legacy_root.rglob("*") if path.is_dir()), reverse=True):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            try:
                legacy_root.rmdir()
            except OSError:
                pass
    return moved_files, moved_repositories


def download_asset(root: Path, asset: Asset, known: object) -> tuple[str, dict[str, object], bool]:
    target = root / asset.path
    if target.is_symlink():
        raise ValueError(f"archive target must not be a symlink: {target}")
    if isinstance(known, dict) and target.is_file() and target.stat().st_size == known.get("size"):
        return asset.path, known, True
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".part")
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            request = urllib.request.Request(asset.url, headers={"User-Agent": USER_AGENT})
            digest = hashlib.sha256()
            size = 0
            with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as output:
                content_type = response.headers.get_content_type()
                disposition = response.headers.get("Content-Disposition")
                while block := response.read(1024 * 1024):
                    size += len(block)
                    if asset.expected_size is not None and size > asset.expected_size:
                        raise ValueError(f"download exceeds declared size: {asset.url}")
                    digest.update(block)
                    output.write(block)
            if asset.expected_size is not None and size != asset.expected_size:
                raise ValueError(f"download size mismatch for {asset.url}: expected {asset.expected_size}, got {size}")
            os.replace(temporary, target)
            return asset.path, {
                "url": asset.url, "size": size, "sha256": digest.hexdigest(),
                "content_type": content_type, "content_disposition": disposition,
            }, False
        except urllib.error.HTTPError as error:
            if asset.optional and error.code == 404:
                if temporary.exists():
                    temporary.unlink()
                return asset.path, {"url": asset.url, "missing": True}, False
            last_error = error
        except (OSError, ValueError, urllib.error.URLError) as error:
            last_error = error
        if temporary.exists():
            temporary.unlink()
        if attempt < 2:
            time.sleep(1 + attempt)
    assert last_error is not None
    raise last_error


def mirror_repositories(root: Path, manifest: dict[str, object]) -> None:
    repositories = manifest["repositories"]
    assert isinstance(repositories, dict)
    for name, (url, relative) in REPOSITORIES.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            subprocess.run(["git", "-C", str(target), "remote", "update", "--prune"], check=True)
        else:
            subprocess.run(["git", "clone", "--mirror", url, str(target)], check=True)
        subprocess.run(["git", "-C", str(target), "fsck", "--full", "--no-dangling"], check=True)
        head = subprocess.check_output(["git", "-C", str(target), "rev-parse", "HEAD"], text=True).strip()
        refs = subprocess.check_output(["git", "-C", str(target), "show-ref"], text=True).splitlines()
        repositories[name] = {"url": url, "path": relative, "head": head, "refs": len(refs)}


def verify_archive(root: Path, manifest: dict[str, object]) -> int:
    files = manifest["files"]
    assert isinstance(files, dict)
    checked = 0
    for relative, metadata in sorted(files.items()):
        if not isinstance(metadata, dict) or metadata.get("missing") is True:
            continue
        path = root / relative
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"archive file is missing or unsafe: {relative}")
        if path.stat().st_size != metadata.get("size") or file_sha256(path) != metadata.get("sha256"):
            raise ValueError(f"archive file failed integrity verification: {relative}")
        checked += 1
    return checked


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Baixa e inventaria o acervo upstream atual do x86QW.")
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--workers", type=int, default=8)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--verify", action="store_true", help="valida o acervo existente sem acessar a rede")
    mode.add_argument("--migrate-layout", action="store_true", help="migra o acervo antigo sem acessar a rede")
    return parser.parse_args()


def main() -> int:
    options = parse_arguments()
    if not 1 <= options.workers <= 32:
        raise ValueError("workers must be between 1 and 32")
    root = options.archive.resolve()
    manifest_path = root / "manifest.json"
    manifest = load_manifest(manifest_path)
    if options.verify:
        count = verify_archive(root, manifest)
        print(f"archive valid: {count} file(s)")
        return 0

    moved_files, moved_repositories = migrate_archive_layout(root, manifest)
    if moved_files or moved_repositories:
        write_manifest(manifest_path, manifest)
        print(f"Migrated archive layout: {moved_files} file(s), {moved_repositories} Git mirror(s).")
    if options.migrate_layout:
        checked = verify_archive(root, manifest)
        print(f"archive layout ready: {checked} verified file(s)")
        return 0

    print("Mirroring Git repositories...")
    mirror_repositories(root, manifest)
    write_manifest(manifest_path, manifest)
    print("Discovering current upstream assets...")
    assets = discover_assets(root, options.workers)
    files = manifest["files"]
    assert isinstance(files, dict)
    print(f"Downloading {len(assets)} asset(s) with {options.workers} worker(s)...")
    failures: list[str] = []
    completed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=options.workers) as pool:
        futures = {pool.submit(download_asset, root, asset, files.get(asset.path)): asset for asset in assets}
        for future in concurrent.futures.as_completed(futures):
            asset = futures[future]
            try:
                relative, metadata, skipped = future.result()
                files[relative] = metadata
                completed += 1
                action = "cached" if skipped else "saved"
                print(f"[{completed}/{len(assets)}] {action}: {relative}", flush=True)
                if completed % 25 == 0:
                    write_manifest(manifest_path, manifest)
            except Exception as error:
                failures.append(f"{asset.url}: {error}")
                print(f"[error] {asset.url}: {error}", file=sys.stderr, flush=True)
    write_manifest(manifest_path, manifest)
    if failures:
        raise ValueError(f"archive incomplete: {len(failures)} download(s) failed")
    checked = verify_archive(root, manifest)
    print(f"archive complete: {checked} verified file(s)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        print(f"archive failed: {error}", file=sys.stderr)
        raise SystemExit(1)
