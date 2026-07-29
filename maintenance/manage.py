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
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from maintenance.tools.build_component_packages import build_packages, register_packages
from maintenance.tools.build_package import verify_artifact
from maintenance.tools.check_component_updates import check_updates
from maintenance.tools.component_releases import load_releases
from maintenance.tools.component_sources import discover_snapshot
from maintenance.tools.components import (
    load_catalog as load_component_catalog,
    profile_fingerprint,
    validate_tree_partition,
)
from maintenance.tools.publish_gitlab_packages import artifact_url, local_artifact, remote_sha256
from maintenance.tools.public_upstreams import github_commit_revision
from maintenance.tools.sync_distribution import (
    Asset,
    discover_assets,
    download_asset,
    file_sha256,
    load_manifest,
    verify_distribution,
    write_manifest,
)
from maintenance.tools.validate_catalog import PACKAGE_FIELDS, validate_catalog, validate_package
from maintenance.tools.validate_recipes import recipe_paths, validate_recipe
from maintenance.tools.upstreams import load_upstreams, verify_preserved_sources


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
PRIMARY_GITHUB_REPOSITORY = "x86dx2/x86qw"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
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

    shutil.copytree(source, destination, copy_function=link_or_copy)


def safe_relative(value: object, prefix: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\0" in value:
        raise ManagerError(f"caminho invalido: {value!r}")
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ManagerError(f"caminho inseguro: {value}")
    if relative.parts[0] != prefix:
        raise ManagerError(f"o caminho precisa ficar em {prefix}/: {value}")
    return relative.as_posix()


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
        delta = distribution_delta(assets, manifest)
    outdated = [item for item in component_results if item["status"] != "current"]
    if options.json:
        print(json.dumps({
            "components": component_results,
            "distribution": delta,
            "reference": reference_note,
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
        print(f"\n{len(outdated)} componente(s) e {len(delta)} arquivo(s) envolvidos em mudancas.")
    return 2 if outdated or delta else 0


def command_verify(options: argparse.Namespace) -> int:
    catalog = load_json(CATALOG)
    package_count = validate_catalog(catalog)
    component_catalog = load_component_catalog(COMPONENTS)
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
        f"receitas: {recipe_count}; arquivos upstream: {upstream_count}; fontes: {source_count}; "
        f"nQuake: {revision[:12]}."
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
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
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
    try:
        manifest = build_packages(DIST, temporary)
        if BUILDS.exists():
            shutil.rmtree(BUILDS)
        os.replace(temporary, BUILDS)
        if options.register:
            register_packages(CATALOG, manifest)
        print(f"[OK] {len(manifest['packages'])} pacote(s) gerado(s) em {BUILDS}.")
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
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
    delta = distribution_delta(assets, current_manifest)
    if not delta and new_reference == old_reference:
        print("[OK] O dist e os inventarios ja representam os upstreams atuais.")
        return 0
    print(f"[INFO] {len(delta)} alteracao(oes) de arquivo; nQuake {old_reference[:12]} -> {new_reference[:12]}.")
    if options.dry_run:
        for summary in summarize_delta(delta):
            print(f"  - {summary}")
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


def fetch_definition_file(entry: dict[str, object], base: Path, destination: Path) -> tuple[int, str]:
    source = entry.get("source")
    url = entry.get("url")
    if bool(source) == bool(url):
        raise ManagerError("cada arquivo da definicao deve possuir exatamente source ou url")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(source, str):
        origin = (base / source).resolve()
        if not origin.is_file() or origin.is_symlink():
            raise ManagerError(f"arquivo local invalido: {origin}")
        shutil.copyfile(origin, destination)
    elif isinstance(url, str) and url.startswith("https://"):
        request = urllib.request.Request(url, headers={"User-Agent": "x86qw-maintenance/1"})
        with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as output:
            shutil.copyfileobj(response, output)
    else:
        raise ManagerError(f"URL de arquivo invalida: {url!r}")
    size = destination.stat().st_size
    digest = file_sha256(destination)
    expected_size = entry.get("size")
    expected_sha = entry.get("sha256")
    if expected_size is not None and expected_size != size:
        raise ManagerError(f"tamanho inesperado para {entry.get('destination')}")
    if expected_sha is not None and (not isinstance(expected_sha, str) or not HEX64.fullmatch(expected_sha) or expected_sha != digest):
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
    if definition.get("format") != 1 or definition.get("project") != "x86qw" or definition.get("kind") != "distribution-change":
        raise ManagerError("a definicao precisa ser x86qw/distribution-change formato 1")
    files = definition.get("files", [])
    if not isinstance(files, list) or not files:
        raise ManagerError("a definicao precisa incorporar ao menos um arquivo diretamente utilizado")
    if options.dry_run:
        print(json.dumps(definition, ensure_ascii=False, indent=2))
        return 0
    require_clean_worktree()
    confirm(f"Incorporar {len(files)} arquivo(s) e atualizar os inventarios?", yes=options.yes)
    work = prepare_workspace(MAINTENANCE / "build")
    try:
        staged_components = load_json(work / "components.json")
        staged_releases = load_json(work / "component-releases.json")
        staged_catalog = load_json(work / "catalog.json")
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
        for raw in files:
            if not isinstance(raw, dict):
                raise ManagerError("entrada de arquivo invalida")
            destination = safe_relative(raw.get("destination"), "dist")
            relative = PurePosixPath(destination).relative_to("dist").as_posix()
            target = work / "dist" / relative
            size, digest = fetch_definition_file(raw, definition_path.parent, target)
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
        raise ManagerError(f"URL primaria nao representa um GitHub Release: {url}")
    return match.group(1), match.group(2)


def github_release(repository: str, tag: str) -> dict[str, object] | None:
    result = subprocess.run(
        ["gh", "api", f"repos/{repository}/releases/tags/{tag}"],
        cwd=PROJECT_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        if "HTTP 404" in result.stderr or "Not Found" in result.stderr:
            return None
        raise ManagerError(result.stderr.strip() or f"falha ao consultar release {tag}")
    value = json.loads(result.stdout)
    return value if isinstance(value, dict) else None


def publish_github(catalog: dict[str, object], *, dry_run: bool) -> None:
    packages = catalog["packages"]
    assert isinstance(packages, list)
    releases: dict[tuple[str, str], list[tuple[dict[str, object], Path, str]]] = {}
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
            (package, local_artifact(package, DIST, BUILDS), primary)
        )
    for (repository, tag), artifacts in releases.items():
        release = github_release(repository, tag)
        if release is None:
            print(f"[GITHUB {repository}] criar release {tag}")
            if not dry_run:
                run(["gh", "release", "create", tag, "--repo", repository, "--title", tag, "--notes", "x86QW distribution artifact mirror."])
                release = github_release(repository, tag)
        remote_assets = {
            item.get("name"): item for item in (release or {}).get("assets", []) if isinstance(item, dict)
        }
        for package, path, primary in artifacts:
            remote = remote_assets.get(package["filename"])
            if remote is not None:
                digest = remote.get("digest")
                fingerprint = (
                    (remote.get("size"), str(digest).removeprefix("sha256:"))
                    if isinstance(digest, str)
                    else remote_sha256(primary)
                )
                if fingerprint != (package["size"], package["sha256"]):
                    raise ManagerError(f"asset GitHub imutavel difere do catalogo: {package['filename']}")
                print(f"[GITHUB {repository}] verificado {package['filename']}")
                continue
            print(f"[GITHUB {repository}] enviar {package['filename']}")
            if not dry_run:
                run(["gh", "release", "upload", tag, str(path), "--repo", repository])


def command_publish(options: argparse.Namespace) -> int:
    catalog = load_json(CATALOG)
    validate_catalog(catalog)
    if not BUILDS.exists():
        print("[INFO] Builds de componentes ausentes; gerando agora.")
        command_build(argparse.Namespace(register=False))
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
    except (ManagerError, OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError, urllib.error.URLError) as error:
        print(f"[ERRO] {error}", file=sys.stderr)
        if "--verbose" in sys.argv:
            raise
        raise SystemExit(1)
