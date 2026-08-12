#!/usr/bin/env python3
"""Attach an externally produced M3 evidence file without rebuilding a candidate."""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from maintenance.tools.native_evidence_contract import REQUIRED_NATIVE_PLATFORMS  # noqa: E402
from maintenance.tools.native_release_evidence import (  # noqa: E402
    validate_signed_evidence_coverage,
)
from maintenance.tools.release_candidate import CandidateError, verify_candidate  # noqa: E402


class EvidenceAttachmentError(RuntimeError):
    """The external evidence handoff is absent, unsafe, or inconsistent."""


def _evidence_file(root: Path) -> Path:
    root = Path(root)
    if root.is_symlink():
        raise EvidenceAttachmentError("artefato de evidência não pode ser symlink")
    if root.is_file():
        if root.name != "release-evidence.json":
            raise EvidenceAttachmentError("artefato de evidência deve conter release-evidence.json")
        return root
    if not root.is_dir():
        raise EvidenceAttachmentError("artefato de evidência ausente ou inseguro")
    matches: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise EvidenceAttachmentError(f"artefato de evidência contém symlink: {path}")
        if path.is_file() and path.name == "release-evidence.json":
            matches.append(path)
        elif not path.is_file() and not path.is_dir():
            raise EvidenceAttachmentError(f"artefato de evidência contém tipo especial: {path}")
    if len(matches) != 1:
        raise EvidenceAttachmentError(
            f"artefato de evidência deve conter exatamente um release-evidence.json; recebeu {len(matches)}"
        )
    return matches[0]


def _copy_tree(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_dir():
        raise EvidenceAttachmentError(f"candidato ausente ou inseguro: {source}")
    if destination.is_symlink() or not destination.is_dir():
        raise EvidenceAttachmentError(f"staging do candidato ausente ou inseguro: {destination}")
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_symlink():
            raise EvidenceAttachmentError(f"candidato contém symlink: {path}")
        if path.is_dir():
            target.mkdir()
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target, follow_symlinks=False)
        else:
            raise EvidenceAttachmentError(f"candidato contém tipo especial: {path}")


def attach(*, candidate: Path, evidence: Path, output: Path) -> dict[str, object]:
    """Copy a verified candidate and add exactly one externally signed report."""

    candidate = Path(candidate)
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise EvidenceAttachmentError(f"destino do candidato já existe: {output}")
    try:
        manifest = verify_candidate(candidate)
        evidence_path = _evidence_file(Path(evidence))
        output.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
        try:
            _copy_tree(candidate, staging)
            shutil.copy2(
                evidence_path,
                staging / "release-evidence.json",
                follow_symlinks=False,
            )
            validate_signed_evidence_coverage(
                candidate=staging,
                evidence=staging / "release-evidence.json",
                expected_platforms=REQUIRED_NATIVE_PLATFORMS,
            )
            output.mkdir()
            shutil.copytree(staging, output, dirs_exist_ok=True, symlinks=False)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return manifest
    except EvidenceAttachmentError:
        raise
    except (CandidateError, OSError) as error:
        raise EvidenceAttachmentError(str(error)) from error


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args(arguments)
    try:
        manifest = attach(
            candidate=options.candidate,
            evidence=options.evidence,
            output=options.output,
        )
    except (EvidenceAttachmentError, OSError) as error:
        print(f"[ERRO] {error}", file=sys.stderr)
        return 1
    print(f"[OK] Evidência anexada ao candidato {manifest['version']} sem rebuild.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
