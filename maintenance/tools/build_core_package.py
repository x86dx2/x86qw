#!/usr/bin/env python3
"""Build the mandatory x86QW base-game data package outside the installer bundle."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VERSION = "1.0.0"
PACKAGE = "x86qw-core-id1"
RELEASE_TAG = f"x86qw-content-core-{VERSION}"
GITLAB_PROJECT_ID = 84856335
FIXED_ZIP_TIME = (2020, 1, 1, 0, 0, 0)
PAKS = ("pak0.pak", "pak1.pak")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_core_package(distribution: Path, output: Path) -> dict[str, object]:
    id1 = distribution / "game-data/id1"
    sources = [id1 / name for name in PAKS]
    for source in sources:
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"core game data is missing or unsafe: {source}")
        with source.open("rb") as pak:
            if pak.read(4) != b"PACK":
                raise ValueError(f"core game data is not a PAK: {source}")
    source_hashes = [(source.name, file_sha256(source)) for source in sources]
    revision = hashlib.sha256(
        "".join(f"{name}\t{digest}\n" for name, digest in source_hashes).encode("ascii")
    ).hexdigest()
    release_root = output / f"core-{VERSION}"
    release_root.mkdir(parents=True, exist_ok=True)
    filename = f"{PACKAGE}-{VERSION}.zip"
    artifact = release_root / filename
    members: list[dict[str, str]] = []
    with zipfile.ZipFile(artifact, "w", allowZip64=True) as archive:
        for source, (_, digest) in zip(sources, source_hashes, strict=True):
            member = f"payload/id1/{source.name}"
            members.append({
                "path": member,
                "sha256": digest,
                "source": f"dist/game-data/id1/{source.name}",
            })
            info = zipfile.ZipInfo(member, FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source.read_bytes())
        metadata = json.dumps({
            "format": 1,
            "project": "x86qw",
            "package": PACKAGE,
            "version": VERSION,
            "source_revision": revision,
            "members": members,
        }, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        info = zipfile.ZipInfo("_x86qw/component.json", FIXED_ZIP_TIME)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        archive.writestr(info, metadata)
    mirror = (
        f"https://github.com/x86dx2/x86qw/releases/download/{RELEASE_TAG}/{filename}"
    )
    gitlab = (
        f"https://gitlab.com/api/v4/projects/{GITLAB_PROJECT_ID}/packages/generic/"
        f"{PACKAGE}/{VERSION}/{filename}"
    )
    return {
        "component": "core",
        "package": PACKAGE,
        "version": VERSION,
        "channel": "content",
        "platform": "any",
        "architecture": "any",
        "filename": filename,
        "size": artifact.stat().st_size,
        "sha256": file_sha256(artifact),
        "origin_url": mirror,
        "license": "id-software-registered-game-data",
        "license_url": "https://github.com/x86dx2/x86qw",
        "source_urls": ["https://github.com/x86dx2/x86qw/tree/main/dist/game-data/id1"],
        "redistribution_reviewed": True,
        "urls": [mirror, gitlab],
        "source_revision": revision,
        "mirror_title": f"x86QW Content · Dados base {VERSION}",
        "mirror_notes": "Dados base obrigatórios usados pelo instalador x86QW.",
        "mirror_latest": False,
    }
