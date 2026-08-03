#!/usr/bin/env python3
"""Build the mandatory x86QW base-game data package outside the installer bundle."""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path


try:
    from .build_artifacts import (
        publish_verified_file,
        read_regular_file,
        staged_artifact,
    )
except ImportError:  # Execucao direta
    from build_artifacts import publish_verified_file, read_regular_file, staged_artifact


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from x86qw_runtime.io.archive import ArchiveError, read_archive_members, scan_archive

VERSION = "0.1.0"
PACKAGE = "x86qw-core-id1"
RELEASE_TAG = f"x86qw-content-core-{VERSION}"
GITLAB_PROJECT_ID = 84813414
FIXED_ZIP_TIME = (2020, 1, 1, 0, 0, 0)
PAKS = ("pak0.pak", "pak1.pak")
MAX_CORE_PAK_BYTES = 128 * 1024 * 1024


def build_core_package(distribution: Path, output: Path) -> dict[str, object]:
    id1 = distribution / "game-data/id1"
    sources = [id1 / name for name in PAKS]
    source_payloads = [
        (source, read_regular_file(source, maximum_size=MAX_CORE_PAK_BYTES))
        for source in sources
    ]
    for source, payload in source_payloads:
        if not payload.startswith(b"PACK"):
            raise ValueError(f"core game data is not a PAK: {source}")
    source_hashes = [
        (source.name, hashlib.sha256(payload).hexdigest())
        for source, payload in source_payloads
    ]
    revision = hashlib.sha256(
        "".join(f"{name}\t{digest}\n" for name, digest in source_hashes).encode("ascii")
    ).hexdigest()
    release_root = output / f"core-{VERSION}"
    filename = f"{PACKAGE}-{VERSION}.zip"
    artifact = release_root / filename
    members: list[dict[str, str]] = []
    with staged_artifact(artifact, root=output, prefix=".core-") as staged:
        with zipfile.ZipFile(staged.stream, "w", allowZip64=True) as archive:
            for (source, payload), (_, digest) in zip(
                source_payloads, source_hashes, strict=True,
            ):
                member = f"payload/id1/{source.name}"
                members.append({
                    "path": member,
                    "sha256": digest,
                    "source": f"dist/game-data/id1/{source.name}",
                })
                info = zipfile.ZipInfo(member, FIXED_ZIP_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, payload)
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
        staged.seal()

        def validate(path: Path):
            try:
                plan = scan_archive(
                    path,
                    required_members=(
                        *(str(member["path"]) for member in members),
                        "_x86qw/component.json",
                    ),
                )
                read_archive_members(plan, ())
            except ArchiveError as error:
                raise ValueError(
                    f"core package failed canonical archive validation: {path}: {error}"
                ) from error
            return plan

        plan = publish_verified_file(
            staged,
            artifact,
            validate=validate,
            fingerprint=lambda value: (value.source_size, value.source_sha256),
            conflict_message=f"core package target already differs: {artifact}",
        )
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
        "size": plan.source_size,
        "sha256": plan.source_sha256,
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
