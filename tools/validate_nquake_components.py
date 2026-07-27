#!/usr/bin/env python3
"""Validate the nQuake component catalog and an optional local snapshot."""

from __future__ import annotations

import argparse
from pathlib import Path

from nquake_components import load_catalog, validate_tree_partition


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / "inventory/nquake-components.json")
    parser.add_argument("--snapshot", type=Path, help="diretório raiz de um snapshot nQuake")
    arguments = parser.parse_args()
    catalog = load_catalog(arguments.catalog)
    if arguments.snapshot:
        paths = sorted(path.relative_to(arguments.snapshot).as_posix() for path in arguments.snapshot.rglob("*") if path.is_file())
        partition = validate_tree_partition(catalog, paths)
        print(f"Catálogo válido: {len(partition)} componentes, {len(paths)} arquivos atribuídos.")
    else:
        print(f"Catálogo válido: {len(catalog['components'])} componentes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
