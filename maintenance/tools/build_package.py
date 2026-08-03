#!/usr/bin/env python3
"""Build an immutable byte-for-byte x86QW mirror package from one recipe."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

try:
    from .add_package import register_package
    from .validate_catalog import DEFAULT_CATALOG, PACKAGE_FIELDS, ROOT
    from .validate_recipes import validate_recipe
    from .downloader import DownloadError, MAX_ARTIFACT_BYTES, PinnedArtifact, download as bounded_download
except ImportError:  # Execucao direta
    from add_package import register_package
    from validate_catalog import DEFAULT_CATALOG, PACKAGE_FIELDS, ROOT
    from validate_recipes import validate_recipe
    from downloader import DownloadError, MAX_ARTIFACT_BYTES, PinnedArtifact, download as bounded_download


DEFAULT_OUTPUT = ROOT / "dist"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_zip(path: Path, expected_members: list[str]) -> None:
    if not zipfile.is_zipfile(path):
        raise ValueError("artifact is not a ZIP archive")
    with zipfile.ZipFile(path) as archive:
        names: set[str] = set()
        for member in archive.infolist():
            name = member.filename.rstrip("/")
            if not name:
                continue
            relative = PurePosixPath(name)
            if relative.is_absolute() or ".." in relative.parts or "\\" in name:
                raise ValueError(f"archive contains an unsafe path: {member.filename}")
            if member.flag_bits & 0x1:
                raise ValueError(f"archive contains an encrypted member: {member.filename}")
            names.add(name)
        missing = sorted(set(expected_members) - names)
        if missing:
            raise ValueError(f"archive is missing expected member: {missing[0]}")
        bad = archive.testzip()
        if bad is not None:
            raise ValueError(f"archive member failed CRC validation: {bad}")


def verify_tar_gz(path: Path, expected_members: list[str]) -> None:
    try:
        with tarfile.open(path, "r:gz") as archive:
            names: set[str] = set()
            for member in archive:
                name = member.name.rstrip("/")
                if not name:
                    continue
                relative = PurePosixPath(name)
                if relative.is_absolute() or ".." in relative.parts or "\\" in name:
                    raise ValueError(f"archive contains an unsafe path: {member.name}")
                if member.issym() or member.islnk() or member.isdev():
                    raise ValueError(f"archive contains an unsupported member: {member.name}")
                names.add(name)
    except tarfile.TarError as error:
        raise ValueError("artifact is not a valid gzip-compressed TAR archive") from error
    missing = sorted(set(expected_members) - names)
    if missing:
        raise ValueError(f"archive is missing expected member: {missing[0]}")


def verify_artifact(path: Path, package: dict[str, object]) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"artifact must be a regular file: {path}")
    size = path.stat().st_size
    if size != package["size"]:
        raise ValueError(f"artifact size mismatch: expected {package['size']}, got {size}")
    digest = file_sha256(path)
    if digest != package["sha256"]:
        raise ValueError(f"artifact SHA-256 mismatch: expected {package['sha256']}, got {digest}")
    if package["artifact_format"] == "zip":
        verify_zip(path, package["expected_members"])
    elif package["artifact_format"] == "tar.gz":
        verify_tar_gz(path, package["expected_members"])


def download(url: str, destination: Path, expected_size: int, expected_sha256: str) -> None:
    bounded_download(PinnedArtifact(
        url=url,
        destination=destination,
        expected_size=expected_size,
        expected_sha256=expected_sha256,
        maximum_size=MAX_ARTIFACT_BYTES,
        deadline_seconds=120,
        headers={"User-Agent": "x86qw-ingest/1"},
        label=destination.name,
    ))


def build_package(
    recipe_path: Path,
    output_root: Path = DEFAULT_OUTPUT,
    *,
    artifact: Path | None = None,
) -> tuple[Path, Path, dict[str, object]]:
    recipe_bytes = recipe_path.read_bytes()
    recipe = json.loads(recipe_bytes)
    state = validate_recipe(recipe, str(recipe_path))
    if state != "ready":
        raise ValueError(f"recipe is blocked: {recipe['review']['notes']}")
    package = recipe["package"]

    target_dir = output_root.joinpath(
        package["component"], package["version"], package["channel"],
        f"{package['platform']}-{package['architecture']}",
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / package["filename"]
    manifest = target_dir / "manifest.json"

    with tempfile.TemporaryDirectory(prefix="x86qw-ingest-") as temporary:
        candidate = Path(temporary) / package["filename"]
        if artifact is None:
            download(
                package["origin_url"], candidate, package["size"], package["sha256"],
            )
        else:
            if artifact.is_symlink() or not artifact.is_file():
                raise ValueError(f"artifact must be a regular file: {artifact}")
            shutil.copyfile(artifact, candidate)
        verify_artifact(candidate, package)

        if target.exists():
            verify_artifact(target, package)
        else:
            descriptor, temporary_name = tempfile.mkstemp(prefix=".artifact-", dir=target_dir)
            os.close(descriptor)
            temporary_target = Path(temporary_name)
            try:
                shutil.copyfile(candidate, temporary_target)
                os.replace(temporary_target, target)
            finally:
                if temporary_target.exists():
                    temporary_target.unlink()
            target.chmod(0o644)

    public_package = {key: package[key] for key in PACKAGE_FIELDS}
    public_package["distribution_path"] = target.relative_to(output_root).as_posix()
    manifest_data = {
        "format": 1,
        "project": "x86qw",
        "kind": "mirror",
        "recipe_sha256": hashlib.sha256(recipe_bytes).hexdigest(),
        "package": public_package,
    }
    encoded = (json.dumps(manifest_data, ensure_ascii=False, indent=2) + "\n").encode()
    if manifest.exists() and manifest.read_bytes() != encoded:
        raise ValueError(f"manifest already exists with different contents: {manifest}")
    manifest.write_bytes(encoded)
    manifest.chmod(0o644)
    return target, manifest, public_package


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Baixa e valida um mirror x86QW a partir de uma receita aprovada."
    )
    parser.add_argument("recipe", type=Path)
    parser.add_argument("--artifact", type=Path, help="usa um arquivo local em vez de baixar novamente")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--register", action="store_true", help="registra o pacote no catálogo após validar")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    return parser.parse_args()


def main() -> int:
    options = parse_arguments()
    target, manifest, package = build_package(
        options.recipe, options.output_dir, artifact=options.artifact,
    )
    if options.register:
        register_package(
            options.catalog,
            target,
            component=package["component"], version=package["version"],
            channel=package["channel"], platform=package["platform"],
            architecture=package["architecture"], origin_url=package["origin_url"],
            license_name=package["license"], license_url=package["license_url"],
            source_urls=package["source_urls"], mirror_urls=package["urls"],
            redistribution_reviewed=package["redistribution_reviewed"],
            distribution_path=target.relative_to(options.output_dir).as_posix(),
        )
    print(f"built {target}")
    print(f"manifest {manifest}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DownloadError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"package not built: {error}", file=sys.stderr)
        raise SystemExit(1)
