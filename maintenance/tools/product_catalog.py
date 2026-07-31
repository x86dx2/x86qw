"""Build the public product facts from the canonical x86QW inventories."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from maintenance.tools.components import load_catalog as load_component_catalog
from maintenance.tools.runtime_catalog import load_inventory as load_runtime_inventory
from maintenance.tools.validate_catalog import validate_catalog


def build_product_catalog(project_root: Path) -> dict[str, object]:
    inventory_root = project_root / "maintenance/inventory"
    public_catalog_path = project_root / "site/public/api/v1/catalog.json"
    public_catalog = json.loads(public_catalog_path.read_text(encoding="utf-8"))
    package_count = validate_catalog(public_catalog)
    components = load_component_catalog(inventory_root / "components.json")
    inventory = load_runtime_inventory(
        inventory_root,
        component_catalog=components,
        project_root=project_root,
        public_catalog=public_catalog,
    )
    capabilities = inventory["capabilities"]
    runtimes = inventory["runtimes"]["runtimes"]
    games = inventory["games"]["games"]
    installers = [
        item for item in public_catalog["packages"]
        if item.get("package") == "x86qw-installer" and item.get("current") is True
    ]
    if len(installers) != 1:
        raise ValueError("public catalog must declare exactly one current installer")
    current = installers[0]
    version = (project_root / "dist/installer/VERSION").read_text(encoding="utf-8").strip()
    if current.get("version") != version:
        raise ValueError("installer VERSION differs from the public current package")

    projected_runtimes = []
    for runtime in runtimes:
        projected_runtimes.append({
            "id": runtime["id"],
            "label": runtime["label"],
            "kind": runtime["kind"],
            "protocols": runtime["protocols"],
            "capabilities": runtime["capabilities"],
            "component": runtime["component"],
            "platforms": [
                {
                    "system": platform["system"],
                    "architecture": platform["architecture"],
                    "variant": platform["variant"],
                    "format": platform["format"],
                    "origin": platform["origin"],
                    "test_required": platform["test_required"],
                }
                for platform in runtime["platforms"]
            ],
        })

    return {
        "format": 1,
        "project": "x86qw",
        "version": version,
        "package_count": package_count,
        "component_count": len(components["components"]),
        "commands": capabilities["commands"],
        "installer": {
            "filename": current["filename"],
            "distribution_path": current["distribution_path"],
            "sha256": current["sha256"],
            "size": current["size"],
            "urls": current["urls"],
        },
        "runtimes": projected_runtimes,
        "games": [
            {
                "id": game["id"],
                "label": game["label"],
                "version": game.get("version"),
                "protocol": game["protocol"],
                "component": game["component"],
                "client_runtimes": game["client_runtimes"],
                "server_runtimes": game["server_runtimes"],
                "smoke_test": game["smoke_test"],
            }
            for game in games
        ],
    }


def encoded_product_catalog(project_root: Path) -> bytes:
    return (
        json.dumps(
            build_product_catalog(project_root),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Gera ou valida os fatos públicos do x86QW.")
    parser.add_argument("--write", action="store_true", help="atualiza a projeção pública")
    options = parser.parse_args()
    project_root = Path(__file__).resolve().parents[2]
    destination = project_root / "site/public/api/v1/product.json"
    expected = encoded_product_catalog(project_root)
    if not options.write:
        if not destination.is_file() or destination.is_symlink() or destination.read_bytes() != expected:
            raise SystemExit("product.json diverge dos inventários canônicos; execute com --write")
        return 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".product-", suffix=".json", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(expected)
        os.replace(name, destination)
    finally:
        Path(name).unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
