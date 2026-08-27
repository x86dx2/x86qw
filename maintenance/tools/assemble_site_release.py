#!/usr/bin/env python3
"""Assemble an immutable public-site tree from candidate bytes and staged TUF."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from .validate_catalog import validate_catalog
except ImportError:  # Execucao direta
    from validate_catalog import validate_catalog

from x86qw_runtime.io.metadata import read_bounded_regular_file


MAX_PUBLIC_JSON = 2 * 1024 * 1024


class SiteAssemblyError(RuntimeError):
    """The public site cannot be assembled from one candidate generation."""


def _regular(path: Path, label: str, maximum: int | None = None) -> bytes:
    try:
        payload = read_bounded_regular_file(path, maximum_size=maximum or MAX_PUBLIC_JSON)
    except OSError as error:
        raise SiteAssemblyError(f"{label} ausente, inseguro ou grande demais: {path}") from error
    return payload


def _json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(_regular(path, label).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise SiteAssemblyError(f"{label} não é JSON válido") from error
    if not isinstance(value, dict):
        raise SiteAssemblyError(f"{label} precisa ser objeto JSON")
    return value


def _copy_tree(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_dir():
        raise SiteAssemblyError(f"site fonte ausente ou inseguro: {source}")
    if destination.exists():
        if destination.is_symlink() or not destination.is_dir():
            raise SiteAssemblyError(f"destino do site inseguro: {destination}")
    else:
        destination.mkdir(parents=True, exist_ok=False)
    for path in sorted(source.rglob("*")):
        relative = PurePosixPath(*path.relative_to(source).parts)
        if any(part in {"", ".", ".."} for part in relative.parts):
            raise SiteAssemblyError(f"caminho do site inválido: {relative}")
        target = destination.joinpath(*relative.parts)
        if path.is_symlink():
            raise SiteAssemblyError(f"site fonte contém symlink: {relative}")
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=False)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        else:
            raise SiteAssemblyError(f"site fonte contém tipo especial: {relative}")


def _write_new(path: Path, payload: bytes) -> None:
    if os.path.lexists(path):
        raise SiteAssemblyError(f"destino do site já contém entrada: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _published_root_version(repository: Path) -> int:
    metadata = Path(repository) / "metadata"
    versions = []
    for path in sorted(metadata.glob("*.root.json")):
        encoded_version = path.name.removesuffix(".root.json")
        if not encoded_version.isdecimal():
            raise SiteAssemblyError(f"root TUF possui nome inválido: {path.name}")
        version = int(encoded_version)
        document = _json(path, "root TUF versionada")
        signed = document.get("signed")
        signed_version = signed.get("version") if isinstance(signed, dict) else None
        if isinstance(signed_version, bool) or signed_version != version:
            raise SiteAssemblyError(f"root TUF diverge do nome versionado: {path.name}")
        versions.append(version)
    ordered_versions = sorted(versions)
    if not ordered_versions or ordered_versions != list(range(1, max(ordered_versions) + 1)):
        raise SiteAssemblyError("cadeia de roots TUF precisa ser contínua a partir da v1")
    return max(ordered_versions)


def assemble_site_release(
    *,
    site_source: Path,
    catalog: Path,
    product: Path,
    trust_repository: Path,
    output: Path,
) -> dict[str, object]:
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise SiteAssemblyError(f"destino do site já existe: {output}")
    catalog_bytes = _regular(Path(catalog), "catálogo candidato")
    catalog_document = _json(Path(catalog), "catálogo candidato")
    try:
        validate_catalog(catalog_document)
    except (TypeError, ValueError) as error:
        raise SiteAssemblyError("catálogo candidato inválido") from error
    product_bytes = _regular(Path(product), "product candidato")
    product_document = _json(Path(product), "product candidato")
    installer_versions = [
        package.get("version")
        for package in catalog_document.get("packages", [])
        if isinstance(package, dict)
        and package.get("component") == "installer"
        and package.get("current") is True
    ]
    if (
        product_document.get("project") != "x86qw"
        or len(installer_versions) != 1
        or product_document.get("version") != installer_versions[0]
    ):
        raise SiteAssemblyError("product e catálogo não representam a mesma versão current")
    if trust_repository.is_symlink() or not trust_repository.is_dir():
        raise SiteAssemblyError("repositório TUF staged ausente ou inseguro")
    root_version = _published_root_version(trust_repository)

    parent = output.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=parent))
    try:
        _copy_tree(Path(site_source), staging)
        _write_new(staging / "api/v1/catalog.json.tmp", catalog_bytes)
        (staging / "api/v1/catalog.json.tmp").replace(staging / "api/v1/catalog.json")
        _write_new(staging / "api/v1/product.json.tmp", product_bytes)
        (staging / "api/v1/product.json.tmp").replace(staging / "api/v1/product.json")
        release_truth_path = staging / "api/v1/release-truth.json"
        release_truth = _json(release_truth_path, "verdade de release")
        try:
            tuf = release_truth["authorities"]["deployment"]["tuf"]
            projected_root_version = tuf["root_version"]
        except (KeyError, TypeError) as error:
            raise SiteAssemblyError("verdade de release não declara a root TUF") from error
        if isinstance(projected_root_version, bool) or not isinstance(projected_root_version, int):
            raise SiteAssemblyError("verdade de release declara root TUF inválida")
        if projected_root_version != root_version:
            tuf["root_version"] = root_version
            release_truth_bytes = (
                json.dumps(release_truth, ensure_ascii=False, indent=2) + "\n"
            ).encode("utf-8")
            _write_new(staging / "api/v1/release-truth.json.tmp", release_truth_bytes)
            (staging / "api/v1/release-truth.json.tmp").replace(release_truth_path)
        trust_destination = staging / "api/v1/trust"
        if trust_destination.is_symlink():
            trust_destination.unlink()
        elif trust_destination.exists():
            if trust_destination.is_dir():
                shutil.rmtree(trust_destination)
            else:
                trust_destination.unlink()
        _copy_tree(Path(trust_repository), trust_destination)
        os.replace(staging, output)
        staging = None
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)
    return {
        "format": 1,
        "project": "x86qw",
        "status": "assembled",
        "catalog_size": len(catalog_bytes),
        "product_size": len(product_bytes),
        "root_version": root_version,
        "output": os.fspath(output),
    }


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-source", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--product", type=Path, required=True)
    parser.add_argument("--trust-repository", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args(arguments)
    try:
        result = assemble_site_release(
            site_source=options.site_source,
            catalog=options.catalog,
            product=options.product,
            trust_repository=options.trust_repository,
            output=options.output,
        )
    except (OSError, SiteAssemblyError) as error:
        print(f"[ERRO] {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
