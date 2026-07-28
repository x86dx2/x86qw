#!/usr/bin/env python3
"""Validate the x86QW component catalog and an optional nQuake reference snapshot."""

from __future__ import annotations

import argparse
from pathlib import Path

from components import load_catalog, validate_tree_partition
from component_releases import load_releases, verified_artifact_members, verified_package_files


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / "inventory/components.json")
    parser.add_argument("--releases", type=Path, default=ROOT / "inventory/component-releases.json")
    parser.add_argument("--snapshot", type=Path, help="diretório raiz de um snapshot nQuake")
    parser.add_argument("--distribution", type=Path, help="valida também os artefatos preservados em dist")
    arguments = parser.parse_args()
    catalog = load_catalog(arguments.catalog)
    releases = load_releases(arguments.releases, arguments.catalog)
    for component in catalog["components"]:
        for source in component.get("project_sources", []):
            path = ROOT / source["path"]
            if not path.is_file() or path.is_symlink() or not path.stat().st_size:
                raise ValueError(f"fonte x86QW inválida: {source['path']}")
    if arguments.distribution:
        release_components = releases["components"]
        assert isinstance(release_components, dict)
        for release in release_components.values():
            assert isinstance(release, dict)
            for artifact in release.get("artifacts", []):
                if release["strategy"] == "upstream-package":
                    verified_package_files(arguments.distribution, artifact)
                else:
                    verified_artifact_members(arguments.distribution, artifact)
    if arguments.snapshot:
        paths = sorted(path.relative_to(arguments.snapshot).as_posix() for path in arguments.snapshot.rglob("*") if path.is_file())
        partition = validate_tree_partition(catalog, paths)
        print(
            f"Catálogo válido: {len(catalog['components'])} componentes, "
            f"{len(paths)} arquivos de referência atribuídos e versões verificadas."
        )
    else:
        print(f"Catálogo válido: {len(catalog['components'])} componentes e versões verificadas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
