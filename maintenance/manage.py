#!/usr/bin/env python3
"""Gerencia fontes, inventarios, builds e publicacao da distribuicao x86QW."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unicodedata
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from maintenance.tools.build_component_packages import build_packages, register_packages
from maintenance.tools.build_core_package import build_core_package
from maintenance.tools import release_ownership
from maintenance.tools.build_package import verify_artifact
from maintenance.tools.check_component_updates import check_updates
from maintenance.tools.component_releases import (
    load_releases,
    validate_releases,
)
from maintenance.tools.component_policy import (
    component_for_distribution_path,
    load_component_policy,
    require_component,
)
from maintenance.tools.component_sources import discover_snapshot
from maintenance.tools.components import (
    component_for_source,
    components_by_id,
    load_catalog as load_component_catalog,
    profile_fingerprint,
    validate_catalog as validate_component_catalog,
    validate_portable_relative_path,
    validate_tree_partition,
)
from maintenance.tools.downloader import (
    BoundedMetadata,
    DownloadError,
    DownloadHTTPError,
    MAX_ARTIFACT_BYTES,
    PinnedArtifact,
    download,
    safe_url_for_log,
    validate_https_url,
)
from maintenance.tools.publish_gitlab_packages import artifact_url, local_artifact, remote_sha256
from maintenance.tools.public_upstreams import github_commit_revision
from maintenance.tools.product_catalog import encoded_product_catalog
from maintenance.tools.runtime_catalog import load_inventory as load_runtime_inventory
from maintenance.tools.sync_distribution import (
    Asset,
    consumed_component,
    discover_assets,
    download_asset,
    file_sha256,
    load_manifest,
    pin_assets_from_manifest,
    unpinned_asset_paths,
    verify_distribution,
    write_manifest,
)
from maintenance.tools.validate_catalog import PACKAGE_FIELDS, validate_catalog, validate_package
from maintenance.tools.validate_recipes import recipe_paths, validate_recipe
from maintenance.tools.upstreams import load_upstreams, source_owner, verify_preserved_sources
from x86qw_runtime.trust import TrustError, load_trusted_catalog, validate_bootstrap_policy


DIST = PROJECT_ROOT / "dist"
MAINTENANCE = PROJECT_ROOT / "maintenance"
INVENTORY = MAINTENANCE / "inventory"
COMPONENTS = INVENTORY / "components.json"
RELEASES = INVENTORY / "component-releases.json"
POLICY = INVENTORY / "component-policy.json"
UPSTREAMS = INVENTORY / "upstreams.json"
RECIPES = MAINTENANCE / "recipes"
BUILDS = MAINTENANCE / "build/packages"
CATALOG = PROJECT_ROOT / "site/public/api/v1/catalog.json"
PRODUCT_CATALOG = PROJECT_ROOT / "site/public/api/v1/product.json"
PUBLIC_TRUST_METADATA = PROJECT_ROOT / "site/public/api/v1/trust/metadata"
PUBLIC_TRUST_TARGETS = PROJECT_ROOT / "site/public/api/v1/trust/targets"
PRIMARY_GITHUB_REPOSITORY = "x86dx2/x86qw"
GITHUB_API_MAX_BYTES = 4 * 1024 * 1024
GITHUB_API_DEADLINE_SECONDS = 60
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]*$")
SAFE_COMPONENT_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SAFE_CONSUMER = re.compile(r"^[a-z][a-z0-9-]*:[a-z0-9][a-z0-9_.+-]*$")
EXPECTED_STABLE_MEMBERS = {
    "linux": ["ezQuake-x86_64.AppImage"],
    "macos": ["ezQuake.app/Contents/MacOS/ezQuake"],
    "windows": ["ezquake.exe"],
}


class ManagerError(RuntimeError):
    """Erro de operacao que deve ser explicado sem traceback por padrao."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ManagerError(f"nao foi possivel ler {path}: {error}") from error
    if not isinstance(value, dict):
        raise ManagerError(f"o arquivo precisa conter um objeto JSON: {path}")
    return value


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=".json", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write("\n")
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class _LocalTufFetcher:
    """Serve a checked-out public TUF projection through the runtime boundary."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def download_bytes(self, url: str, max_length: int) -> bytes:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.query or parsed.fragment:
            raise TrustError("projeção local de TUF recebeu URL inválida")
        name = PurePosixPath(parsed.path).name
        if not name or name in {".", ".."}:
            raise TrustError("projeção local de TUF recebeu caminho inválido")
        matches = [
            path for path in self.root.rglob(name)
            if path.is_file() and not path.is_symlink()
        ]
        if len(matches) != 1:
            raise TrustError(f"projeção local de TUF não possui {name}")
        payload = matches[0].read_bytes()
        if len(payload) > max_length:
            raise TrustError(f"metadata TUF local excede o limite: {name}")
        return payload


def verify_local_tuf_catalog() -> str:
    """Validate the embedded root and an optional checked-out TUF projection.

    A source checkout may be ahead of the public site, so absence of the
    generated public metadata is reported as pending rather than being
    confused with an expired legacy signing format.
    """

    root_path = MAINTENANCE / "trust/root.json"
    try:
        root_bytes = root_path.read_bytes()
        validate_bootstrap_policy(root_bytes)
    except (OSError, TrustError) as error:
        raise TrustError(f"root TUF incorporada inválida: {error}") from error

    metadata_files = tuple(PUBLIC_TRUST_METADATA.rglob("*.json")) if PUBLIC_TRUST_METADATA.is_dir() else ()
    target_files = tuple(PUBLIC_TRUST_TARGETS.rglob("*.json")) if PUBLIC_TRUST_TARGETS.is_dir() else ()
    if not metadata_files and not target_files:
        return "root validada; metadata pública aguardando publicação"
    if not PUBLIC_TRUST_METADATA.is_dir() or not PUBLIC_TRUST_TARGETS.is_dir():
        raise TrustError("projeção pública de TUF incompleta")
    required_metadata = {
        "timestamp.json",
        "1.root.json",
    }
    names = {path.name for path in metadata_files if not path.is_symlink()}
    if not required_metadata <= names or not any(
        path.name.endswith(".snapshot.json") for path in metadata_files
    ) or not any(path.name.endswith(".targets.json") for path in metadata_files):
        raise TrustError("projeção pública de TUF não contém root/timestamp/snapshot/targets")

    with tempfile.TemporaryDirectory(prefix="x86qw-verify-tuf-") as temporary:
        cache = Path(temporary)
        authenticated = load_trusted_catalog(
            bootstrap_root=root_bytes,
            metadata_dir=cache / "metadata",
            target_dir=cache / "targets",
            metadata_base_url="https://local.x86qw.invalid/trust/metadata/",
            target_base_url="https://local.x86qw.invalid/trust/targets/",
            fetcher=_LocalTufFetcher(PROJECT_ROOT / "site/public/api/v1/trust"),
        )
    try:
        local_catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise TrustError("catálogo local não pôde ser lido para comparação TUF") from error
    if local_catalog != authenticated:
        raise TrustError("catálogo local diverge do target TUF autenticado")
    return "autenticada localmente"


def run(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        check=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def require_clean_worktree() -> None:
    status = run(["git", "status", "--porcelain"], capture=True).stdout.strip()
    if status:
        raise ManagerError(
            "o Git possui alteracoes locais. Conclua ou descarte essas alteracoes antes de modificar a distribuicao"
        )


def copy_tree(source: Path, destination: Path) -> None:
    """Copia uma arvore usando hard links quando o volume permite."""

    def link_or_copy(origin: str, target: str) -> str:
        try:
            os.link(origin, target)
            return target
        except OSError:
            return shutil.copy2(origin, target)

    shutil.copytree(
        source,
        destination,
        copy_function=link_or_copy,
        symlinks=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


def copy_local_file_atomically(
    origin: Path,
    destination: Path,
    *,
    expected_size: int,
    expected_sha256: str,
    mode: int,
) -> tuple[int, str]:
    """Copia um source validado sem escrever sobre um hardlink do workspace."""

    if expected_size <= 0 or expected_size > MAX_ARTIFACT_BYTES:
        raise ManagerError("size local planejado invalido")
    if HEX64.fullmatch(expected_sha256) is None:
        raise ManagerError("sha256 local planejado invalido")
    if mode not in {0o644, 0o755}:
        raise ManagerError("modo local planejado invalido")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}-",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    source_descriptor = -1
    try:
        source_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        source_descriptor = os.open(origin, source_flags)
        with os.fdopen(source_descriptor, "rb") as source_stream:
            source_descriptor = -1
            details = os.fstat(source_stream.fileno())
            if not stat.S_ISREG(details.st_mode):
                raise ManagerError("source local deixou de ser arquivo regular")

            with os.fdopen(temporary_descriptor, "wb") as output:
                temporary_descriptor = -1
                digest = hashlib.sha256()
                size = 0
                while chunk := source_stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > expected_size:
                        raise ManagerError("source local mudou depois da validacao")
                    output.write(chunk)
                    digest.update(chunk)

                copied_sha256 = digest.hexdigest()
                if size != expected_size or copied_sha256 != expected_sha256:
                    raise ManagerError("source local mudou depois da validacao")

                output.flush()
                if hasattr(os, "fchmod"):
                    os.fchmod(output.fileno(), mode)
                else:  # pragma: no cover - Python sem fchmod
                    os.chmod(temporary, mode)
                os.fsync(output.fileno())

        os.replace(temporary, destination)
        return size, copied_sha256
    except ManagerError:
        raise
    except OSError as error:
        raise ManagerError(f"nao foi possivel copiar o source local: {error}") from error
    finally:
        if source_descriptor >= 0:
            os.close(source_descriptor)
        if temporary_descriptor >= 0:
            os.close(temporary_descriptor)
        temporary.unlink(missing_ok=True)


def safe_relative(value: object, prefix: str) -> str:
    try:
        portable = validate_portable_relative_path(value, "caminho")
    except ValueError as error:
        raise ManagerError(str(error)) from error
    relative = PurePosixPath(portable)
    if len(relative.parts) < 2:
        raise ManagerError(f"caminho inseguro: {value}")
    if relative.parts[0] != prefix:
        raise ManagerError(f"o caminho precisa ficar em {prefix}/: {value}")
    return relative.as_posix()


def semantic_path_identity(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def validate_destination_tree(destination: str) -> None:
    """Reject semantic collisions and non-file targets already present in dist/."""

    relative = PurePosixPath(destination).relative_to("dist")
    existing: dict[str, str] = {}
    if DIST.exists():
        for path in DIST.rglob("*"):
            name = path.relative_to(DIST).as_posix()
            existing.setdefault(semantic_path_identity(name), name)

    for depth in range(1, len(relative.parts) + 1):
        candidate = PurePosixPath(*relative.parts[:depth]).as_posix()
        known = existing.get(semantic_path_identity(candidate))
        if known is not None and known != candidate:
            raise ManagerError(
                f"destino colide com caminho existente em caixa ou Unicode: {candidate}"
            )
        target = DIST / Path(*relative.parts[:depth])
        if target.is_symlink():
            raise ManagerError(f"destino atravessa symlink existente: {candidate}")
        if target.exists():
            if depth < len(relative.parts) and not target.is_dir():
                raise ManagerError(f"pai do destino nao e diretorio: {candidate}")
            if depth == len(relative.parts) and not target.is_file():
                raise ManagerError(f"destino existente nao e arquivo regular: {candidate}")


def validate_local_source_path(base: Path, value: object) -> Path:
    try:
        portable = validate_portable_relative_path(value, "source local")
    except ValueError as error:
        raise ManagerError(str(error)) from error
    raw_parts = portable.split("/")

    base_resolved = base.resolve()
    candidate = base_resolved
    for part in raw_parts:
        candidate /= part
        if candidate.is_symlink():
            raise ManagerError("source local precisa ser arquivo regular sem symlink")
    try:
        details = candidate.stat()
    except OSError as error:
        raise ManagerError("source local precisa ser arquivo regular sem symlink") from error
    if not stat.S_ISREG(details.st_mode):
        raise ManagerError("source local precisa ser arquivo regular sem symlink")
    try:
        candidate.resolve().relative_to(base_resolved)
    except ValueError as error:
        raise ManagerError("source local escapa do diretorio da definicao") from error
    return candidate


def remove_empty_directories(root: Path) -> None:
    for directory in sorted((path for path in root.rglob("*") if path.is_dir()), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass


def distribution_delta(assets: list[Asset], manifest: dict[str, object]) -> list[dict[str, str]]:
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise ManagerError("dist/manifest.json nao possui uma colecao de arquivos valida")
    desired = {asset.path: asset for asset in assets}
    changes: list[dict[str, str]] = []
    for path, asset in desired.items():
        current = files.get(path)
        reason = None
        if not isinstance(current, dict):
            reason = "novo"
        elif current.get("url") != asset.url:
            reason = "origem alterada"
        elif asset.expected_size is not None and current.get("size") != asset.expected_size:
            reason = "tamanho alterado"
        elif asset.expected_sha256 is not None and current.get("sha256") != asset.expected_sha256:
            reason = "conteudo alterado"
        if reason:
            changes.append({"path": path, "status": "update-available", "reason": reason})
    for path in sorted(set(files) - set(desired)):
        changes.append({"path": path, "status": "obsolete", "reason": "nao pertence mais ao conjunto atual"})
    return sorted(changes, key=lambda item: item["path"])


def summarize_delta(delta: list[dict[str, str]]) -> list[str]:
    nquake_new: dict[str, int] = {}
    nquake_old: dict[str, int] = {}
    others: list[str] = []
    for item in delta:
        parts = PurePosixPath(item["path"]).parts
        if (
            len(parts) > 3
            and parts[:2] == ("distributions", "nquake")
            and re.fullmatch(r"[0-9a-f]{40}", parts[2])
        ):
            group = nquake_old if item["status"] == "obsolete" else nquake_new
            group[parts[2]] = group.get(parts[2], 0) + 1
            continue
        others.append(f"{item['path']} ({item['reason']})")
    if nquake_new or nquake_old:
        old = ", ".join(f"{revision[:12]} ({count} removidos)" for revision, count in nquake_old.items()) or "nenhum"
        new = ", ".join(f"{revision[:12]} ({count} novos)" for revision, count in nquake_new.items()) or "nenhum"
        others.insert(0, f"snapshot nQuake: {old} -> {new}")
    return others


def ezquake_runtime_coordinates(asset: Asset) -> tuple[str, str, str] | None:
    if asset.component != "ezquake":
        return None
    parts = PurePosixPath(asset.path).parts
    if len(parts) < 6 or parts[:2] != ("clients", "ezquake"):
        return None
    channel, version, variant = parts[2], parts[3], parts[4]
    if channel not in {"stable", "nightly"} or not variant.startswith(
        ("macos-", "linux-", "windows-")
    ):
        return None
    return channel, version, variant


def update_inventory_lines(
    results: list[dict[str, str]],
    assets: list[Asset],
    releases: dict[str, object],
    catalog: dict[str, object],
) -> list[str]:
    lines: list[str] = []
    packages = catalog.get("packages")
    if not isinstance(packages, list):
        raise ManagerError("catalogo sem pacotes")
    for channel in ("stable", "nightly"):
        current = sorted({
            str(package["version"])
            for package in packages
            if isinstance(package, dict)
            and package.get("component") == "ezquake"
            and package.get("channel") == channel
        })
        upstream = sorted({
            coordinates[1]
            for asset in assets
            if (coordinates := ezquake_runtime_coordinates(asset)) is not None
            and coordinates[0] == channel
        })
        if len(current) != 1 or len(upstream) != 1:
            raise ManagerError(f"catalogo ezQuake {channel} precisa representar exatamente uma versao")
        marker = "OK" if current == upstream else "ATUALIZAR"
        version = current[0] if marker == "OK" else f"{current[0]} -> {upstream[0]}"
        platform_count = sum(
            (coordinates := ezquake_runtime_coordinates(asset)) is not None
            and coordinates[0] == channel
            for asset in assets
        )
        lines.append(f"[{marker}] ezQuake {channel}: {version} ({platform_count} plataformas)")

    release_components = releases.get("components")
    if not isinstance(release_components, dict):
        raise ManagerError("inventario sem releases de componentes")
    component_catalog = load_component_catalog(COMPONENTS)
    labels = {
        str(component["id"]): str(component["label"])
        for component in component_catalog["components"]
    }

    reference_results = [item for item in results if item["strategy"] == "reference-snapshot"]
    reference = releases.get("reference")
    if not isinstance(reference, dict):
        raise ManagerError("inventario sem referencia nQuake")
    lines.append(
        f"[OK] Conteudo baseado no nQuake: {str(reference['revision'])[:12]} "
        f"({len(reference_results)} componentes)"
    )
    for result in reference_results:
        identifier = result["component"]
        release = release_components.get(identifier)
        if not isinstance(release, dict):
            raise ManagerError(f"release ausente: {identifier}")
        lines.append(f"     - {labels.get(identifier, identifier)}: {release['version']}")

    for result in results:
        if result["strategy"] == "reference-snapshot":
            continue
        identifier = result["component"]
        release = release_components.get(identifier)
        if not isinstance(release, dict):
            raise ManagerError(f"release ausente: {identifier}")
        marker = "OK" if result["status"] == "current" else "ATUALIZAR"
        upstream = release.get("upstream")
        upstream_version = str(upstream.get("release")) if isinstance(upstream, dict) else result["latest_source"]
        if marker != "OK":
            upstream_version = result["latest_source"]
        detail = str(result["installed"])
        if upstream_version != detail:
            detail = f"upstream {upstream_version}; pacote x86QW {detail}"
        artifacts = release.get("artifacts", [])
        locations = [
            f"dist/{artifact['distribution_path']}"
            for artifact in artifacts
            if isinstance(artifact, dict) and isinstance(artifact.get("distribution_path"), str)
        ]
        location = f"; fonte {', '.join(locations)}" if locations else ""
        lines.append(f"[{marker}] {labels.get(identifier, identifier)}: {detail}{location}")
    return lines


def reference_revision(assets: list[Asset]) -> str:
    revisions = {
        PurePosixPath(asset.path).parts[2]
        for asset in assets
        if asset.component == "nquake" and len(PurePosixPath(asset.path).parts) > 3
    }
    if len(revisions) != 1:
        raise ManagerError("a descoberta nQuake nao produziu exatamente uma revisao")
    revision = revisions.pop()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ManagerError(f"revisao nQuake invalida: {revision}")
    return revision


def git_blob_sha1(path: Path) -> str:
    digest = hashlib.sha1()
    size = path.stat().st_size
    digest.update(f"blob {size}\0".encode())
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def reference_content_changed(
    assets: list[Asset],
    manifest: dict[str, object],
    *,
    root: Path = DIST,
) -> bool:
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise ManagerError("manifesto sem arquivos")
    current_by_upstream: dict[str, tuple[str, dict[str, object]]] = {}
    for relative, metadata in files.items():
        parts = PurePosixPath(relative).parts
        if (
            len(parts) > 3
            and parts[:2] == ("distributions", "nquake")
            and re.fullmatch(r"[0-9a-f]{40}", parts[2])
            and isinstance(metadata, dict)
        ):
            current_by_upstream[PurePosixPath(*parts[3:]).as_posix()] = (relative, metadata)
    discovered = [asset for asset in assets if asset.component == "nquake"]
    if len(discovered) != len(current_by_upstream):
        return True
    for asset in discovered:
        parts = PurePosixPath(asset.path).parts
        upstream = PurePosixPath(*parts[3:]).as_posix()
        current = current_by_upstream.get(upstream)
        if current is None or asset.expected_git_sha1 is None:
            return True
        relative, metadata = current
        path = root / relative
        if (
            not path.is_file()
            or path.is_symlink()
            or (asset.expected_size is not None and path.stat().st_size != asset.expected_size)
            or (asset.expected_size is not None and metadata.get("size") != asset.expected_size)
            or git_blob_sha1(path) != asset.expected_git_sha1
        ):
            return True
    return False


def preserve_current_reference_assets(
    assets: list[Asset], manifest: dict[str, object], *, root: Path = DIST,
) -> list[Asset]:
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise ManagerError("manifesto sem arquivos")
    preserved = [asset for asset in assets if asset.component != "nquake"]
    for relative, metadata in files.items():
        parts = PurePosixPath(relative).parts
        if not (
            len(parts) > 3
            and parts[:2] == ("distributions", "nquake")
            and re.fullmatch(r"[0-9a-f]{40}", parts[2])
            and isinstance(metadata, dict)
        ):
            continue
        path = root / relative
        if not path.is_file() or path.is_symlink():
            raise ManagerError(f"arquivo nQuake registrado esta ausente: {relative}")
        preserved.append(Asset(
            "nquake",
            str(metadata["url"]),
            relative,
            int(metadata["size"]),
            str(metadata.get("package")) if metadata.get("package") else None,
            str(metadata["sha256"]),
        ))
    return sorted(preserved, key=lambda asset: asset.path)


def update_reference_releases(releases: dict[str, object], revision: str) -> bool:
    reference = releases.get("reference")
    components = releases.get("components")
    if not isinstance(reference, dict) or not isinstance(components, dict):
        raise ManagerError("inventario de releases de componentes invalido")
    old = str(reference.get("revision", ""))
    if old == revision:
        return False
    short = revision[:12]
    old_short = old[:12]
    reference["revision"] = revision
    for release in components.values():
        if not isinstance(release, dict):
            continue
        strategy = release.get("strategy")
        version = str(release.get("version", ""))
        if strategy == "reference-snapshot":
            suffix = version[len(old_short):] if version.startswith(old_short) else ""
            release["version"] = short + suffix
        elif strategy in {"upstream-overlay", "reference-overlay"}:
            release["version"] = version.replace(f"nquake.{old_short}", f"nquake.{short}")
            if isinstance(release.get("distribution_tag"), str):
                release["distribution_tag"] = str(release["distribution_tag"]).replace(
                    f"nquake.{old_short}", f"nquake.{short}"
                )
    releases["checked_at"] = now()
    return True


def apply_reference_transition(
    releases: dict[str, object],
    proposed: object,
) -> tuple[str, str] | None:
    """Apply one explicit, stale-safe nQuake reference transition in memory."""

    if proposed is None:
        return None
    if not isinstance(proposed, dict) or set(proposed) != {
        "repository", "previous_revision", "revision",
    }:
        raise ManagerError(
            "reference precisa declarar somente repository, previous_revision e revision"
        )
    current = releases.get("reference")
    if not isinstance(current, dict):
        raise ManagerError("inventario de releases nao possui referencia nQuake")
    repository = proposed.get("repository")
    previous = proposed.get("previous_revision")
    revision = proposed.get("revision")
    if (
        not isinstance(repository, str)
        or not isinstance(previous, str)
        or HEX40.fullmatch(previous) is None
        or not isinstance(revision, str)
        or HEX40.fullmatch(revision) is None
    ):
        raise ManagerError("transicao da referencia nQuake possui campos invalidos")
    try:
        parsed_repository = validate_https_url(
            repository, "repository da referencia nQuake",
        )
    except DownloadError as error:
        raise ManagerError("repository da referencia nQuake invalido") from error
    if parsed_repository.query:
        raise ManagerError("repository da referencia nQuake nao pode conter query")
    current_repository = current.get("repository")
    current_revision = current.get("revision")
    if repository != current_repository:
        raise ManagerError("transicao da referencia nQuake nao pode trocar o repository")
    if previous != current_revision:
        raise ManagerError(
            "previous_revision da referencia nQuake diverge do inventario atual"
        )
    if revision == previous:
        raise ManagerError("transicao da referencia nQuake precisa alterar a revisao")
    update_reference_releases(releases, revision)
    return previous, revision


def reference_asset_url(repository: str, revision: str, upstream_path: str) -> str:
    """Derive the only raw GitHub origin authorized by the pinned reference."""

    try:
        parsed = validate_https_url(repository, "repository da referencia nQuake")
    except DownloadError as error:
        raise ManagerError("repository da referencia nQuake invalido") from error
    repository_parts = parsed.path.strip("/").split("/")
    if (
        parsed.hostname is None
        or parsed.hostname.casefold() != "github.com"
        or parsed.port not in (None, 443)
        or parsed.query
        or len(repository_parts) != 2
        or any(not part for part in repository_parts)
    ):
        raise ManagerError("repository da referencia nQuake nao e um repositorio GitHub canonico")
    owner, name = repository_parts
    quoted_path = urllib.parse.quote(upstream_path, safe="/")
    return f"https://raw.githubusercontent.com/{owner}/{name}/{revision}/{quoted_path}"


def asset_platform(asset: Asset) -> str:
    coordinates = ezquake_runtime_coordinates(asset)
    if coordinates is None:
        raise ManagerError(f"artefato não é um runtime ezQuake: {asset.path}")
    variant = coordinates[2]
    if variant.startswith("macos"):
        return "macos"
    if variant.startswith("linux"):
        return "linux"
    if variant.startswith("windows"):
        return "windows"
    raise ManagerError(f"plataforma ezQuake desconhecida: {asset.path}")


def ezquake_source_revision(asset: Asset) -> str:
    coordinates = ezquake_runtime_coordinates(asset)
    if coordinates is None:
        raise ManagerError(f"artefato não é um runtime ezQuake: {asset.path}")
    channel, version, _ = coordinates
    if channel == "stable":
        return version
    registry = load_upstreams(UPSTREAMS)
    entries = registry["upstreams"]
    assert isinstance(entries, list)
    for upstream in entries:
        assert isinstance(upstream, dict)
        if upstream.get("id") != "ezquake-nightly" or upstream.get("version") != version:
            continue
        revision = upstream.get("revision")
        if isinstance(revision, str) and re.fullmatch(r"[0-9a-f]{40}", revision):
            return revision
    short = version.rsplit("_", 1)[-1]
    return github_commit_revision("QW-Group/ezquake-source", short)


def update_ezquake_catalog(
    catalog: dict[str, object],
    assets: list[Asset],
    manifest: dict[str, object],
    recipes: Path,
) -> tuple[str, str]:
    packages = catalog.get("packages")
    files = manifest.get("files")
    if not isinstance(packages, list) or not isinstance(files, dict):
        raise ManagerError("catalogo ou manifesto invalido")
    templates = {
        (str(item["channel"]), str(item["platform"])): item
        for item in packages
        if isinstance(item, dict) and item.get("component") == "ezquake"
    }
    ezquake_assets = [asset for asset in assets if ezquake_runtime_coordinates(asset) is not None]
    replacements: list[dict[str, object]] = []
    stable_version = ""
    nightly_version = ""
    stable_records: list[dict[str, object]] = []
    for asset in ezquake_assets:
        parts = PurePosixPath(asset.path).parts
        channel, version = parts[2], parts[3]
        platform = asset_platform(asset)
        template = templates.get((channel, platform))
        metadata = files.get(asset.path)
        if not isinstance(template, dict) or not isinstance(metadata, dict):
            raise ManagerError(f"faltam metadados para atualizar o catalogo: {asset.path}")
        source_revision = ezquake_source_revision(asset)
        record = dict(template)
        record.update({
            "version": version,
            "filename": PurePosixPath(asset.path).name,
            "size": metadata["size"],
            "sha256": metadata["sha256"],
            "origin_url": asset.url,
            "license_url": f"https://github.com/QW-Group/ezquake-source/blob/{source_revision}/LICENSE",
            "source_urls": [
                f"https://github.com/QW-Group/ezquake-source/archive/{'refs/tags/' if channel == 'stable' else ''}{source_revision}.tar.gz"
            ],
            "distribution_path": asset.path,
        })
        tag = f"ezquake-{version}" if channel == "stable" else f"ezquake-nightly-{version}"
        github_repository = PRIMARY_GITHUB_REPOSITORY
        if template.get("version") == version and template.get("sha256") == metadata["sha256"]:
            for existing_url in template.get("urls", []):
                try:
                    github_repository, _ = github_release_coordinates(
                        str(existing_url), str(record["filename"]),
                    )
                    break
                except ManagerError:
                    continue
        record["urls"] = [
            f"https://github.com/{github_repository}/releases/download/{tag}/{record['filename']}"
        ]
        record["urls"].append(artifact_url(record))
        record["mirror_title"] = f"x86QW Content · ezQuake {channel} {version}"
        record["mirror_notes"] = (
            f"Cliente ezQuake {channel} {version} preservado pela distribuição x86QW."
        )
        record["mirror_latest"] = False
        replacements.append(record)
        if channel == "stable":
            stable_version = version
            stable_records.append(record)
        else:
            nightly_version = version
    if len(replacements) != 6 or not stable_version or not nightly_version:
        raise ManagerError("a atualizacao exige tres builds stable e tres builds nightly do ezQuake")
    packages[:] = [item for item in packages if not isinstance(item, dict) or item.get("component") != "ezquake"]
    packages.extend(replacements)
    packages.sort(key=lambda item: (
        str(item.get("component", "")), str(item.get("package", item.get("component", ""))),
        str(item.get("channel", "")), str(item.get("platform", "")), str(item.get("version", "")),
    ))
    catalog["generated_at"] = now()

    ezquake_recipes = recipes / "ezquake"
    if ezquake_recipes.exists():
        shutil.rmtree(ezquake_recipes)
    target_root = ezquake_recipes / stable_version
    target_root.mkdir(parents=True)
    for record in stable_records:
        platform = str(record["platform"])
        package = {key: record[key] for key in PACKAGE_FIELDS}
        package["artifact_format"] = "zip"
        package["expected_members"] = EXPECTED_STABLE_MEMBERS[platform]
        recipe = {
            "format": 1,
            "project": "x86qw",
            "kind": "mirror",
            "package": package,
            "review": {
                "status": "ready",
                "notes": "Artefato oficial preservado byte a byte no dist e validado por SHA-256.",
            },
        }
        variant = PurePosixPath(str(record["distribution_path"])).parts[4]
        write_json(target_root / f"{variant}.json", recipe)
    validate_catalog(catalog)
    for path in recipe_paths(recipes):
        validate_recipe(load_json(path), str(path))
    return stable_version, nightly_version


def normalize_catalog_release_metadata(catalog: dict[str, object]) -> None:
    """Keep GitHub's Latest badge exclusive to the current installer release."""
    packages = catalog.get("packages")
    if not isinstance(packages, list):
        raise ManagerError("catalogo invalido ao normalizar metadados de release")
    for package in packages:
        if not isinstance(package, dict):
            continue
        component = str(package.get("component", ""))
        version = str(package.get("version", ""))
        if component == "installer":
            package.setdefault("mirror_title", f"x86QW Installer {version}")
            package.setdefault("mirror_notes", "Instalador público e atualizador da distribuição x86QW.")
            package["mirror_latest"] = package.get("current") is True
        elif component == "ezquake":
            channel = str(package.get("channel", ""))
            package.setdefault("mirror_title", f"x86QW Content · ezQuake {channel} {version}")
            package.setdefault(
                "mirror_notes",
                f"Cliente ezQuake {channel} {version} preservado pela distribuição x86QW.",
            )
            package["mirror_latest"] = False
        else:
            package["mirror_latest"] = False
    validate_catalog(catalog)


def update_client_compatibility(releases: dict[str, object], stable: str, nightly: str) -> None:
    components = releases.get("components")
    if not isinstance(components, dict):
        return
    for release in components.values():
        if not isinstance(release, dict):
            continue
        compatibility = release.get("compatibility")
        if isinstance(compatibility, dict) and isinstance(compatibility.get("client_scope"), list):
            compatibility["client_scope"] = [f"ezquake-stable:{stable}", f"ezquake-nightly:{nightly}"]


def sync_candidate(candidate: Path, assets: list[Asset], *, workers: int) -> dict[str, object]:
    manifest_path = candidate / "manifest.json"
    manifest = load_manifest(manifest_path)
    files = manifest["files"]
    assert isinstance(files, dict)
    desired = {asset.path for asset in assets}
    failures: list[str] = []
    completed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        jobs = {pool.submit(download_asset, candidate, asset, files.get(asset.path)): asset for asset in assets}
        for future in concurrent.futures.as_completed(jobs):
            asset = jobs[future]
            try:
                path, metadata, cached = future.result()
                files[path] = metadata
                completed += 1
                action = "cache" if cached else "baixado"
                print(f"[{completed:>3}/{len(assets)}] {action}: {path}", flush=True)
            except Exception as error:  # mantem todos os erros da rodada para diagnostico
                failures.append(f"{asset.path}: {error}")
    if failures:
        raise ManagerError(f"{len(failures)} download(s) falharam; primeiro erro: {failures[0]}")
    for relative in sorted(set(files) - desired):
        target = candidate / relative
        if target.is_file() or target.is_symlink():
            target.unlink()
        del files[relative]
    remove_empty_directories(candidate)
    manifest["repositories"] = {}
    manifest["layout"] = "distribution-v1"
    write_manifest(manifest_path, manifest)
    verify_distribution(candidate, manifest)
    return manifest


def validate_staged(
    candidate: Path,
    components_path: Path,
    releases_path: Path,
    catalog_path: Path,
    recipes_path: Path,
) -> None:
    component_catalog = load_component_catalog(components_path)
    releases = load_releases(releases_path, components_path)
    catalog = load_json(catalog_path)
    validate_catalog(catalog)
    for path in recipe_paths(recipes_path):
        validate_recipe(load_json(path), str(path))
    _, snapshot = discover_snapshot(candidate)
    paths = sorted(path.relative_to(snapshot).as_posix() for path in snapshot.rglob("*") if path.is_file())
    validate_tree_partition(component_catalog, paths)
    for component in component_catalog["components"]:
        for source in component.get("project_sources", []):
            source_relative = PurePosixPath(str(source["path"]))
            source_path = candidate / PurePosixPath(*source_relative.parts[1:])
            if not source_path.is_file() or source_path.is_symlink() or not source_path.stat().st_size:
                raise ManagerError(f"fonte x86QW ausente: {source['path']}")
    verify_distribution(
        candidate,
        load_manifest(candidate / "manifest.json"),
        component_catalog=components_path,
        component_releases=releases_path,
        policy_path=POLICY,
    )
    if not isinstance(releases.get("components"), dict):
        raise ManagerError("inventario de releases vazio")


def replace_tree(source: Path, target: Path, backup_root: Path, applied: list[tuple[Path, Path | None]]) -> None:
    backup = backup_root / f"backup-{len(applied)}"
    if target.exists():
        os.replace(target, backup)
        applied.append((target, backup))
    else:
        applied.append((target, None))
    os.replace(source, target)


def apply_workspace(work: Path, *, include_components: bool = True) -> None:
    targets = [(work / "dist", DIST), (work / "recipes", RECIPES)]
    if include_components:
        targets.extend([
            (work / "components.json", COMPONENTS),
            (work / "component-releases.json", RELEASES),
        ])
    targets.append((work / "catalog.json", CATALOG))
    if (work / "packages").exists():
        targets.append((work / "packages", BUILDS))
    applied: list[tuple[Path, Path | None]] = []
    try:
        for source, target in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            replace_tree(source, target, work, applied)
    except Exception:
        for target, backup in reversed(applied):
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)
            if backup is not None and backup.exists():
                os.replace(backup, target)
        raise
    for _, backup in applied:
        if backup is None or not backup.exists():
            continue
        if backup.is_dir():
            shutil.rmtree(backup)
        else:
            backup.unlink()


def prepare_workspace(parent: Path) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="update-", dir=parent))
    copy_tree(DIST, work / "dist")
    copy_tree(RECIPES, work / "recipes")
    shutil.copy2(COMPONENTS, work / "components.json")
    shutil.copy2(RELEASES, work / "component-releases.json")
    shutil.copy2(CATALOG, work / "catalog.json")
    return work


def command_check(options: argparse.Namespace) -> int:
    releases = load_releases(RELEASES, COMPONENTS)
    component_results = check_updates(releases, online=not options.offline)
    delta: list[dict[str, str]] = []
    review_required: list[str] = []
    reference_note: dict[str, object] | None = None
    if not options.offline:
        print("[INFO] Consultando clientes e arquivos incorporados...", file=sys.stderr if options.json else sys.stdout)
        assets = discover_assets()
        manifest = load_manifest(DIST / "manifest.json")
        head = reference_revision(assets)
        pinned = str(releases["reference"]["revision"])
        changed = reference_content_changed(assets, manifest)
        reference_note = {"pinned": pinned, "upstream": head, "consumed_content_changed": changed}
        if head != pinned and not changed:
            assets = preserve_current_reference_assets(assets, manifest)
            for item in component_results:
                if item["strategy"] in {"reference-snapshot", "reference-overlay"}:
                    item["status"] = "current"
                    item["latest_source"] = item["installed"]
        assets = pin_assets_from_manifest(assets, manifest)
        review_required = unpinned_asset_paths(assets)
        delta = distribution_delta(assets, manifest)
    outdated = [item for item in component_results if item["status"] != "current"]
    if options.json:
        print(json.dumps({
            "components": component_results,
            "distribution": delta,
            "reference": reference_note,
            "review_required": review_required,
        }, ensure_ascii=False, indent=2))
    else:
        reference_updates = [
            item for item in outdated
            if item["strategy"] in {"reference-snapshot", "reference-overlay"}
        ]
        for item in component_results:
            if item in reference_updates:
                continue
            marker = "OK" if item["status"] == "current" else "ATUALIZAR"
            detail = item["installed"] if item["status"] == "current" else f"{item['installed']} -> {item['latest_source']}"
            print(f"[{marker}] {item['component']}: {detail}")
        if reference_updates:
            print(
                f"[ATUALIZAR] referencia nQuake: {reference_updates[0]['installed'][:12]} -> "
                f"{reference_updates[0]['latest_source'][:12]} ({len(reference_updates)} componentes)"
            )
        elif reference_note and reference_note["upstream"] != reference_note["pinned"]:
            print(
                f"[OK] referencia nQuake: upstream {str(reference_note['upstream'])[:12]} alterou apenas "
                f"arquivos fora do x86QW; preservado {str(reference_note['pinned'])[:12]}"
            )
        for summary in summarize_delta(delta):
            print(f"[ATUALIZAR] {summary}")
        if review_required:
            print(
                f"[REVISAR] {len(review_required)} candidato(s) remoto(s) ainda não possuem "
                "SHA-256 previamente fixado; nenhum deles pode ser promovido por update."
            )
        print(f"\n{len(outdated)} componente(s) e {len(delta)} arquivo(s) envolvidos em mudancas.")
    return 2 if outdated or delta or review_required else 0


def command_verify(options: argparse.Namespace) -> int:
    catalog = load_json(CATALOG)
    package_count = validate_catalog(catalog)
    try:
        trust_status = verify_local_tuf_catalog()
    except TrustError as error:
        raise ManagerError(
            f"TUF não autentica o catálogo local: {error}"
        ) from error
    component_catalog = load_component_catalog(COMPONENTS)
    runtime_inventory = load_runtime_inventory(
        INVENTORY,
        component_catalog=component_catalog,
        project_root=PROJECT_ROOT,
        public_catalog=catalog,
    )
    if (
        not PRODUCT_CATALOG.is_file()
        or PRODUCT_CATALOG.is_symlink()
        or PRODUCT_CATALOG.read_bytes() != encoded_product_catalog(PROJECT_ROOT)
    ):
        raise ManagerError(
            "site/public/api/v1/product.json diverge dos inventarios canonicos; "
            "execute 'python3 -m maintenance.tools.product_catalog --write'"
        )
    load_releases(RELEASES, COMPONENTS)
    upstream_registry = load_upstreams(UPSTREAMS)
    recipe_count = 0
    for path in recipe_paths(RECIPES):
        validate_recipe(load_json(path), str(path))
        recipe_count += 1
    revision, snapshot = discover_snapshot(DIST)
    paths = sorted(path.relative_to(snapshot).as_posix() for path in snapshot.rglob("*") if path.is_file())
    validate_tree_partition(component_catalog, paths)
    upstream_count = verify_distribution(DIST, load_manifest(DIST / "manifest.json"))
    source_count = verify_preserved_sources(upstream_registry, DIST, PROJECT_ROOT)
    print(
        f"[OK] Catalogo: {package_count} pacotes; componentes: {len(component_catalog['components'])}; "
        f"runtimes: {len(runtime_inventory['runtimes']['runtimes'])}; "
        f"jogos: {len(runtime_inventory['games']['games'])}; "
        f"receitas: {recipe_count}; arquivos upstream: {upstream_count}; fontes: {source_count}; "
        f"nQuake: {revision[:12]}; TUF: {trust_status}."
    )
    misplaced = [
        path for path in (
            "DESIGN.md", "PRODUCT.md", "wrangler.jsonc", "distribution",
            "installer", "inventory", "recipes", "tools", "tests",
        )
        if (PROJECT_ROOT / path).exists()
    ]
    if misplaced:
        raise ManagerError(f"itens fora de contexto na raiz: {', '.join(misplaced)}")
    if not options.no_tests:
        environment = dict(
            os.environ,
            PYTHONDONTWRITEBYTECODE="1",
            # Qualquer smoke de runtime incorporado às suítes herda janela
            # segura. Casos dedicados de fullscreen removem esta variável.
            X86QW_TEST_WINDOWED="1",
        )
        for suite in ("maintenance/tests", "site/tests"):
            print(f"[INFO] Testando {suite}...")
            subprocess.run(
                [sys.executable, "-m", "unittest", "discover", "-s", suite, "-v"],
                cwd=PROJECT_ROOT,
                env=environment,
                check=True,
            )
    print("[OK] Distribuicao e estrutura do projeto validadas.")
    return 0


def command_build(options: argparse.Namespace) -> int:
    project_ref = getattr(options, "project_ref", None)
    for recipe in recipe_paths(RECIPES):
        raw = load_json(recipe)
        package = raw["package"]
        assert isinstance(package, dict)
        if package["component"] == "ezquake":
            artifact = DIST.joinpath(
                "clients", "ezquake", str(package["channel"]), str(package["version"]),
                f"{package['platform']}-{package['architecture']}", str(package["filename"]),
            )
        else:
            artifact = DIST.joinpath(
                str(package["component"]), str(package["version"]), str(package["channel"]),
                f"{package['platform']}-{package['architecture']}", str(package["filename"]),
            )
        verify_artifact(artifact, package)
    BUILDS.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="packages-", dir=BUILDS.parent))
    ownership_temporary = (
        Path(tempfile.mkdtemp(prefix="ownership-", dir=BUILDS.parent))
        if project_ref is not None else None
    )
    try:
        component_ownership = ownership_temporary / "components.json" if ownership_temporary else None
        core_ownership = ownership_temporary / "core.json" if ownership_temporary else None
        manifest = build_packages(
            DIST,
            temporary,
            ownership_output=component_ownership,
            project_ref=project_ref,
        )
        core_package = build_core_package(
            DIST,
            temporary,
            ownership_output=core_ownership,
        )
        manifest["packages"].append(core_package)
        ownership = None
        if component_ownership is not None and core_ownership is not None:
            ownership = release_ownership.merge_documents([
                release_ownership.load_fragment(component_ownership),
                release_ownership.load_fragment(core_ownership),
            ])
        if BUILDS.exists():
            shutil.rmtree(BUILDS)
        os.replace(temporary, BUILDS)
        ownership_root = BUILDS.parent / "ownership"
        if ownership_root.exists():
            shutil.rmtree(ownership_root)
        if ownership is not None:
            ownership_root.mkdir(parents=True, exist_ok=True)
            release_ownership.write_document(ownership_root / "content.json", ownership)
        if options.register:
            register_packages(CATALOG, manifest)
            catalog = load_json(CATALOG)
            normalize_catalog_release_metadata(catalog)
            write_json(CATALOG, catalog)
        print(f"[OK] {len(manifest['packages'])} pacote(s) gerado(s) em {BUILDS}.")
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
        if ownership_temporary is not None and ownership_temporary.exists():
            shutil.rmtree(ownership_temporary)
    return 0


def confirm(message: str, *, yes: bool) -> None:
    if yes:
        return
    if not sys.stdin.isatty():
        raise ManagerError("use --yes para confirmar a operacao sem terminal interativo")
    answer = input(f"{message} [s/N]: ").strip().casefold()
    if answer not in {"s", "sim"}:
        raise ManagerError("operacao cancelada; nenhum arquivo foi alterado")


def command_update(options: argparse.Namespace) -> int:
    if not 1 <= options.workers <= 32:
        raise ManagerError("--workers deve estar entre 1 e 32")
    print("[INFO] Consultando todos os upstreams declarados...")
    releases = load_releases(RELEASES, COMPONENTS)
    results = check_updates(releases, online=True)
    assets = discover_assets()
    print("\nComponentes e clientes verificados:")
    for line in update_inventory_lines(results, assets, releases, load_json(CATALOG)):
        print(line)
    blocked = [
        item for item in results
        if item["status"] != "current"
        and item["strategy"] not in {"reference-snapshot", "reference-overlay"}
    ]
    if blocked:
        names = ", ".join(item["component"] for item in blocked)
        raise ManagerError(
            f"{names} exige nova versao/revisao explicita. Use 'add' com uma definicao revisada antes do update"
        )
    current_manifest = load_manifest(DIST / "manifest.json")
    discovered_reference = reference_revision(assets)
    old_reference = str(releases["reference"]["revision"])
    if discovered_reference != old_reference and not reference_content_changed(assets, current_manifest):
        print(
            f"[INFO] nQuake {discovered_reference[:12]} nao alterou nenhum arquivo consumido; "
            f"mantendo a referencia imutavel {old_reference[:12]}."
        )
        assets = preserve_current_reference_assets(assets, current_manifest)
        new_reference = old_reference
    else:
        new_reference = discovered_reference
    assets = pin_assets_from_manifest(assets, current_manifest)
    review_required = unpinned_asset_paths(assets)
    delta = distribution_delta(assets, current_manifest)
    if not delta and new_reference == old_reference and not review_required:
        print("[OK] O dist e os inventarios ja representam os upstreams atuais.")
        return 0
    print(f"[INFO] {len(delta)} alteracao(oes) de arquivo; nQuake {old_reference[:12]} -> {new_reference[:12]}.")
    if options.dry_run:
        for summary in summarize_delta(delta):
            print(f"  - {summary}")
    if review_required:
        raise ManagerError(
            f"{len(review_required)} candidato(s) remoto(s) exige(m) tamanho e SHA-256 "
            f"revisados antes do update; primeiro: {review_required[0]}. "
            "Use 'add' com uma definição revisada para incorporar o arquivo sem confiar "
            "na própria transferência. Nenhum payload candidato foi baixado."
        )
    if options.dry_run:
        return 0
    require_clean_worktree()
    confirm("Aplicar esta atualizacao ao Git working tree?", yes=options.yes)

    work = prepare_workspace(MAINTENANCE / "build")
    try:
        manifest = sync_candidate(work / "dist", assets, workers=options.workers)
        staged_releases = load_json(work / "component-releases.json")
        update_reference_releases(staged_releases, new_reference)
        staged_catalog = load_json(work / "catalog.json")
        stable, nightly = update_ezquake_catalog(staged_catalog, assets, manifest, work / "recipes")
        update_client_compatibility(staged_releases, stable, nightly)
        write_json(work / "component-releases.json", staged_releases)
        write_json(work / "catalog.json", staged_catalog)
        validate_staged(
            work / "dist", work / "components.json", work / "component-releases.json",
            work / "catalog.json", work / "recipes",
        )
        package_manifest = build_packages(
            work / "dist",
            work / "packages",
            component_catalog=work / "components.json",
            component_releases=work / "component-releases.json",
        )
        package_manifest["packages"].append(build_core_package(work / "dist", work / "packages"))
        register_packages(work / "catalog.json", package_manifest)
        validate_catalog(load_json(work / "catalog.json"))
        apply_workspace(work)
        print(f"[OK] Working tree atualizado: ezQuake {stable}/{nightly}, nQuake {new_reference[:12]}.")
    finally:
        if work.exists():
            shutil.rmtree(work)

    if options.commit:
        command_commit(argparse.Namespace(push=options.push, message=options.message))
    elif options.push:
        raise ManagerError("--push exige --commit")
    else:
        print("[PROXIMO] Execute './maintenance/manage.py verify' e revise 'git diff' antes do commit.")
    return 0


def validate_definition_file(
    entry: object,
    base: Path,
) -> dict[str, object]:
    if not isinstance(entry, dict):
        raise ManagerError("entrada de arquivo invalida")
    destination = safe_relative(entry.get("destination"), "dist")
    source = entry.get("source")
    url = entry.get("url")
    if (source is None) == (url is None):
        raise ManagerError("cada arquivo da definicao deve possuir exatamente source ou url")

    plan: dict[str, object] = {"entry": entry, "destination": destination}
    if source is not None:
        origin = validate_local_source_path(base, source)
        expected_size = entry.get("size")
        expected_sha = entry.get("sha256")
        if expected_size is not None and (
            type(expected_size) is not int
            or expected_size <= 0
            or expected_size > MAX_ARTIFACT_BYTES
        ):
            raise ManagerError("size local invalido na definicao")
        if expected_sha is not None and (
            not isinstance(expected_sha, str)
            or HEX64.fullmatch(expected_sha) is None
        ):
            raise ManagerError("sha256 local invalido na definicao")
        source_details = origin.stat()
        actual_size = source_details.st_size
        if actual_size <= 0 or actual_size > MAX_ARTIFACT_BYTES:
            raise ManagerError("arquivo local precisa ser nao vazio e respeitar o limite global")
        if expected_size is not None and expected_size != actual_size:
            raise ManagerError("size local nao corresponde ao arquivo da definicao")
        actual_sha = file_sha256(origin)
        if expected_sha is not None and expected_sha != actual_sha:
            raise ManagerError("sha256 local nao corresponde ao arquivo da definicao")
        plan.update({
            "source": origin,
            "origin": "arquivo local validado",
            "size": actual_size,
            "sha256": actual_sha,
            "mode": 0o755 if source_details.st_mode & 0o111 else 0o644,
        })
    else:
        if not isinstance(url, str):
            raise ManagerError("URL de arquivo invalida")
        try:
            parsed_url = validate_https_url(url, "URL do arquivo da definicao")
        except DownloadError as error:
            raise ManagerError(str(error)) from error
        expected_size = entry.get("size")
        expected_sha = entry.get("sha256")
        if (
            type(expected_size) is not int
            or expected_size <= 0
            or expected_size > MAX_ARTIFACT_BYTES
            or not isinstance(expected_sha, str)
            or HEX64.fullmatch(expected_sha) is None
        ):
            raise ManagerError(
                "arquivo remoto da definição exige size e sha256 válidos"
            )
        plan.update({
            "url": url,
            "origin": safe_url_for_log(url),
            "size": expected_size,
            "sha256": expected_sha,
        })

    managed = entry.get("managed", False)
    if type(managed) is not bool:
        raise ManagerError("managed precisa ser booleano")
    if managed:
        if source is not None:
            raise ManagerError("arquivo upstream gerenciado precisa usar URL HTTPS fixada")
        assert url is not None
        if parsed_url.query:
            raise ManagerError(
                "URL persistente de arquivo gerenciado nao pode conter query"
            )
        component_name = entry.get("distribution_component")
        consumer = entry.get("consumer")
        if (
            not isinstance(component_name, str)
            or SAFE_COMPONENT_ID.fullmatch(component_name) is None
            or not isinstance(consumer, str)
            or SAFE_CONSUMER.fullmatch(consumer) is None
        ):
            raise ManagerError(
                "arquivo upstream gerenciado exige distribution_component e consumer seguros"
            )
        package = entry.get("package")
        if package is not None and (
            not isinstance(package, str) or SAFE_IDENTIFIER.fullmatch(package) is None
        ):
            raise ManagerError("package do arquivo gerenciado precisa ser identificador seguro")
    return plan


def validate_distribution_change(
    definition: dict[str, object],
    definition_path: Path,
) -> list[dict[str, object]]:
    if (
        definition.get("format") != 1
        or definition.get("project") != "x86qw"
        or definition.get("kind") != "distribution-change"
    ):
        raise ManagerError("a definicao precisa ser x86qw/distribution-change formato 1")
    files = definition.get("files", [])
    if not isinstance(files, list) or not files:
        raise ManagerError("a definicao precisa incorporar ao menos um arquivo diretamente utilizado")
    plans = [validate_definition_file(entry, definition_path.parent) for entry in files]
    destinations = [str(plan["destination"]) for plan in plans]
    destination_identities = [semantic_path_identity(item) for item in destinations]
    if len(destination_identities) != len(set(destination_identities)):
        raise ManagerError("a definicao possui destinos duplicados ou colidentes")
    for destination in destinations:
        validate_destination_tree(destination)

    try:
        policy = load_component_policy(POLICY)
        current_manifest = load_manifest(DIST / "manifest.json")
        upstream_registry = load_upstreams(UPSTREAMS)
    except (OSError, ValueError) as error:
        raise ManagerError(f"inventario da distribuicao invalido: {error}") from error
    current_files = current_manifest.get("files")
    if not isinstance(current_files, dict):
        raise ManagerError("manifesto atual nao possui arquivos validos")

    current_catalog = load_json(CATALOG)
    try:
        validate_catalog(current_catalog)
    except ValueError as error:
        raise ManagerError(f"catalogo publico atual invalido: {error}") from error

    package = definition.get("package")
    if package is not None:
        try:
            validate_package(package, "definition.package")
        except ValueError as error:
            raise ManagerError(str(error)) from error
        assert isinstance(package, dict)
        if package.get("component") == "installer":
            raise ManagerError(
                "pacotes do instalador so podem ser preparados pelo fluxo de release imutavel"
            )

    staged_components = load_json(COMPONENTS)
    staged_releases = load_json(RELEASES)
    reference_transition = apply_reference_transition(
        staged_releases, definition.get("reference"),
    )

    component = definition.get("component")
    replace = definition.get("replace", False)
    if type(replace) is not bool:
        raise ManagerError("replace precisa ser booleano")
    if component is not None:
        if not isinstance(component, dict):
            raise ManagerError("component precisa ser um objeto")
        if (
            not isinstance(component.get("id"), str)
            or SAFE_COMPONENT_ID.fullmatch(component["id"]) is None
        ):
            raise ManagerError("component.id precisa ser identificador seguro")
        profiles = definition.get("profiles", [])
        if not isinstance(profiles, list) or not all(
            isinstance(item, str) and SAFE_COMPONENT_ID.fullmatch(item)
            for item in profiles
        ):
            raise ManagerError("profiles precisa ser uma lista de identificadores seguros")
        if not isinstance(definition.get("release"), dict):
            raise ManagerError("todo componente novo ou substituido precisa declarar release")
        release_entries = staged_releases.get("components")
        if not isinstance(release_entries, dict):
            raise ManagerError("inventario de releases nao possui componentes validos")
        component_id = str(component["id"])
        current_release = release_entries.get(component_id)
        proposed_release = definition["release"]
        assert isinstance(proposed_release, dict)
        if (
            replace
            and isinstance(current_release, dict)
        ):
            current_namespace = current_release.get("distribution_component", "nquake")
            proposed_namespace = proposed_release.get("distribution_component", "nquake")
            if proposed_namespace != current_namespace:
                raise ManagerError(
                    "release substituta precisa preservar distribution_component "
                    f"{current_namespace}: {component_id}"
                )
        preserve_profile_fingerprints(staged_components)
        upsert_component(staged_components, component, replace)
        profile_map = staged_components.get("profiles")
        assert isinstance(profile_map, dict)
        for profile in profiles:
            members = profile_map.get(profile)
            if not isinstance(members, list):
                raise ManagerError(f"perfil desconhecido: {profile}")
            if component["id"] not in members:
                members.append(component["id"])
        preserve_profile_fingerprints(staged_components)
        release_entries[component_id] = proposed_release
    elif "profiles" in definition or "release" in definition:
        raise ManagerError("profiles e release exigem component")

    try:
        validate_component_catalog(staged_components)
        component_ids = set(components_by_id(staged_components))
        namespaces = staged_components.get("content_namespaces")
        if not isinstance(namespaces, list):
            raise ValueError("component catalog has no content namespaces")
        validate_releases(staged_releases, component_ids, set(namespaces))
    except ValueError as error:
        raise ManagerError(f"inventarios propostos invalidos: {error}") from error

    staged_component_entries = staged_components.get("components")
    if not isinstance(staged_component_entries, list):
        raise ManagerError("inventario de componentes proposto invalido")

    def project_consumers(path: str) -> set[str]:
        consumers: set[str] = set()
        for candidate in staged_component_entries:
            if not isinstance(candidate, dict) or not isinstance(candidate.get("id"), str):
                continue
            for field in (
                "project_sources", "project_inputs", "project_overrides",
                "project_archive_overrides",
            ):
                entries = candidate.get(field, [])
                if not isinstance(entries, list):
                    continue
                if any(
                    isinstance(item, dict) and item.get("path") == path
                    for item in entries
                ):
                    consumers.add(str(candidate["id"]))
        return consumers

    def staged_artifact_authority(
        path: str,
    ) -> tuple[str, str, dict[str, object]] | None:
        release_entries = staged_releases.get("components")
        if not isinstance(release_entries, dict):
            return None
        matches = [
            (
                str(release_id),
                str(release.get("distribution_component", "nquake")),
                artifact,
            )
            for release_id, release in release_entries.items()
            if isinstance(release, dict)
            for artifact in release.get("artifacts", [])
            if isinstance(artifact, dict) and artifact.get("distribution_path") == path
        ]
        if len(matches) > 1:
            raise ManagerError(f"destino aparece em mais de uma release proposta: {path}")
        return matches[0] if matches else None

    def require_remote_identity(
        plan: dict[str, object],
        record: dict[str, object],
        *,
        label: str,
        url_field: str,
    ) -> None:
        entry = plan["entry"]
        assert isinstance(entry, dict)
        if (
            entry.get("url") != record.get(url_field)
            or plan["size"] != record.get("size")
            or plan["sha256"] != record.get("sha256")
        ):
            raise ManagerError(
                f"URL, tamanho ou SHA-256 diverge do {label}: {plan['destination']}"
            )

    upstream_entries = upstream_registry.get("upstreams")
    if not isinstance(upstream_entries, list):
        raise ManagerError("registro de upstreams nao possui entradas validas")

    def preserved_source(path: str) -> dict[str, object] | None:
        matches: list[dict[str, object]] = []
        for upstream in upstream_entries:
            if not isinstance(upstream, dict):
                continue
            source = upstream.get("source")
            if isinstance(source, dict) and source.get("distribution_path") == path:
                matches.append(source)
        if len(matches) > 1:
            raise ManagerError(f"fonte preservada duplicada no inventario: {path}")
        return matches[0] if matches else None

    for plan in plans:
        entry = plan["entry"]
        assert isinstance(entry, dict)
        destination = str(plan["destination"])
        relative = PurePosixPath(destination).relative_to("dist").as_posix()

        if "source" in plan:
            if relative in current_files:
                raise ManagerError(
                    f"fonte local nao pode substituir arquivo upstream gerenciado: {relative}"
                )
            consumers = project_consumers(destination)
            if len(consumers) != 1:
                raise ManagerError(
                    f"fonte local precisa de um consumidor exato no BOM proposto: {destination}"
                )
            continue

        if entry.get("managed") is not True:
            raise ManagerError(
                "todo arquivo remoto persistente precisa declarar managed: true"
            )
        component_name = entry["distribution_component"]
        consumer = entry["consumer"]
        assert isinstance(component_name, str)
        assert isinstance(consumer, str)
        try:
            require_component(policy, component_name, relative)
            owner = component_for_distribution_path(policy, relative)
        except ValueError as error:
            raise ManagerError(str(error)) from error
        if owner != component_name:
            raise ManagerError(
                f"componente declarado nao e dono do destino: {component_name} != {owner}"
            )

        existing = current_files.get(relative)
        existing_metadata = existing if isinstance(existing, dict) else None
        source_component = source_owner(upstream_registry, relative)
        allowed_consumers = set(policy[component_name]["consumers"])
        if source_component is not None:
            allowed_consumers.add(f"development:{source_component}")
        if (
            existing_metadata is not None
            and existing_metadata.get("component") == component_name
            and isinstance(existing_metadata.get("consumer"), str)
        ):
            allowed_consumers.add(str(existing_metadata["consumer"]))
        if consumer not in allowed_consumers:
            raise ManagerError(
                f"consumer nao declarado para {component_name}: {consumer}"
            )

        reference_package: str | None = None
        if component_name == "nquake":
            parts = PurePosixPath(relative).parts
            if len(parts) >= 4 and parts[:2] == ("distributions", "nquake"):
                reference = staged_releases.get("reference")
                if not isinstance(reference, dict):
                    raise ManagerError("inventario de releases nao possui referencia nQuake")
                reference_revision = reference.get("revision")
                if (
                    not isinstance(reference_revision, str)
                    or HEX40.fullmatch(reference_revision) is None
                    or parts[2] != reference_revision
                ):
                    raise ManagerError(
                        "revisao do snapshot nQuake diverge da referencia fixada: "
                        f"{parts[2]}"
                    )
                upstream_path = PurePosixPath(*parts[3:]).as_posix()
                repository = reference.get("repository")
                if not isinstance(repository, str):
                    raise ManagerError("inventario de releases nao possui repository nQuake")
                entry = plan["entry"]
                assert isinstance(entry, dict)
                if entry.get("url") != reference_asset_url(
                    repository, reference_revision, upstream_path,
                ):
                    raise ManagerError(
                        "URL do snapshot nQuake diverge da referencia fixada: "
                        f"{relative}"
                    )
                reference_package = component_for_source(
                    staged_components, upstream_path, "reference",
                )
        artifact_authority = staged_artifact_authority(relative)
        if artifact_authority is not None:
            artifact_package, artifact_owner, _ = artifact_authority
            if artifact_owner != component_name:
                raise ManagerError(
                    "distribution_component da release diverge do plano gerenciado: "
                    f"{artifact_owner} != {component_name}: {relative}"
                )
        expected_package = reference_package
        if expected_package is None and artifact_authority is not None:
            expected_package = artifact_package
        if expected_package is None:
            expected_package = source_component
        if expected_package is None and existing_metadata is not None:
            current_package = existing_metadata.get("package")
            if current_package is not None and not isinstance(current_package, str):
                raise ManagerError(f"package atual invalido no manifesto: {relative}")
            expected_package = current_package

        try:
            current_consumer_owner = consumed_component(relative)
        except ValueError as error:
            raise ManagerError(f"destino gerenciado invalido: {error}") from error
        staged_consumer_owner = (
            artifact_authority[1] if artifact_authority is not None else None
        )
        if (
            current_consumer_owner != component_name
            and staged_consumer_owner != component_name
            and not (
                component_name == "nquake"
                and reference_package is not None
                and source_component is None
            )
        ):
            raise ManagerError(
                f"destino nao possui consumidor operacional declarado: {relative}"
            )

        provided_package = entry.get("package")
        if expected_package is None and provided_package is not None:
            raise ManagerError(f"package nao pertence ao destino gerenciado: {relative}")
        if expected_package is not None and provided_package != expected_package:
            raise ManagerError(
                f"package do arquivo gerenciado deve ser {expected_package}: {relative}"
            )

        if existing_metadata is not None:
            require_remote_identity(
                plan,
                existing_metadata,
                label="manifesto imutavel atual",
                url_field="url",
            )
            immutable_fields = {
                "component": entry.get("distribution_component"),
                "consumer": entry.get("consumer"),
                "package": entry.get("package"),
            }
            for field, proposed_value in immutable_fields.items():
                if proposed_value != existing_metadata.get(field):
                    raise ManagerError(
                        f"metadado {field} diverge do manifesto imutavel atual: {relative}"
                    )
            continue

        if relative.startswith("installer/"):
            raise ManagerError(
                "novos pacotes do instalador exigem o fluxo de release imutavel"
            )

        authorities = 0
        if artifact_authority is not None:
            artifact_package, _, artifact = artifact_authority
            if entry.get("package") != artifact_package:
                raise ManagerError(
                    "package do arquivo gerenciado diverge da release proposta: "
                    f"{relative}"
                )
            require_remote_identity(
                plan, artifact, label="release proposta", url_field="url",
            )
            authorities += 1
        source_record = preserved_source(relative)
        if source_record is not None:
            require_remote_identity(
                plan, source_record, label="registro de upstream", url_field="url",
            )
            authorities += 1
        if isinstance(package, dict) and package.get("distribution_path") == relative:
            require_remote_identity(
                plan, package, label="pacote publico proposto", url_field="origin_url",
            )
            authorities += 1
        if component_name == "nquake" and reference_package is not None:
            authorities += 1
        if authorities == 0:
            raise ManagerError(
                f"arquivo remoto nao esta vinculado a release, upstream ou pacote proposto: {relative}"
            )

    if reference_transition is not None:
        _, proposed_revision = reference_transition
        proposed_prefix = f"dist/distributions/nquake/{proposed_revision}/"
        if not any(
            isinstance(plan.get("destination"), str)
            and str(plan["destination"]).startswith(proposed_prefix)
            for plan in plans
        ):
            raise ManagerError(
                "transicao da referencia nQuake precisa incorporar o novo snapshot"
            )

    if package is not None:
        assert isinstance(package, dict)
        distribution_path = package.get("distribution_path")
        if isinstance(distribution_path, str) and distribution_path in current_files:
            raise ManagerError(
                "um pacote proposto nao pode reutilizar caminho ja registrado no manifesto"
            )
        matching_plan = next(
            (
                plan for plan in plans
                if isinstance(distribution_path, str)
                and plan["destination"] == f"dist/{distribution_path}"
            ),
            None,
        )
        if matching_plan is None:
            raise ManagerError("o pacote declarado precisa corresponder a um arquivo da definicao")
        if package["size"] != matching_plan["size"] or package["sha256"] != matching_plan["sha256"]:
            raise ManagerError("o pacote declarado diverge dos pins do arquivo da definicao")
        matching_entry = matching_plan["entry"]
        assert isinstance(matching_entry, dict)
        if matching_entry.get("url") != package["origin_url"]:
            raise ManagerError("a origem do pacote diverge da URL do arquivo da definicao")
        if matching_entry.get("managed") is not True:
            raise ManagerError("o pacote declarado precisa corresponder a arquivo gerenciado")
        if matching_entry.get("distribution_component") != package["component"]:
            raise ManagerError("componente do pacote diverge do dono do arquivo gerenciado")
        expected_public_package = (
            matching_entry.get("package")
            or matching_entry["distribution_component"]
        )
        public_package = package.get("package", package["component"])
        if public_package != expected_public_package:
            raise ManagerError(
                "identidade logica do pacote publico diverge do arquivo gerenciado: "
                f"{public_package} != {expected_public_package}"
            )
        package_entries = current_catalog.get("packages")
        if not isinstance(package_entries, list):
            raise ManagerError("catalogo publico atual nao possui pacotes validos")
        identity = tuple(
            package.get(key)
            for key in ("component", "version", "channel", "platform", "architecture")
        )
        if any(
            isinstance(item, dict)
            and tuple(
                item.get(key)
                for key in ("component", "version", "channel", "platform", "architecture")
            ) == identity
            for item in package_entries
        ):
            raise ManagerError(f"a identidade de pacote ja existe: {identity}")
        proposed_catalog = {
            **current_catalog,
            "packages": [*package_entries, package],
        }
        try:
            validate_catalog(proposed_catalog)
        except ValueError as error:
            raise ManagerError(f"catalogo publico proposto invalido: {error}") from error
    return plans


def fetch_definition_file(
    entry: dict[str, object],
    base: Path,
    destination: Path,
    *,
    validated_plan: dict[str, object] | None = None,
) -> tuple[int, str]:
    plan = validated_plan if validated_plan is not None else validate_definition_file(entry, base)
    if plan.get("entry") is not entry:
        raise ManagerError("plano de arquivo nao corresponde a definicao validada")
    source = plan.get("source")
    url = plan.get("url")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(source, Path):
        origin = source
        planned_size = plan["size"]
        planned_sha256 = plan["sha256"]
        planned_mode = plan["mode"]
        assert isinstance(planned_size, int)
        assert isinstance(planned_sha256, str)
        assert isinstance(planned_mode, int)
        size, digest = copy_local_file_atomically(
            origin,
            destination,
            expected_size=planned_size,
            expected_sha256=planned_sha256,
            mode=planned_mode,
        )
    elif isinstance(url, str):
        expected_size = plan.get("size")
        expected_sha = plan.get("sha256")
        if (
            not isinstance(expected_size, int)
            or expected_size < 0
            or not isinstance(expected_sha, str)
            or not HEX64.fullmatch(expected_sha)
        ):
            raise ManagerError(
                "arquivo remoto da definição exige size e sha256 válidos"
            )
        try:
            download(PinnedArtifact(
                url=url,
                destination=destination,
                expected_size=expected_size,
                expected_sha256=expected_sha,
                maximum_size=MAX_ARTIFACT_BYTES,
                deadline_seconds=120,
                headers={"User-Agent": "x86qw-maintenance/1"},
                label=str(entry.get("destination", destination.name)),
            ))
        except DownloadError as error:
            raise ManagerError(f"falha ao baixar arquivo da definição: {error}") from error
        size = destination.stat().st_size
        digest = file_sha256(destination)
    else:
        raise ManagerError("URL de arquivo invalida")
    expected_size = plan.get("size")
    expected_sha = plan.get("sha256")
    if expected_size != size:
        raise ManagerError(f"tamanho inesperado para {entry.get('destination')}")
    if not isinstance(expected_sha, str) or not HEX64.fullmatch(expected_sha) or expected_sha != digest:
        raise ManagerError(f"SHA-256 inesperado para {entry.get('destination')}")
    return size, digest


def upsert_component(catalog: dict[str, object], component: dict[str, object], replace: bool) -> None:
    entries = catalog.get("components")
    identifier = component.get("id")
    if not isinstance(entries, list) or not isinstance(identifier, str):
        raise ManagerError("componente da definicao invalido")
    existing = [index for index, item in enumerate(entries) if isinstance(item, dict) and item.get("id") == identifier]
    if existing and not replace:
        raise ManagerError(f"o componente {identifier} ja existe; declare replace: true para atualiza-lo")
    if existing:
        entries[existing[0]] = component
    else:
        entries.append(component)
    entries.sort(key=lambda item: str(item.get("id", "")))


def preserve_profile_fingerprints(catalog: dict[str, object]) -> None:
    profiles = catalog.get("profiles")
    history = catalog.get("profile_history")
    if not isinstance(profiles, dict) or not isinstance(history, dict) or set(profiles) != set(history):
        raise ManagerError("historico de perfis de componentes invalido")
    for name, selected in profiles.items():
        fingerprints = history.get(name)
        if not isinstance(selected, list) or not isinstance(fingerprints, list):
            raise ManagerError(f"historico do perfil {name} invalido")
        fingerprint = profile_fingerprint(selected)
        if fingerprint not in fingerprints:
            fingerprints.append(fingerprint)


def command_add(options: argparse.Namespace) -> int:
    definition_path = options.definition.resolve()
    definition = load_json(definition_path)
    plans = validate_distribution_change(definition, definition_path)
    files = definition["files"]
    assert isinstance(files, list)
    if options.dry_run:
        print(f"[PLANO] Definicao valida: {len(plans)} arquivo(s).")
        for plan in plans:
            print(f"  {plan['destination']} | {plan['origin']}")
        print("[OK] Simulacao concluida; nenhum byte remoto foi baixado e nenhum arquivo foi alterado.")
        return 0
    require_clean_worktree()
    confirm(f"Incorporar {len(files)} arquivo(s) e atualizar os inventarios?", yes=options.yes)
    work = prepare_workspace(MAINTENANCE / "build")
    try:
        staged_components = load_json(work / "components.json")
        staged_releases = load_json(work / "component-releases.json")
        staged_catalog = load_json(work / "catalog.json")
        reference_transition = apply_reference_transition(
            staged_releases, definition.get("reference"),
        )
        component = definition.get("component")
        if component is not None:
            if not isinstance(component, dict):
                raise ManagerError("component precisa ser um objeto")
            preserve_profile_fingerprints(staged_components)
            upsert_component(staged_components, component, bool(definition.get("replace")))
            identifier = str(component["id"])
            profiles = definition.get("profiles", [])
            if not isinstance(profiles, list) or not all(isinstance(item, str) for item in profiles):
                raise ManagerError("profiles precisa ser uma lista")
            profile_map = staged_components.get("profiles")
            if not isinstance(profile_map, dict):
                raise ManagerError("perfis de componentes invalidos")
            for profile in profiles:
                members = profile_map.get(profile)
                if not isinstance(members, list):
                    raise ManagerError(f"perfil desconhecido: {profile}")
                if identifier not in members:
                    members.append(identifier)
            preserve_profile_fingerprints(staged_components)
            release = definition.get("release")
            release_entries = staged_releases.get("components")
            if not isinstance(release, dict) or not isinstance(release_entries, dict):
                raise ManagerError("todo componente novo ou substituido precisa declarar release")
            release_entries[identifier] = release

        manifest = load_manifest(work / "dist/manifest.json")
        manifest_files = manifest["files"]
        assert isinstance(manifest_files, dict)
        if reference_transition is not None:
            previous_revision, _ = reference_transition
            previous_prefix = f"distributions/nquake/{previous_revision}/"
            previous_snapshot = (
                work / "dist/distributions/nquake" / previous_revision
            )
            if (
                not previous_snapshot.is_dir()
                or previous_snapshot.is_symlink()
            ):
                raise ManagerError(
                    "snapshot nQuake anterior ausente ou inseguro no workspace"
                )
            shutil.rmtree(previous_snapshot)
            for relative in tuple(manifest_files):
                if relative.startswith(previous_prefix):
                    del manifest_files[relative]
        for index, raw in enumerate(files):
            if not isinstance(raw, dict):
                raise ManagerError("entrada de arquivo invalida")
            destination = safe_relative(raw.get("destination"), "dist")
            relative = PurePosixPath(destination).relative_to("dist").as_posix()
            target = work / "dist" / relative
            size, digest = fetch_definition_file(
                raw,
                definition_path.parent,
                target,
                validated_plan=plans[index],
            )
            if raw.get("managed") is True:
                component_name = raw.get("distribution_component")
                consumer = raw.get("consumer")
                url = raw.get("url")
                if not all(isinstance(value, str) and value for value in (component_name, consumer, url)):
                    raise ManagerError("arquivo upstream gerenciado exige distribution_component, consumer e url")
                metadata: dict[str, object] = {
                    "component": component_name,
                    "consumer": consumer,
                    "url": url,
                    "size": size,
                    "sha256": digest,
                }
                if isinstance(raw.get("package"), str):
                    metadata["package"] = raw["package"]
                manifest_files[relative] = metadata
        write_manifest(work / "dist/manifest.json", manifest)

        package = definition.get("package")
        if package is not None:
            validate_package(package, "definition.package")
            assert isinstance(package, dict)
            artifact = work / "dist" / str(package.get("distribution_path", ""))
            if not artifact.is_file() or artifact.stat().st_size != package["size"] or file_sha256(artifact) != package["sha256"]:
                raise ManagerError("o pacote declarado nao corresponde ao arquivo incorporado")
            package_entries = staged_catalog.get("packages")
            if not isinstance(package_entries, list):
                raise ManagerError("catalogo de pacotes invalido")
            identity = tuple(package.get(key) for key in ("component", "version", "channel", "platform", "architecture"))
            if any(isinstance(item, dict) and tuple(item.get(key) for key in (
                "component", "version", "channel", "platform", "architecture",
            )) == identity for item in package_entries):
                raise ManagerError(f"a identidade de pacote ja existe: {identity}")
            package_entries.append(package)
            staged_catalog["generated_at"] = now()

        write_json(work / "components.json", staged_components)
        write_json(work / "component-releases.json", staged_releases)
        write_json(work / "catalog.json", staged_catalog)
        validate_staged(
            work / "dist", work / "components.json", work / "component-releases.json",
            work / "catalog.json", work / "recipes",
        )
        package_manifest = build_packages(
            work / "dist", work / "packages",
            component_catalog=work / "components.json",
            component_releases=work / "component-releases.json",
        )
        register_packages(work / "catalog.json", package_manifest)
        apply_workspace(work)
        print("[OK] Arquivos incorporados, inventarios validados e pacotes reconstruidos.")
    finally:
        if work.exists():
            shutil.rmtree(work)
    print("[PROXIMO] Execute './maintenance/manage.py verify' e revise 'git diff'.")
    return 0


def github_release_coordinates(url: str, filename: str) -> tuple[str, str]:
    match = re.fullmatch(
        r"https://github\.com/([^/]+/[^/]+)/releases/download/([^/]+)/([^/]+)",
        url,
    )
    if match is None or match.group(3) != filename:
        raise ManagerError(
            "URL primaria nao representa um GitHub Release: "
            f"{safe_url_for_log(url)}"
        )
    return match.group(1), match.group(2)


def github_api_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "x86qw-maintenance/1",
    }
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token is None:
        try:
            result = subprocess.run(
                ["gh", "auth", "token"],
                cwd=PROJECT_ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result is not None and result.returncode == 0:
            token = result.stdout.strip()
    if token:
        if len(token) > 4096 or any(ord(character) < 33 or ord(character) == 127 for character in token):
            raise ManagerError("token GitHub inválido")
        headers["Authorization"] = f"Bearer {token}"
    return headers


def github_api_object(repository: str, endpoint: str, label: str) -> dict[str, object] | None:
    repository_parts = repository.split("/")
    if (
        len(repository_parts) != 2
        or any(part in {"", ".", ".."} for part in repository_parts)
        or re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None
    ):
        raise ManagerError(f"repositório GitHub inválido: {repository!r}")
    url = f"https://api.github.com/repos/{repository}/{endpoint}"
    try:
        result = download(BoundedMetadata(
            url=url,
            maximum_size=GITHUB_API_MAX_BYTES,
            deadline_seconds=GITHUB_API_DEADLINE_SECONDS,
            headers=github_api_headers(),
            label=label,
        ))
    except DownloadHTTPError as error:
        if error.status == 404:
            return None
        raise ManagerError(f"falha ao consultar {label}: HTTP {error.status}") from error
    except DownloadError as error:
        raise ManagerError(f"falha ao consultar {label}: {error}") from error
    try:
        value = json.loads((result.data or b"").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ManagerError(f"resposta JSON inválida ao consultar {label}") from error
    return value if isinstance(value, dict) else None


def github_release(repository: str, tag: str) -> dict[str, object] | None:
    encoded_tag = urllib.parse.quote(tag, safe="")
    return github_api_object(repository, f"releases/tags/{encoded_tag}", f"release GitHub {tag}")


def github_latest_release_tag(repository: str) -> str | None:
    release = github_api_object(repository, "releases/latest", f"latest GitHub de {repository}")
    if release is None:
        return None
    tag = release.get("tag_name")
    return tag if isinstance(tag, str) and tag else None


def publish_github(catalog: dict[str, object], *, dry_run: bool) -> None:
    packages = catalog["packages"]
    assert isinstance(packages, list)
    releases: dict[tuple[str, str], list[tuple[dict[str, object], str]]] = {}
    for package in packages:
        assert isinstance(package, dict)
        urls = package["urls"]
        assert isinstance(urls, list)
        github_mirror: tuple[str, str, str] | None = None
        for url in urls:
            try:
                repository, tag = github_release_coordinates(str(url), str(package["filename"]))
                github_mirror = repository, tag, str(url)
                break
            except ManagerError:
                continue
        if github_mirror is None:
            raise ManagerError(f"pacote sem mirror GitHub: {package['filename']}")
        repository, tag, primary = github_mirror
        releases.setdefault((repository, tag), []).append(
            (package, primary)
        )
    latest_by_repository = {
        repository: github_latest_release_tag(repository)
        for repository, _ in releases
    }
    for (repository, tag), artifacts in releases.items():
        titles = {
            str(package.get("mirror_title", package.get("release_title", f"x86QW Content · {tag}")))
            for package, _ in artifacts
        }
        notes = {
            str(package.get("mirror_notes", "Pacotes versionados da distribuição x86QW."))
            for package, _ in artifacts
        }
        latest_values = {
            bool(package.get(
                "mirror_latest",
                package.get("component") == "installer" and package.get("current") is True,
            ))
            for package, _ in artifacts
        }
        if len(titles) != 1 or len(notes) != 1 or len(latest_values) != 1:
            raise ManagerError(f"release {tag} possui metadados de espelho conflitantes")
        title = titles.pop()
        release_notes = notes.pop()
        make_latest = latest_values.pop()
        release = github_release(repository, tag)
        if release is None:
            print(f"[GITHUB {repository}] criar release {tag} ({title})")
            if not dry_run:
                run([
                    "gh", "release", "create", tag, "--repo", repository,
                    "--title", title, "--notes", release_notes,
                    "--latest" if make_latest else "--latest=false",
                ])
                release = github_release(repository, tag)
                latest_by_repository[repository] = tag if make_latest else latest_by_repository[repository]
        else:
            is_latest = latest_by_repository[repository] == tag
            if (
                release.get("name") != title
                or release.get("body") != release_notes
                or is_latest != make_latest
            ):
                print(f"[GITHUB {repository}] atualizar metadados de {tag}")
                if not dry_run:
                    run([
                        "gh", "api", "--method", "PATCH",
                        f"repos/{repository}/releases/{release['id']}",
                        "-f", f"name={title}", "-f", f"body={release_notes}",
                        "-f", f"make_latest={'true' if make_latest else 'false'}",
                    ])
                    latest_by_repository[repository] = tag if make_latest else (
                        None if is_latest else latest_by_repository[repository]
                    )
        if release is None and not dry_run:
            raise ManagerError(f"release GitHub {tag} nao ficou disponivel apos a criacao")
        remote_assets = {
            item.get("name"): item for item in (release or {}).get("assets", []) if isinstance(item, dict)
        }
        for package, primary in artifacts:
            remote = remote_assets.get(package["filename"])
            if remote is not None:
                digest = remote.get("digest")
                fingerprint = (
                    (remote.get("size"), str(digest).removeprefix("sha256:"))
                    if isinstance(digest, str)
                    else remote_sha256(
                        primary, int(package["size"]), str(package["sha256"]),
                    )
                )
                if fingerprint != (package["size"], package["sha256"]):
                    raise ManagerError(f"asset GitHub imutavel difere do catalogo: {package['filename']}")
                print(f"[GITHUB {repository}] verificado {package['filename']}")
                continue
            print(f"[GITHUB {repository}] enviar {package['filename']}")
            if dry_run:
                continue
            path = local_artifact(package, DIST, BUILDS)
            run(["gh", "release", "upload", tag, str(path), "--repo", repository])
            release = github_release(repository, tag)
            if release is None:
                raise ManagerError(f"release GitHub {tag} desapareceu apos o upload")
            remote_assets = {
                item.get("name"): item
                for item in release.get("assets", [])
                if isinstance(item, dict)
            }
            remote = remote_assets.get(package["filename"])
            if remote is None:
                raise ManagerError(f"asset GitHub nao apareceu apos o upload: {package['filename']}")
            digest = remote.get("digest")
            fingerprint = (
                (remote.get("size"), str(digest).removeprefix("sha256:"))
                if isinstance(digest, str)
                else remote_sha256(primary, int(package["size"]), str(package["sha256"]))
            )
            if fingerprint != (package["size"], package["sha256"]):
                raise ManagerError(f"asset GitHub enviado difere do catalogo: {package['filename']}")


def command_publish(options: argparse.Namespace) -> int:
    catalog = load_json(CATALOG)
    validate_catalog(catalog)
    # Publication consumes bytes that were built and approved elsewhere.  A
    # missing local build directory is not permission to create a new one:
    # doing so would make the bytes published depend on the publisher's
    # checkout and would violate the candidate's build-once contract.
    if not options.gitlab_only:
        publish_github(catalog, dry_run=options.dry_run)
    if not options.github_only:
        if options.dry_run:
            packages = catalog["packages"]
            assert isinstance(packages, list)
            print(f"[GITLAB] verificar/publicar {len(packages)} artefato(s)")
        else:
            command = [
                sys.executable, str(MAINTENANCE / "tools/publish_gitlab_packages.py"),
                "--catalog", str(CATALOG), "--dist", str(DIST), "--builds", str(BUILDS),
            ]
            command.extend(["--publish", "--register"])
            run(command)
    print("[OK] Publicacao concluida." if not options.dry_run else "[OK] Simulacao de publicacao concluida.")
    return 0


def command_commit(options: argparse.Namespace) -> int:
    status = run(["git", "status", "--porcelain"], capture=True).stdout.strip()
    if not status:
        print("[OK] Nao ha alteracoes para registrar no Git.")
        return 0
    run(["git", "add", "dist", "maintenance/inventory", "maintenance/recipes", "site/public/api/v1/catalog.json"])
    staged = run(["git", "diff", "--cached", "--name-only"], capture=True).stdout.strip()
    if not staged:
        raise ManagerError("nao ha mudancas de distribuicao para commit")
    message = options.message or "chore(distribution): update preserved upstreams"
    run(["git", "commit", "-m", message])
    if options.push:
        run(["git", "push", "origin", "HEAD"])
        remotes = run(["git", "remote"], capture=True).stdout.split()
        if "gitlab" in remotes:
            run(["git", "push", "gitlab", "HEAD"])
    print("[OK] Alteracoes da distribuicao registradas no Git.")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Fonte unica para verificar, atualizar, ampliar, montar e publicar o dist x86QW."
    )
    root.add_argument("--verbose", action="store_true", help="mostra traceback em caso de erro")
    commands = root.add_subparsers(dest="command", required=True)

    check = commands.add_parser("check", help="consulta upstreams sem alterar arquivos")
    check.add_argument("--offline", action="store_true", help="valida somente versoes declaradas")
    check.add_argument("--json", action="store_true", help="saida estruturada")
    check.set_defaults(handler=command_check)

    update = commands.add_parser("update", help="atualiza dist, inventarios, receitas e catalogo em conjunto")
    update.add_argument("--workers", type=int, default=8)
    update.add_argument("--dry-run", action="store_true")
    update.add_argument("--yes", action="store_true")
    update.add_argument("--commit", action="store_true", help="cria commit apos atualizar")
    update.add_argument("--push", action="store_true", help="envia o commit para origin e gitlab")
    update.add_argument("--message", help="mensagem do commit")
    update.set_defaults(handler=command_update)

    add = commands.add_parser("add", help="incorpora pacote/configuracao por definicao revisada")
    add.add_argument("definition", type=Path)
    add.add_argument("--dry-run", action="store_true")
    add.add_argument("--yes", action="store_true")
    add.set_defaults(handler=command_add)

    verify = commands.add_parser("verify", help="valida dist, inventarios, catalogo, estrutura e testes")
    verify.add_argument("--no-tests", action="store_true")
    verify.set_defaults(handler=command_verify)

    build = commands.add_parser("build", help="monta pacotes temporarios a partir do dist")
    build.add_argument("--register", action="store_true", help="atualiza o catalogo com os builds")
    build.add_argument("--project-ref", help="SHA-1 do commit x86QW ligado ao ownership do candidato")
    build.set_defaults(handler=command_build)

    publish = commands.add_parser("publish", help="publica os artefatos nos mirrors GitHub e GitLab")
    mirror = publish.add_mutually_exclusive_group()
    mirror.add_argument("--github-only", action="store_true")
    mirror.add_argument("--gitlab-only", action="store_true")
    publish.add_argument("--dry-run", action="store_true")
    publish.set_defaults(handler=command_publish)

    commit = commands.add_parser("commit", help="registra no Git somente mudancas da distribuicao")
    commit.add_argument("--message")
    commit.add_argument("--push", action="store_true")
    commit.set_defaults(handler=command_commit)
    return root


def main() -> int:
    arguments = parser().parse_args()
    return int(arguments.handler(arguments))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        DownloadError,
        ManagerError,
        OSError,
        ValueError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
    ) as error:
        print(f"[ERRO] {error}", file=sys.stderr)
        if "--verbose" in sys.argv:
            raise
        raise SystemExit(1)
