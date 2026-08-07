from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from maintenance.native_case_entrypoint import (
    CANONICAL_CASES,
    CandidateCaseError,
    build_case_command,
    load_candidate,
    validate_case_name,
)


class NativeCaseEntrypointTests(unittest.TestCase):
    def _candidate(self, root: Path) -> Path:
        candidate = root / "candidate"
        candidate.mkdir()
        payload = candidate / "runtime/clients/stable.zip"
        payload.parent.mkdir(parents=True)
        payload.write_bytes(b"candidate bytes")
        digest = hashlib.sha256(payload.read_bytes()).hexdigest()
        manifest = {
            "format": 1,
            "project": "x86qw",
            "version": "1.0.0-rc.1",
            "commit": "c" * 40,
            "artifacts": {
                "runtime/clients/stable.zip": {
                    "size": payload.stat().st_size,
                    "sha256": digest,
                },
            },
        }
        (candidate / "candidate.json").write_text(
            json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8",
        )
        return candidate

    def test_only_the_closed_eighteen_case_protocol_is_accepted(self) -> None:
        self.assertEqual(18, len(CANONICAL_CASES))
        for name in CANONICAL_CASES:
            self.assertEqual(name, validate_case_name(name))
        with self.assertRaises(CandidateCaseError):
            validate_case_name("unknown-case")

    def test_candidate_artifacts_are_hash_checked_before_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            candidate = self._candidate(Path(temporary))
            loaded = load_candidate(candidate)
            self.assertEqual("1.0.0-rc.1", loaded.version)
            self.assertIn("runtime/clients/stable.zip", loaded.artifacts)
            (candidate / "runtime/clients/stable.zip").write_bytes(b"tampered")
            with self.assertRaisesRegex(CandidateCaseError, "diverge"):
                load_candidate(candidate)

    def test_symlinked_candidate_artifact_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            candidate = self._candidate(Path(temporary))
            payload = candidate / "runtime/clients/stable.zip"
            payload.unlink()
            payload.symlink_to(candidate / "candidate.json")
            with self.assertRaisesRegex(CandidateCaseError, "symlink"):
                load_candidate(candidate)

    def test_case_dispatch_rejects_a_missing_candidate_owned_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            candidate = self._candidate(Path(temporary))
            with self.assertRaisesRegex(CandidateCaseError, "artefato nativo ausente"):
                build_case_command(
                    candidate=load_candidate(candidate),
                    case="mvdsv-mvd",
                    scratch=Path(temporary) / "scratch",
                )

    def test_case_dispatch_is_literal_and_uses_a_candidate_owned_service(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            service = candidate / "runtime/servers/mvdsv/1.11/x86qw/runtime/macos-arm64/mvdsv"
            service.parent.mkdir(parents=True)
            service.write_bytes(b"native mvdsv")
            manifest_path = candidate / "candidate.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["artifacts"][
                "runtime/servers/mvdsv/1.11/x86qw/runtime/macos-arm64/mvdsv"
            ] = {
                "size": service.stat().st_size,
                "sha256": hashlib.sha256(service.read_bytes()).hexdigest(),
            }
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8",
            )
            prepared = build_case_command(
                candidate=load_candidate(candidate),
                case="mvdsv-mvd",
                scratch=root / "scratch",
            )
            self.assertEqual(service, prepared.executable)
            self.assertEqual("mvdsv", Path(prepared.argv[0]).name)
            self.assertEqual("-version", prepared.argv[1])
            self.assertNotIn(candidate.as_posix(), prepared.argv[0])
            self.assertIs(prepared.shell, False)

    def test_install_case_dispatches_stable_release_from_candidate_without_prompts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            archive = candidate / "installer/x86qw-installer-1.0.0.zip"
            archive.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("x86qw-installer-1.0.0/x86qw.pyz", b"installer")
            stable = candidate / (
                "runtime/clients/ezquake/stable/3.6.9/"
                "macos-universal/ezQuake-macOS-universal.zip"
            )
            stable.parent.mkdir(parents=True, exist_ok=True)
            stable.write_bytes(b"stable runtime")
            manifest_path = candidate / "candidate.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for path in (archive, stable):
                relative = path.relative_to(candidate).as_posix()
                manifest["artifacts"][relative] = {
                    "size": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8",
            )

            prepared = build_case_command(
                candidate=load_candidate(candidate),
                case="install-clean-space-unicode",
                scratch=root / "scratch",
            )

            self.assertEqual(
                (
                    sys.executable,
                    str(root / "scratch/extracted/installer/x86qw.pyz"),
                    "--platform", "macos",
                    "--channel", "stable",
                    "--release", "3.6.9",
                    "--without-components",
                    "install",
                    str(root / "scratch/instalação espaço"),
                ),
                prepared.argv,
            )


if __name__ == "__main__":
    unittest.main()
