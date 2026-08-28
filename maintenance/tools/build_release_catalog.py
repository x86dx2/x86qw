#!/usr/bin/env python3
"""Create the candidate catalog entry from the exact installer bytes.

The public catalog is a release input, not a hand-edited copy of the previous
stable catalog.  This tool keeps the existing package records, retires the old
installer ``current`` flag, and derives the new installer size and SHA-256 from
the already-built candidate ZIP.  It never uploads or changes the source
catalog.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from maintenance.tools.validate_catalog import (
    MAX_ARTIFACT_BYTES,
    published_package_count,
    validate_catalog,
)
from x86qw_runtime.versioning import VersionError, parse_semver


REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
GITLAB_PROJECT = re.compile(r"^[0-9]+$")
UTC = timezone.utc


class ReleaseCatalogError(ValueError):
    """Candidate catalog inputs are invalid or inconsistent."""


def _canonical_generated_at(value: object, label: str = "generated_at") -> str:
    """Return a second-precision UTC timestamp suitable for deterministic JSON."""

    if not isinstance(value, str) or not value.strip():
        raise ReleaseCatalogError(f"{label} precisa ser uma data UTC RFC3339")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise ReleaseCatalogError(f"{label} não é uma data RFC3339 válida") from error
    if parsed.tzinfo is None or parsed.microsecond:
        raise ReleaseCatalogError(
            f"{label} precisa conter fuso horário e precisão de segundos"
        )
    return parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _regular_file(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ReleaseCatalogError(f"{label} ausente ou inseguro: {path}")
    if path.stat().st_size <= 0 or path.stat().st_size > MAX_ARTIFACT_BYTES:
        raise ReleaseCatalogError(f"{label} excede o limite permitido: {path}")
    return path


def _read_json(path: Path, label: str) -> dict[str, object]:
    _regular_file(path, label)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReleaseCatalogError(f"{label} não é JSON válido") from error
    if not isinstance(value, dict):
        raise ReleaseCatalogError(f"{label} precisa ser um objeto JSON")
    return value


def _digest(path: Path) -> tuple[int, str]:
    size = 0
    digest = hashlib.sha256()
    with _regular_file(path, "instalador candidato").open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def build_candidate_catalog(
    *,
    source: Path,
    installer: Path,
    output: Path,
    version: str,
    repository: str = "x86dx2/x86qw",
    gitlab_project: str = "84813414",
    release_title: str | None = None,
    release_notes: str | None = None,
    generated_at: str | None = None,
) -> dict[str, object]:
    try:
        parse_semver(version)
    except (TypeError, VersionError) as error:
        raise ReleaseCatalogError(f"versão candidata inválida: {version}") from error
    if REPOSITORY.fullmatch(repository) is None:
        raise ReleaseCatalogError("repositório GitHub inválido")
    if GITLAB_PROJECT.fullmatch(gitlab_project) is None:
        raise ReleaseCatalogError("projeto GitLab inválido")
    if output.exists() or output.is_symlink():
        raise ReleaseCatalogError(f"catálogo candidato já existe: {output}")
    catalog = _read_json(Path(source), "catálogo base")
    try:
        validate_catalog(catalog)
    except (OSError, ValueError) as error:
        raise ReleaseCatalogError(f"catálogo base inválido: {error}") from error
    packages = catalog.get("packages")
    assert isinstance(packages, list)
    installers = [
        item for item in packages
        if isinstance(item, dict) and item.get("component") == "installer"
    ]
    current = [item for item in installers if item.get("current") is True]
    if len(current) != 1:
        raise ReleaseCatalogError("catálogo base não possui um instalador current")
    if any(item.get("version") == version for item in installers):
        raise ReleaseCatalogError(f"catálogo já contém a versão candidata: {version}")
    if generated_at is None:
        generated_at = catalog.get("generated_at")
    generated_at = _canonical_generated_at(generated_at)

    if release_title is not None and not isinstance(release_title, str):
        raise ReleaseCatalogError("título da release precisa ser texto")
    if release_notes is not None and not isinstance(release_notes, str):
        raise ReleaseCatalogError("notas da release precisam ser texto")
    title = release_title.strip() if release_title is not None else f"x86QW Installer {version}"
    notes = release_notes.strip() if release_notes is not None else f"x86QW release candidate {version}."
    if not title:
        raise ReleaseCatalogError("título da release não pode ser vazio")
    if not notes:
        raise ReleaseCatalogError("notas da release não podem ser vazias")

    size, digest = _digest(Path(installer))
    filename = f"x86qw-installer-{version}.zip"
    tag = f"x86qw-installer-{version}"
    github_url = f"https://github.com/{repository}/releases/download/{tag}/{filename}"
    gitlab_url = (
        f"https://gitlab.com/api/v4/projects/{gitlab_project}/packages/generic/"
        f"x86qw-installer/{version}/{filename}"
    )
    record = dict(current[0])
    record.update({
        "version": version,
        "current": True,
        "filename": filename,
        "size": size,
        "sha256": digest,
        "origin_url": github_url,
        "urls": [github_url, gitlab_url],
        "distribution_path": f"installer/packages/{version}/{filename}",
        "release_url": f"https://github.com/{repository}/releases/tag/{tag}",
        "release_title": title,
        "release_notes": notes,
        "mirror_title": title,
        "mirror_notes": notes,
        "mirror_latest": True,
    })
    for item in installers:
        item["current"] = False
        item["mirror_latest"] = False
    packages.append(record)
    packages.sort(key=lambda item: (
        str(item.get("component", "")),
        str(item.get("package", item.get("component", ""))),
        str(item.get("version", "")),
        str(item.get("platform", "")),
    ))
    catalog["generated_at"] = generated_at
    try:
        validate_catalog(catalog)
    except (OSError, ValueError) as error:
        raise ReleaseCatalogError(f"catálogo candidato inválido: {error}") from error
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}-", suffix=".tmp", dir=output.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(catalog, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return catalog


def build_candidate_product(
    *, source: Path, catalog: dict[str, object], output: Path,
) -> dict[str, object]:
    """Project the candidate installer facts into the public product document."""

    if output.exists() or output.is_symlink():
        raise ReleaseCatalogError(f"produto candidato já existe: {output}")
    product = _read_json(Path(source), "produto base")
    packages = catalog.get("packages")
    if not isinstance(packages, list):
        raise ReleaseCatalogError("catálogo candidato sem packages")
    installers = [
        item for item in packages
        if isinstance(item, dict)
        and item.get("package") == "x86qw-installer"
        and item.get("current") is True
    ]
    if len(installers) != 1:
        raise ReleaseCatalogError("catálogo candidato não possui um installer current")
    current = installers[0]
    installer = product.get("installer")
    if not isinstance(installer, dict):
        raise ReleaseCatalogError("produto base sem projeção de installer")
    product["version"] = current["version"]
    product["package_count"] = published_package_count(catalog)
    product["installer"] = {
        "filename": current["filename"],
        "distribution_path": current["distribution_path"],
        "sha256": current["sha256"],
        "size": current["size"],
        "urls": current["urls"],
    }
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}-", suffix=".tmp", dir=output.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(product, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return product


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--repository", default="x86dx2/x86qw")
    parser.add_argument("--gitlab-project", default="84813414")
    parser.add_argument("--release-title")
    parser.add_argument("--release-notes")
    parser.add_argument(
        "--generated-at",
        help="timestamp UTC RFC3339 determinístico; por padrão preserva o catálogo-base",
    )
    parser.add_argument("--product-source", type=Path)
    parser.add_argument("--product-output", type=Path)
    options = parser.parse_args(arguments)
    try:
        catalog = build_candidate_catalog(
            source=options.source,
            installer=options.installer,
            output=options.output,
            version=options.version,
            repository=options.repository,
            gitlab_project=options.gitlab_project,
            release_title=options.release_title,
            release_notes=options.release_notes,
            generated_at=options.generated_at,
        )
        if (options.product_source is None) != (options.product_output is None):
            raise ReleaseCatalogError(
                "--product-source e --product-output devem ser usados juntos"
            )
        if options.product_source is not None and options.product_output is not None:
            build_candidate_product(
                source=options.product_source,
                catalog=catalog,
                output=options.product_output,
            )
    except (OSError, ReleaseCatalogError) as error:
        print(f"[ERRO] {error}")
        return 1
    print(json.dumps({"project": catalog["project"], "version": options.version}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
