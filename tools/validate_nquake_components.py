#!/usr/bin/env python3
"""Validate the nQuake component catalog and an optional local snapshot."""

from __future__ import annotations

import argparse
from pathlib import Path

from nquake_components import load_catalog, validate_tree_partition
from nquake_releases import load_releases, verified_artifact_members


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / "inventory/nquake-components.json")
    parser.add_argument("--releases", type=Path, default=ROOT / "inventory/nquake-releases.json")
    parser.add_argument("--snapshot", type=Path, help="diretório raiz de um snapshot nQuake")
    parser.add_argument("--archive", type=Path, help="valida também os artefatos de release preservados")
    arguments = parser.parse_args()
    catalog = load_catalog(arguments.catalog)
    releases = load_releases(arguments.releases, arguments.catalog)
    if arguments.archive:
        release_components = releases["components"]
        assert isinstance(release_components, dict)
        for release in release_components.values():
            assert isinstance(release, dict)
            for artifact in release.get("artifacts", []):
                verified_artifact_members(arguments.archive, artifact)
    if arguments.snapshot:
        paths = sorted(path.relative_to(arguments.snapshot).as_posix() for path in arguments.snapshot.rglob("*") if path.is_file())
        partition = validate_tree_partition(catalog, paths)
        print(f"Catálogo válido: {len(partition)} componentes, {len(paths)} arquivos atribuídos e versões verificadas.")
    else:
        print(f"Catálogo válido: {len(catalog['components'])} componentes e versões verificadas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
