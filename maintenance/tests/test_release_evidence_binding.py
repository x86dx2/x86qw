from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from maintenance.tools import native_release_evidence, release_candidate, release_evidence_binding
from x86qw_runtime.contracts.native_evidence import CASE_ASSERTIONS, CANONICAL_CASES, NATIVE_EVIDENCE_FORMAT


class ReleaseEvidenceBindingTests(unittest.TestCase):
    def _candidate(self, root: Path) -> Path:
        source = root / "build"
        source.mkdir()
        (source / "artifact.zip").write_bytes(b"candidate")
        candidate = root / "candidate"
        release_candidate.prepare_candidate(
            source=source,
            output=candidate,
            version="1.0.0-rc.1",
            commit="a" * 40,
            generated_at="2026-08-11T12:00:00Z",
        )
        return candidate

    def _evidence(self, root: Path, candidate: Path) -> tuple[Path, Path]:
        artifact_root = root / "evidence"
        records_dir = artifact_root / "records"
        records_dir.mkdir(parents=True)
        manifest_sha256 = hashlib.sha256((candidate / "candidate.json").read_bytes()).hexdigest()
        cases = []
        for name in CANONICAL_CASES:
            payload = f"{name}\n".encode()
            artifact_path = artifact_root / "logs" / f"{name}.log"
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_bytes(payload)
            cases.append({
                "name": name,
                "command": ["x86qw", name],
                "status": "passed",
                "exit_code": 0,
                "started_at": "2026-08-11T12:01:00Z",
                "duration_ms": 10,
                "assertions": sorted(CASE_ASSERTIONS[name]),
                "artifacts": [{
                    "path": f"logs/{name}.log",
                    "kind": f"{name}-log",
                    "size": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }],
            })
        smoke = artifact_root / "smoke.json"
        smoke.write_text(json.dumps({
            "format": NATIVE_EVIDENCE_FORMAT,
            "status": "passed",
            "platform": "macOS-ARM64",
            "completed_at": "2026-08-11T12:02:00Z",
            "candidate": {
                "version": "1.0.0-rc.1",
                "commit": "a" * 40,
                "manifest_sha256": manifest_sha256,
            },
            "environment": {
                "os": "macOS",
                "architecture": "arm64",
                "standard_user": True,
                "elevated": False,
                "distro": None,
                "distro_version": None,
                "glibc_version": None,
            },
            "hardware": {"chip": "Apple M3 Pro", "model": "Mac15,6"},
            "cases": cases,
            "secrets": "redacted",
            "runtime_executed": True,
        }) + "\n", encoding="utf-8")
        native_release_evidence.write_native_evidence(
            candidate=candidate,
            platform="macOS-ARM64",
            report=smoke,
            artifact_root=artifact_root,
            output=records_dir / "macOS-ARM64.json",
            recorded_at="2026-08-11T12:02:00Z",
        )
        return artifact_root, records_dir

    def _create(self, root: Path) -> tuple[Path, Path, Path]:
        candidate = self._candidate(root)
        artifact_root, records_dir = self._evidence(root, candidate)
        binding = artifact_root / "binding.json"
        release_evidence_binding.create_binding(
            candidate=candidate,
            records_dir=records_dir,
            artifact_root=artifact_root,
            output=binding,
            source_workflow=".github/workflows/release.yml",
            source_run_id="12345",
            source_run_attempt="1",
            source_artifact="x86qw-m3-evidence-12345",
            generated_at="2026-08-11T12:03:00Z",
        )
        return candidate, artifact_root, binding

    def test_binding_covers_exact_candidate_and_evidence_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, artifact_root, binding = self._create(root)
            result = release_evidence_binding.verify_binding(
                candidate=candidate,
                records_dir=artifact_root / "records",
                artifact_root=artifact_root,
                binding=binding,
            )
            self.assertEqual("macOS-ARM64", next(iter(result["platforms"])))
            self.assertEqual(".github/workflows/release.yml", result["source"]["workflow"])

    def test_binding_rejects_changed_evidence_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, artifact_root, binding = self._create(root)
            (artifact_root / "logs" / "game-ktx.log").write_bytes(b"changed\n")
            with self.assertRaises(release_evidence_binding.EvidenceBindingError):
                release_evidence_binding.verify_binding(
                    candidate=candidate,
                    records_dir=artifact_root / "records",
                    artifact_root=artifact_root,
                    binding=binding,
                )

    def test_binding_rejects_extra_unbound_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, artifact_root, binding = self._create(root)
            (artifact_root / "unbound.txt").write_text("unexpected\n", encoding="utf-8")
            with self.assertRaises(release_evidence_binding.EvidenceBindingError):
                release_evidence_binding.verify_binding(
                    candidate=candidate,
                    records_dir=artifact_root / "records",
                    artifact_root=artifact_root,
                    binding=binding,
                )

    def test_binding_rejects_manifest_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, artifact_root, binding = self._create(root)
            manifest = candidate / "candidate.json"
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["version"] = "1.0.0"
            manifest.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            with self.assertRaises(release_evidence_binding.EvidenceBindingError):
                release_evidence_binding.verify_binding(
                    candidate=candidate,
                    records_dir=artifact_root / "records",
                    artifact_root=artifact_root,
                    binding=binding,
                )


if __name__ == "__main__":
    unittest.main()
