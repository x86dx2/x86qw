#!/usr/bin/env python3
"""Validate and stage signed TUF metadata after asset publication.

This command never signs, creates, overwrites, or uploads metadata.  The
signing ceremony supplies a closed directory; this gate authenticates its
catalog target and copies it into a new staging directory for the subsequent
site publisher.  Missing signer output therefore fails closed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from x86qw_runtime.trust import (  # noqa: E402
    MAX_METADATA_BYTES,
    TrustError,
    load_trusted_catalog,
    validate_bootstrap_policy,
)


class TufPublicationError(RuntimeError):
    """Signed metadata is missing, invalid, or cannot be staged safely."""


class _LocalFetcher:
    def __init__(self, metadata_dir: Path, target_dir: Path) -> None:
        self.metadata_dir = Path(metadata_dir)
        self.target_dir = Path(target_dir)

    def download_bytes(self, url: str, max_length: int) -> bytes:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.query or parsed.fragment:
            raise TrustError("metadata TUF recebeu URL local inválida")
        base = self.target_dir if "/targets/" in parsed.path else self.metadata_dir
        name = Path(parsed.path).name
        if not name or name in {".", ".."}:
            raise TrustError("metadata TUF recebeu nome inválido")
        candidates = [
            path for path in base.rglob(name)
            if path.is_file() and not path.is_symlink()
        ]
        if not candidates:
            try:
                from tuf.api import exceptions
            except ImportError as error:
                raise TrustError("dependências TUF fixadas estão indisponíveis") from error
            raise exceptions.DownloadHTTPError(
                f"metadata TUF ausente: {name}", 404,
            )
        if len(candidates) != 1:
            raise TrustError(f"metadata TUF não identificou exatamente {name}")
        payload = candidates[0].read_bytes()
        if len(payload) > max_length:
            raise TrustError(f"metadata TUF excede o limite: {name}")
        return payload


def _regular_files(root: Path) -> tuple[Path, ...]:
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise TufPublicationError(f"diretório TUF ausente ou inseguro: {root}")
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise TufPublicationError(f"metadata TUF contém symlink: {path}")
        if path.is_file():
            if path.stat().st_size > MAX_METADATA_BYTES:
                raise TufPublicationError(f"metadata TUF excede o limite: {path.name}")
            files.append(path)
        elif not path.is_dir():
            raise TufPublicationError(f"metadata TUF contém tipo especial: {path}")
    return tuple(files)


def _copy_tree(source: Path, destination: Path) -> None:
    files = _regular_files(source)
    destination.mkdir(mode=0o700)
    for path in files:
        relative = path.relative_to(source)
        target = destination.joinpath(*relative.parts)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(path, target, follow_symlinks=False)
        os.chmod(target, 0o644)


def stage_tuf_metadata(
    *, metadata_dir: Path, catalog: Path, root: Path, stage_dir: Path,
) -> dict[str, object]:
    """Authenticate metadata and copy it to a new, non-overwritable stage."""

    metadata_dir = Path(metadata_dir)
    metadata_source = metadata_dir / "metadata"
    target_source = metadata_dir / "targets"
    metadata_files = _regular_files(metadata_source)
    _regular_files(target_source)
    root_bytes = Path(root).read_bytes()
    try:
        validate_bootstrap_policy(root_bytes)
    except (OSError, TrustError) as error:
        raise TufPublicationError(f"root TUF inválida: {error}") from error
    root_metadata = tuple(
        path for path in metadata_files
        if path.name.endswith(".root.json")
        and path.name.removesuffix(".root.json").isdigit()
        and int(path.name.removesuffix(".root.json")) > 0
    )
    if not root_metadata or not any(path.read_bytes() == root_bytes for path in root_metadata):
        raise TufPublicationError(
            "metadata TUF precisa incluir a root versionada correspondente à âncora"
        )
    catalog_value = json.loads(Path(catalog).read_text(encoding="utf-8"))
    if not isinstance(catalog_value, dict):
        raise TufPublicationError("catálogo final precisa ser um objeto JSON")
    try:
        with tempfile.TemporaryDirectory(prefix="x86qw-tuf-stage-verify-") as temporary:
            verified = load_trusted_catalog(
                bootstrap_root=root_bytes,
                metadata_dir=Path(temporary) / "metadata",
                target_dir=Path(temporary) / "targets",
                metadata_base_url="https://local.x86qw.invalid/trust/metadata/",
                target_base_url="https://local.x86qw.invalid/trust/targets/",
                fetcher=_LocalFetcher(metadata_source, target_source),
            )
    except (OSError, TrustError, json.JSONDecodeError) as error:
        raise TufPublicationError(f"metadata TUF não autentica o catálogo final: {error}") from error
    if verified != catalog_value:
        raise TufPublicationError("target TUF autenticado diverge do catálogo final")
    stage_dir = Path(stage_dir)
    if stage_dir.exists() or stage_dir.is_symlink():
        raise TufPublicationError(f"destino de metadata já existe: {stage_dir}")
    stage_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        stage_dir.mkdir(mode=0o700)
        _copy_tree(metadata_source, stage_dir / "metadata")
        _copy_tree(target_source, stage_dir / "targets")
    except (OSError, TufPublicationError) as error:
        if stage_dir.exists() and not stage_dir.is_symlink():
            shutil.rmtree(stage_dir)
        raise TufPublicationError(f"metadata TUF não pôde ser staged: {error}") from error
    return {
        "format": 1,
        "project": "x86qw",
        "status": "verified-staged",
        "metadata_files": len(_regular_files(stage_dir / "metadata")),
        "target_files": len(_regular_files(stage_dir / "targets")),
    }


def stage_tuf_repository(
    *, signed_repository: Path, root: Path, catalog: Path, output: Path,
) -> dict[str, object]:
    """Compatibility spelling for callers that stage a signed repository."""

    return stage_tuf_metadata(
        metadata_dir=signed_repository,
        catalog=catalog,
        root=root,
        stage_dir=output,
    )


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata-dir", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--stage-dir", type=Path, required=True)
    options = parser.parse_args(arguments)
    try:
        result = stage_tuf_metadata(
            metadata_dir=options.metadata_dir,
            catalog=options.catalog,
            root=options.root,
            stage_dir=options.stage_dir,
        )
    except (OSError, TufPublicationError, json.JSONDecodeError) as error:
        print(f"[ERRO] {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
