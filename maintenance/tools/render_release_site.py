#!/usr/bin/env python3
"""Materialize a release site's exact catalog and product projections."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from maintenance.tools.validate_catalog import validate_catalog


class ReleaseSiteError(ValueError):
    """The release site inputs or destination are unsafe or inconsistent."""


def _render_bootstrap(source: Path, destination: Path, record: dict[str, object]) -> None:
    """Bind a copied shell bootstrap to the candidate installer record."""

    _regular_file(source, "bootstrap do instalador")
    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ReleaseSiteError(f"bootstrap do instalador inválido: {source}") from error
    version = str(record["version"])
    digest = str(record["sha256"])
    size = str(record["size"])
    if source.name == "install.sh":
        assignments = {
            "INSTALLER_VERSION": version,
            "INSTALLER_SHA256": digest,
            "INSTALLER_SIZE": size,
        }
        patterns = {
            name: rf"(?m)^({re.escape(name)}=)[^\n]+$"
            for name in assignments
        }
    elif source.name == "install.ps1":
        assignments = {
            "$InstallerVersion": version,
            "$InstallerSha256": digest,
            "$InstallerSize": size,
        }
        patterns = {
            name: rf'(?m)^({re.escape(name)}\s*=\s*)"[^"]*"$'
            for name in assignments
        }
    else:
        raise ReleaseSiteError(f"bootstrap não suportado: {source.name}")
    for name, value in assignments.items():
        text, count = re.subn(
            patterns[name], rf'\g<1>"{value}"', text,
        )
        if count != 1:
            raise ReleaseSiteError(f"bootstrap não expõe uma atribuição única: {name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    os.chmod(destination, source.stat().st_mode & 0o777)


def _regular_file(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ReleaseSiteError(f"{label} ausente ou inseguro: {path}")
    return path


def _read_object(path: Path, label: str) -> dict[str, object]:
    _regular_file(path, label)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReleaseSiteError(f"{label} inválido") from error
    if not isinstance(value, dict):
        raise ReleaseSiteError(f"{label} precisa ser um objeto JSON")
    return value


def _copy_source(source: Path, output: Path) -> None:
    if source.is_symlink() or not source.is_dir():
        raise ReleaseSiteError(f"site fonte ausente ou inseguro: {source}")
    for path in source.rglob("*"):
        if path.is_symlink():
            raise ReleaseSiteError(f"site fonte contém symlink: {path}")
    shutil.copytree(source, output, dirs_exist_ok=False)


def render_release_site(
    *, source: Path, catalog: Path, product: Path, output: Path,
    bootstrap_source: Path | None = None,
) -> dict[str, object]:
    if output.exists() or output.is_symlink():
        raise ReleaseSiteError(f"destino do site já existe: {output}")
    catalog_value = _read_object(Path(catalog), "catálogo candidato")
    try:
        package_count = validate_catalog(catalog_value)
    except (OSError, ValueError) as error:
        raise ReleaseSiteError(f"catálogo candidato inválido: {error}") from error
    product_value = _read_object(Path(product), "produto candidato")
    installers = [
        item for item in catalog_value["packages"]
        if isinstance(item, dict)
        and item.get("package") == "x86qw-installer"
        and item.get("current") is True
    ]
    if len(installers) != 1 or product_value.get("version") != installers[0].get("version"):
        raise ReleaseSiteError("produto e catálogo candidatos divergem")
    if product_value.get("package_count") != package_count:
        raise ReleaseSiteError("produto candidato possui package_count divergente")
    installer = installers[0]
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    _copy_source(Path(source), output)
    api = output / "api/v1"
    api.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(catalog, api / "catalog.json", follow_symlinks=False)
    shutil.copyfile(product, api / "product.json", follow_symlinks=False)
    index = output / "index.html"
    html = _regular_file(index, "index.html").read_text(encoding="utf-8")
    replacements = {
        "product-version": str(product_value["version"]),
        "package-count": str(product_value["package_count"]),
        "component-count": str(product_value["component_count"]),
    }
    for name, value in replacements.items():
        patterns = (
            rf"(<span data-{re.escape(name)}>)[^<]*(</span>)",
            rf"(<span class=\"[^\"]*\brelease-{re.escape(name)}\b[^\"]*\">)[^<]*(</span>)",
        )
        count = 0
        for pattern in patterns:
            html, count = re.subn(
                pattern,
                rf"\g<1>{value}\g<2>",
                html,
            )
            if count:
                break
        if count == 0:
            raise ReleaseSiteError(
                f"index.html não expõe o marcador data-{name} ou release-{name}"
            )
    index.write_text(html, encoding="utf-8")
    if bootstrap_source is not None:
        bootstrap_source = Path(bootstrap_source)
        for name in ("install.sh", "install.ps1"):
            _render_bootstrap(
                bootstrap_source / name,
                output / name,
                installer,
            )
    return {"format": 1, "project": "x86qw", "version": product_value["version"], "package_count": package_count}


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--product", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bootstrap-source", type=Path)
    options = parser.parse_args(arguments)
    try:
        result = render_release_site(
            source=options.source,
            catalog=options.catalog,
            product=options.product,
            output=options.output,
            bootstrap_source=options.bootstrap_source,
        )
    except (OSError, ReleaseSiteError) as error:
        print(f"[ERRO] {error}")
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
