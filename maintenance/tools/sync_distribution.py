"""Primitivas de descoberta e sincronizacao usadas pelo gerenciador x86QW."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath

try:
    from .component_policy import component_for_distribution_path, load_component_policy, require_component
    from .components import component_for_source, load_catalog as load_component_catalog, source_roots
    from .component_releases import component_for_artifact_path, load_releases
    from .public_upstreams import git_remote_tree, github_latest_release, remote_content_length
    from .upstreams import load_upstreams, source_owner
except ImportError:  # Execucao direta
    from component_policy import component_for_distribution_path, load_component_policy, require_component
    from components import component_for_source, load_catalog as load_component_catalog, source_roots
    from component_releases import component_for_artifact_path, load_releases
    from public_upstreams import git_remote_tree, github_latest_release, remote_content_length
    from upstreams import load_upstreams, source_owner


ROOT = Path(__file__).resolve().parents[2]
COMPONENT_CATALOG = ROOT / "maintenance/inventory/components.json"
COMPONENT_RELEASES = ROOT / "maintenance/inventory/component-releases.json"
UPSTREAMS = ROOT / "maintenance/inventory/upstreams.json"
PUBLIC_CATALOG = ROOT / "site/public/api/v1/catalog.json"
USER_AGENT = "x86qw-maintenance/1"
NQUAKE_REPOSITORY = "nQuake/distfiles"
NQUAKE_REF = "master"

RELEASES = {
    "ezquake": (
        "QW-Group/ezquake-source",
        re.compile(r"ezQuake-(?:macOS-universal|linux-x86_64|windows-x64)\.zip"),
    ),
}
RELEASE_ASSETS = {
    "ezquake": (
        "ezQuake-macOS-universal.zip",
        "ezQuake-linux-x86_64.zip",
        "ezQuake-windows-x64.zip",
    ),
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
OBSOLETE_ROOTS = (
    "content/gfx",
    "content/maps/indexes",
    "content/maps",
    "content/locs",
    "components/ezquake/git",
    "components/ezquake/dependencies",
    "components/classicq/git",
    "components/unezquake/git",
    "components/unezquake/dependencies",
    "components/nquake/git",
)


@dataclass(frozen=True)
class Asset:
    component: str
    url: str
    path: str
    expected_size: int | None = None
    subcomponent: str | None = None
    expected_sha256: str | None = None
    expected_git_sha1: str | None = None


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


def release_variant(name: str) -> str:
    lowered = name.casefold()
    for variant in (
        "macos-universal", "macos-arm64", "linux-x86_64", "linux-amd64",
        "windows-x64", "windows-amd64",
    ):
        if variant in lowered:
            return variant
    raise ValueError(f"release asset has no supported runtime variant: {name}")


def release_path(component: str, tag: str, name: str) -> str:
    return f"clients/{component}/stable/{tag}/{release_variant(name)}/{name}"


def nightly_path(platform: str, name: str) -> str:
    build = name.split("_ez", 1)[0]
    return f"clients/ezquake/nightly/{build}/{platform}/{name}"


def discover_release_assets() -> list[Asset]:
    assets: list[Asset] = []
    for component, (repository, accepted) in RELEASES.items():
        tag = github_latest_release(repository)
        names = RELEASE_ASSETS.get(component, ())
        if len(names) != 3 or not all(accepted.fullmatch(name) for name in names):
            raise ValueError(f"a lista de artefatos stable esta incompleta para {component}")
        for name in names:
            url = f"https://github.com/{repository}/releases/download/{urllib.parse.quote(tag, safe='')}/{name}"
            assets.append(Asset(component, url, release_path(component, tag, name), remote_content_length(url)))
    return assets


def discover_nightlies() -> list[Asset]:
    assets: list[Asset] = []
    for platform, (root, pattern) in NIGHTLIES.items():
        names = sorted({name for href in links(root) if (name := safe_filename(href)) and pattern.fullmatch(name)})
        if not names:
            raise ValueError(f"no nightly found for {platform}")
        name = names[-1]
        assets.append(Asset("ezquake", urllib.parse.urljoin(root, name), nightly_path(platform, name)))
    return assets


def discover_nquake() -> list[Asset]:
    catalog = load_component_catalog(COMPONENT_CATALOG)
    used_paths = source_roots(catalog, "reference")

    commit, tree = git_remote_tree(f"https://github.com/{NQUAKE_REPOSITORY}.git", NQUAKE_REF)

    assets: list[Asset] = []
    found_roots: set[str] = set()
    for item in tree:
        path = item.path
        blob_sha = item.sha1
        roots = [root for root in used_paths if path == root or path.startswith(root + "/")]
        if not roots:
            continue
        found_roots.update(roots)
        subcomponent = component_for_source(catalog, path, "reference")
        if subcomponent is None:
            continue
        quoted = urllib.parse.quote(path, safe="/")
        assets.append(Asset(
            "nquake",
            f"https://raw.githubusercontent.com/{NQUAKE_REPOSITORY}/{commit}/{quoted}",
            f"distributions/nquake/{commit}/{path}",
            None,
            subcomponent,
            None,
            blob_sha,
        ))
    missing = sorted(set(used_paths) - found_roots)
    if missing:
        raise ValueError(f"nQuake used path is missing at {commit}: {missing[0]}")
    return assets


def discover_component_release_assets() -> list[Asset]:
    releases = load_releases(COMPONENT_RELEASES, COMPONENT_CATALOG)
    policy = load_component_policy()
    assets: list[Asset] = []
    components = releases["components"]
    assert isinstance(components, dict)
    for identifier, release in components.items():
        assert isinstance(release, dict)
        for artifact in release.get("artifacts", []):
            assert isinstance(artifact, dict)
            distribution_component = component_for_distribution_path(policy, str(artifact["distribution_path"]))
            if distribution_component is None:
                raise ValueError(f"release artifact has no distribution owner: {artifact['distribution_path']}")
            assets.append(Asset(
                distribution_component, str(artifact["url"]), str(artifact["distribution_path"]),
                int(artifact["size"]), identifier, str(artifact["sha256"]),
            ))
    return assets


def discover_preserved_sources() -> list[Asset]:
    registry = load_upstreams(UPSTREAMS)
    policy = load_component_policy()
    assets: list[Asset] = []
    entries = registry["upstreams"]
    assert isinstance(entries, list)
    for entry in entries:
        assert isinstance(entry, dict)
        source = entry["source"]
        assert isinstance(source, dict)
        if source.get("status") not in {"complete", "partial"} or "size" not in source:
            continue
        path = str(source["distribution_path"])
        component = component_for_distribution_path(policy, path)
        if component is None:
            raise ValueError(f"preserved source has no distribution owner: {path}")
        assets.append(Asset(
            component, str(source["url"]), path, int(source["size"]),
            str(entry["id"]), str(source["sha256"]),
        ))
    return assets


def discover_installer_assets() -> list[Asset]:
    catalog = json.loads(PUBLIC_CATALOG.read_text(encoding="utf-8"))
    packages = catalog.get("packages")
    if not isinstance(packages, list):
        raise ValueError("public catalog has no packages")
    assets: list[Asset] = []
    for package in packages:
        if not isinstance(package, dict) or package.get("component") != "installer":
            continue
        path = package.get("distribution_path")
        if not isinstance(path, str):
            raise ValueError("installer package has no distribution path")
        assets.append(Asset(
            "installer", str(package["origin_url"]), path, int(package["size"]),
            str(package.get("package", "installer")), str(package["sha256"]),
        ))
    if not assets:
        raise ValueError("public catalog has no installer package")
    return assets


def discover_assets() -> list[Asset]:
    components = load_component_policy()
    discovered = [
        *discover_release_assets(), *discover_nightlies(), *discover_nquake(),
        *discover_component_release_assets(), *discover_preserved_sources(),
        *discover_installer_assets(),
    ]
    unique: dict[str, Asset] = {}
    for asset in discovered:
        require_component(components, asset.component, asset.path)
        key = asset.path.casefold()
        existing = unique.get(key)
        if existing is not None and existing.path == asset.path and existing.url != asset.url:
            raise ValueError(f"two URLs resolve to the same distribution path: {asset.path}")
        if existing is None or asset.path < existing.path:
            unique[key] = asset
    return sorted(unique.values(), key=lambda asset: asset.path)


def consumed_component(
    path: str,
    *,
    component_catalog: Path = COMPONENT_CATALOG,
    component_releases: Path = COMPONENT_RELEASES,
    policy_path: Path | None = None,
) -> str | None:
    components = load_component_policy(policy_path) if policy_path is not None else load_component_policy()
    component = component_for_distribution_path(components, path)
    upstream = source_owner(load_upstreams(UPSTREAMS), path)
    if component == "nquake":
        parts = PurePosixPath(path).parts
        if (
            len(parts) < 4
            or parts[:2] != ("distributions", "nquake")
            or len(parts[2]) != 40
        ):
            return None
        upstream_path = PurePosixPath(*parts[3:]).as_posix()
        catalog = load_component_catalog(component_catalog)
        return component if component_for_source(catalog, upstream_path, "reference") is not None else None
    if upstream is not None:
        return component
    releases = load_releases(component_releases, component_catalog)
    if component == "installer":
        return component if re.fullmatch(
            r"installer/packages/[0-9]+\.[0-9]+\.[0-9]+/x86qw-installer-[0-9]+\.[0-9]+\.[0-9]+\.zip",
            path,
        ) else None
    artifact_component = component_for_artifact_path(releases, path)
    if artifact_component is not None:
        return component if artifact_component == component else None
    name = PurePosixPath(path).name
    if component == "ezquake":
        if "/nightly/" in path:
            return component if any(pattern.fullmatch(name) for _, pattern in NIGHTLIES.values()) else None
        return component if RELEASES[component][1].fullmatch(name) else None
    return None


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_manifest(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"format": 1, "project": "x86qw", "captured_at": None, "layout": "distribution-v1", "files": {}, "repositories": {}}
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("format") != 1 or manifest.get("project") != "x86qw":
        raise ValueError(f"invalid distribution manifest: {path}")
    if not isinstance(manifest.get("files"), dict) or not isinstance(manifest.get("repositories"), dict):
        raise ValueError(f"invalid distribution manifest collections: {path}")
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


def collapse_case_duplicates(root: Path, files: dict[str, object]) -> int:
    actual_by_case = {
        path.relative_to(root).as_posix().casefold(): path.relative_to(root).as_posix()
        for path in root.rglob("*") if path.is_file() and path.name != "manifest.json"
    }
    groups: dict[str, list[str]] = {}
    for relative in files:
        groups.setdefault(relative.casefold(), []).append(relative)
    removed = 0
    for folded, relatives in groups.items():
        if len(relatives) < 2:
            continue
        metadata = [files[relative] for relative in relatives]
        identities = {
            (item.get("size"), item.get("sha256"))
            for item in metadata if isinstance(item, dict)
        }
        if len(identities) != 1:
            raise ValueError(f"case-colliding distribution files have different content: {relatives[0]}")
        physical = actual_by_case.get(folded)
        keep = physical if physical in relatives else min(relatives)
        for relative in relatives:
            if relative != keep:
                del files[relative]
                removed += 1
    return removed


def prune_unconsumed(root: Path, manifest: dict[str, object]) -> tuple[int, int]:
    files = manifest["files"]
    assert isinstance(files, dict)
    installer_paths = [
        relative
        for relative in files
        if re.fullmatch(
            r"installer/packages/[0-9]+\.[0-9]+\.[0-9]+/"
            r"x86qw-installer-[0-9]+\.[0-9]+\.[0-9]+\.zip",
            relative,
        )
    ]
    current_installer = max(
        installer_paths,
        key=lambda relative: tuple(
            int(part) for part in PurePosixPath(relative).parts[2].split(".")
        ),
        default=None,
    )
    removed_files = collapse_case_duplicates(root, files)
    for relative in sorted(tuple(files)):
        component_name = consumed_component(relative)
        if component_name is not None:
            metadata = files[relative]
            if isinstance(metadata, dict):
                component = load_component_policy()[component_name]
                consumers = component["consumers"]
                assert isinstance(consumers, list)
                metadata["component"] = component_name
                upstream = source_owner(load_upstreams(UPSTREAMS), relative)
                if component_name == "installer":
                    metadata["consumer"] = (
                        "bootstrap:installer"
                        if relative == current_installer
                        else "archive:installer-history"
                    )
                elif component_name == "nquake":
                    parts = PurePosixPath(relative).parts
                    upstream_path = PurePosixPath(*parts[3:]).as_posix()
                    metadata["consumer"] = consumers[0]
                    metadata["package"] = component_for_source(
                        load_component_catalog(COMPONENT_CATALOG), upstream_path, "reference",
                    )
                elif upstream is not None:
                    metadata["consumer"] = f"development:{upstream}"
                    metadata["package"] = upstream
                elif component_name in {"ktx", "final-arena", "pro-x", "team-fortress", "td2"}:
                    metadata["consumer"] = consumers[0]
                    releases = load_releases(COMPONENT_RELEASES, COMPONENT_CATALOG)
                    package = component_for_artifact_path(releases, relative)
                    metadata["package"] = package
                else:
                    metadata["consumer"] = consumers[0]
            continue
        target = root / relative
        if target.is_file() or target.is_symlink():
            target.unlink()
            removed_files += 1
        del files[relative]

    removed_roots = 0
    distribution_root = root.resolve()
    for relative in OBSOLETE_ROOTS:
        target = (root / relative).resolve()
        if distribution_root not in target.parents:
            raise ValueError(f"unsafe obsolete distribution path: {target}")
        if target.is_dir():
            shutil.rmtree(target)
            removed_roots += 1
        elif target.exists() or target.is_symlink():
            target.unlink()
            removed_roots += 1
    manifest["repositories"] = {}
    manifest["layout"] = "distribution-v1"
    for directory in sorted((path for path in root.rglob("*") if path.is_dir()), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass
    return removed_files, removed_roots


def download_asset(root: Path, asset: Asset, known: object) -> tuple[str, dict[str, object], bool]:
    target = root / asset.path
    if target.is_symlink():
        raise ValueError(f"distribution target must not be a symlink: {target}")
    if (
        isinstance(known, dict)
        and target.is_file()
        and target.stat().st_size == known.get("size")
        and known.get("url") == asset.url
        and (asset.expected_size is None or known.get("size") == asset.expected_size)
        and (asset.expected_sha256 is None or known.get("sha256") == asset.expected_sha256)
    ):
        metadata = dict(known)
        if asset.subcomponent is not None:
            metadata["package"] = asset.subcomponent
        return asset.path, metadata, True
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".part")
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            request = urllib.request.Request(asset.url, headers={"User-Agent": USER_AGENT})
            digest = hashlib.sha256()
            size = 0
            with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as output:
                while block := response.read(1024 * 1024):
                    size += len(block)
                    if asset.expected_size is not None and size > asset.expected_size:
                        raise ValueError(f"download exceeds declared size: {asset.url}")
                    digest.update(block)
                    output.write(block)
            if asset.expected_size is not None and size != asset.expected_size:
                raise ValueError(f"download size mismatch for {asset.url}: expected {asset.expected_size}, got {size}")
            if asset.expected_sha256 is not None and digest.hexdigest() != asset.expected_sha256:
                raise ValueError(f"download SHA-256 mismatch for {asset.url}")
            if asset.expected_git_sha1 is not None:
                git_digest = hashlib.sha1(f"blob {size}\0".encode())
                with temporary.open("rb") as downloaded:
                    for block in iter(lambda: downloaded.read(1024 * 1024), b""):
                        git_digest.update(block)
                if git_digest.hexdigest() != asset.expected_git_sha1:
                    raise ValueError(f"download Git blob identity mismatch for {asset.url}")
            os.replace(temporary, target)
            component = load_component_policy()[asset.component]
            consumers = component["consumers"]
            assert isinstance(consumers, list)
            metadata: dict[str, object] = {
                "component": asset.component,
                "consumer": consumers[0],
                "url": asset.url,
                "size": size,
                "sha256": digest.hexdigest(),
            }
            if asset.subcomponent is not None:
                metadata["package"] = asset.subcomponent
            return asset.path, metadata, False
        except (OSError, ValueError, urllib.error.URLError) as error:
            last_error = error
        if temporary.exists():
            temporary.unlink()
        if attempt < 2:
            time.sleep(1 + attempt)
    assert last_error is not None
    raise last_error


def verify_distribution(
    root: Path,
    manifest: dict[str, object],
    *,
    component_catalog: Path = COMPONENT_CATALOG,
    component_releases: Path = COMPONENT_RELEASES,
    policy_path: Path | None = None,
) -> int:
    files = manifest["files"]
    assert isinstance(files, dict)
    if manifest.get("layout") != "distribution-v1" or manifest.get("repositories") != {}:
        raise ValueError("distribution still contains a legacy or repository-oriented layout")
    expected = set(files)
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    if not expected <= actual:
        missing = sorted(expected - actual)
        raise ValueError(f"distribution is missing a registered upstream file: {missing[0]}")
    component_document = load_component_catalog(component_catalog)
    release_document = load_releases(component_releases, component_catalog)
    allowed_project_files = {
        PurePosixPath(str(source["path"])).relative_to("dist").as_posix()
        for component_entry in component_document["components"]
        for source in (
            *component_entry.get("project_sources", []),
            *component_entry.get("project_overrides", []),
            *component_entry.get("project_archive_overrides", []),
            *component_entry.get("project_inputs", []),
        )
    }
    allowed_release_files = {
        str(artifact["distribution_path"])
        for release in release_document["components"].values()
        for artifact in release.get("artifacts", [])
    }
    allowed_unmanaged = {
        "installer/README.md",
        "installer/VERSION",
        "installer/docs/installer.md",
        "installer/bin/install.ps1",
        "installer/bin/install.sh",
        "installer/bin/manager.py",
        "installer/bin/menu.py",
        "installer/bin/gameplay.py",
        "installer/bin/services.py",
        "installer/bin/session_control.py",
        "installer/bin/x86qw.sh",
        "installer/bin/x86qw.cmd",
        "game-data/id1/pak0.pak",
        "game-data/id1/pak1.pak",
        *allowed_project_files,
    }
    unexpected = sorted(actual - expected - allowed_unmanaged - allowed_release_files)
    if unexpected:
        raise ValueError(f"distribution contains a file without an explicit consumer: {unexpected[0]}")
    installer_files = [
        path for path, metadata in files.items()
        if isinstance(metadata, dict) and metadata.get("component") == "installer"
    ]
    current_installers = [
        path for path, metadata in files.items()
        if isinstance(metadata, dict)
        and metadata.get("component") == "installer"
        and metadata.get("consumer") == "bootstrap:installer"
    ]
    if installer_files and len(current_installers) != 1:
        raise ValueError("distribution must register exactly one current installer package")
    if installer_files:
        current_parts = PurePosixPath(current_installers[0]).parts
        if len(current_parts) != 4 or current_parts[:2] != ("installer", "packages"):
            raise ValueError("current installer package has an invalid distribution path")
        latest = root / "installer/packages/latest"
        expected_link = current_parts[2]
        if not latest.is_symlink() or os.readlink(latest) != expected_link:
            raise ValueError(f"installer latest pointer must target {expected_link}")
        if latest.resolve() != (root / "installer/packages" / expected_link).resolve():
            raise ValueError("installer latest pointer escapes its package directory")
    checked = 0
    for relative, metadata in sorted(files.items()):
        component = consumed_component(
            relative,
            component_catalog=component_catalog,
            component_releases=component_releases,
            policy_path=policy_path,
        )
        if component is None:
            raise ValueError(f"distribution file has no explicit x86QW consumer: {relative}")
        if not isinstance(metadata, dict) or metadata.get("component") not in {None, component}:
            raise ValueError(f"distribution file has invalid component metadata: {relative}")
        path = root / relative
        if path.is_symlink() or path.stat().st_size != metadata.get("size") or file_sha256(path) != metadata.get("sha256"):
            raise ValueError(f"distribution file failed integrity verification: {relative}")
        checked += 1
    return checked
