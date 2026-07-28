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
except ImportError:  # Execucao direta
    from component_policy import component_for_distribution_path, load_component_policy, require_component
    from components import component_for_source, load_catalog as load_component_catalog, source_roots
    from component_releases import component_for_artifact_path, load_releases


ROOT = Path(__file__).resolve().parents[2]
COMPONENT_CATALOG = ROOT / "distribution/inventory/components.json"
COMPONENT_RELEASES = ROOT / "distribution/inventory/component-releases.json"
USER_AGENT = "x86qw-distribution/1"
NQUAKE_REPOSITORY = "nQuake/distfiles"
NQUAKE_REF = "master"

RELEASES = {
    "ezquake": (
        "QW-Group/ezquake-source",
        re.compile(r"ezQuake-(?:macOS-universal|linux-x86_64|windows-x64)\.zip"),
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


def github_json(path: str) -> object:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"https://api.github.com/{path}", headers=headers)
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
    raise ValueError(f"release asset has no supported runtime variant: {name}")


def release_path(component: str, tag: str, name: str) -> str:
    return f"{component}/{tag}/stable/{release_variant(name)}/{name}"


def nightly_path(platform: str, name: str) -> str:
    build = name.split("_ez", 1)[0]
    return f"ezquake/{build}/nightly/{platform}/{name}"


def discover_release_assets() -> list[Asset]:
    assets: list[Asset] = []
    for component, (repository, accepted) in RELEASES.items():
        release = github_json(f"repos/{repository}/releases/latest")
        if not isinstance(release, dict) or not isinstance(release.get("tag_name"), str):
            raise ValueError(f"invalid latest release response for {repository}")
        tag = release["tag_name"]
        selected = 0
        for item in release.get("assets", []):
            if not isinstance(item, dict):
                continue
            name = safe_filename(str(item.get("name", "")))
            url = item.get("browser_download_url")
            size = item.get("size")
            if name and accepted.fullmatch(name) and isinstance(url, str) and isinstance(size, int) and size > 0:
                assets.append(Asset(component, url, release_path(component, tag, name), size))
                selected += 1
        if selected != 3:
            raise ValueError(f"expected three runtime assets in latest {component} release, found {selected}")
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

    commit_data = github_json(f"repos/{NQUAKE_REPOSITORY}/commits/{NQUAKE_REF}")
    if not isinstance(commit_data, dict) or not isinstance(commit_data.get("sha"), str):
        raise ValueError("invalid nQuake commit response")
    commit = commit_data["sha"]
    tree_data = commit_data.get("commit", {}).get("tree", {})
    tree_sha = tree_data.get("sha") if isinstance(tree_data, dict) else None
    if not isinstance(tree_sha, str):
        raise ValueError("nQuake commit has no tree")
    tree = github_json(f"repos/{NQUAKE_REPOSITORY}/git/trees/{tree_sha}?recursive=1")
    if not isinstance(tree, dict) or tree.get("truncated") is True or not isinstance(tree.get("tree"), list):
        raise ValueError("nQuake tree response is missing or truncated")

    assets: list[Asset] = []
    found_roots: set[str] = set()
    for item in tree["tree"]:
        if not isinstance(item, dict) or item.get("type") != "blob":
            continue
        path = item.get("path")
        size = item.get("size")
        blob_sha = item.get("sha")
        if (
            not isinstance(path, str)
            or not isinstance(size, int)
            or not isinstance(blob_sha, str)
            or not re.fullmatch(r"[0-9a-f]{40}", blob_sha)
        ):
            continue
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
            f"nquake/{commit}/{path}",
            size,
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


def discover_assets() -> list[Asset]:
    components = load_component_policy()
    discovered = [
        *discover_release_assets(), *discover_nightlies(), *discover_nquake(),
        *discover_component_release_assets(),
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
    releases = load_releases(component_releases, component_catalog)
    if component in {"ktx", "td2"}:
        return component if component_for_artifact_path(releases, path) is not None else None
    if component == "nquake":
        parts = PurePosixPath(path).parts
        if len(parts) < 3 or parts[0] != "nquake" or len(parts[1]) != 40:
            return None
        upstream_path = PurePosixPath(*parts[2:]).as_posix()
        catalog = load_component_catalog(component_catalog)
        return component if component_for_source(catalog, upstream_path, "reference") is not None else None
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
                metadata["consumer"] = consumers[0]
                if component_name in {"ktx", "td2"}:
                    releases = load_releases(COMPONENT_RELEASES, COMPONENT_CATALOG)
                    package = component_for_artifact_path(releases, relative)
                    metadata["package"] = package
                elif component_name == "nquake":
                    parts = PurePosixPath(relative).parts
                    upstream_path = PurePosixPath(*parts[2:]).as_posix()
                    metadata["package"] = component_for_source(
                        load_component_catalog(COMPONENT_CATALOG), upstream_path, "reference",
                    )
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
            git_digest = hashlib.sha1() if asset.expected_git_sha1 is not None else None
            if git_digest is not None:
                if asset.expected_size is None:
                    raise ValueError(f"git blob verification requires a declared size: {asset.url}")
                git_digest.update(f"blob {asset.expected_size}\0".encode())
            size = 0
            with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as output:
                while block := response.read(1024 * 1024):
                    size += len(block)
                    if asset.expected_size is not None and size > asset.expected_size:
                        raise ValueError(f"download exceeds declared size: {asset.url}")
                    digest.update(block)
                    if git_digest is not None:
                        git_digest.update(block)
                    output.write(block)
            if asset.expected_size is not None and size != asset.expected_size:
                raise ValueError(f"download size mismatch for {asset.url}: expected {asset.expected_size}, got {size}")
            if asset.expected_sha256 is not None and digest.hexdigest() != asset.expected_sha256:
                raise ValueError(f"download SHA-256 mismatch for {asset.url}")
            if git_digest is not None and git_digest.hexdigest() != asset.expected_git_sha1:
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
    allowed_project_files = {
        PurePosixPath(str(source["path"])).relative_to("dist").as_posix()
        for component_entry in component_document["components"]
        for source in component_entry.get("project_sources", [])
    }
    allowed_unmanaged = {"id1/pak0.pak", "id1/pak1.pak", *allowed_project_files}
    unexpected = sorted(actual - expected - allowed_unmanaged)
    if unexpected:
        raise ValueError(f"distribution contains a file without an explicit consumer: {unexpected[0]}")
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
