from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from maintenance.tools.native_handoff import CANONICAL_CASES, NativeHandoffError
from maintenance.tools.native_handoff_evidence import (
    EvidenceNotRun,
    aggregate_pending_evidence,
)


class NativeHandoffEvidenceTests(unittest.TestCase):
    def _candidate(self, root: Path) -> tuple[Path, dict[str, str]]:
        candidate = root / "candidate"
        candidate.mkdir()
        artifact = candidate / "artifact.zip"
        artifact.write_bytes(b"immutable candidate bytes")
        artifact_digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        manifest = {
            "format": 1,
            "project": "x86qw",
            "version": "1.0.0-rc.1",
            "commit": "c" * 40,
            "generated_at": "2026-08-06T00:00:00Z",
            "artifacts": {
                "artifact.zip": {
                    "size": artifact.stat().st_size,
                    "sha256": artifact_digest,
                }
            },
            "artifact_count": 1,
            "metadata": {
                name: {"size": 1, "sha256": "a" * 64}
                for name in (
                    "checksums.txt",
                    "ownership.json",
                    "sbom.spdx.json",
                    "provenance.json",
                    "mirrors.json",
                )
            },
            "candidate_sha256": "b" * 64,
        }
        manifest_path = candidate / "candidate.json"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
        identity = {
            "version": manifest["version"],
            "commit": manifest["commit"],
            "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        }
        return candidate, identity

    def _handoff(
        self,
        root: Path,
        identity: dict[str, str],
        *,
        status: str = "passed",
    ) -> Path:
        evidence = root / "native run usuario-secreto"
        evidence.mkdir()
        runtime_dir = root / "Users" / "usuario-secreto" / "bin"
        runtime_dir.mkdir(parents=True)
        runtime = runtime_dir / "runtime-secret"
        runtime.write_bytes(b"runtime exact bytes")
        runtime_identity = {
            "path": str(runtime),
            "size": runtime.stat().st_size,
            "sha256": hashlib.sha256(runtime.read_bytes()).hexdigest(),
        }
        cases = []
        for index, name in enumerate(CANONICAL_CASES, start=1):
            stdout = evidence / f"{index:02d}-{name}.stdout.log"
            stderr = evidence / f"{index:02d}-{name}.stderr.log"
            stdout.write_text(
                f"TOKEN=super-secret caminho=/Users/usuario-secreto caso={name}\n",
                encoding="utf-8",
            )
            stderr.write_text("PASSWORD=never-copy-this\n", encoding="utf-8")
            cases.append({
                "name": name,
                "status": status if index == 1 else "passed",
                "exit_code": 0,
                "duration_ms": index,
                "candidate_artifact": "artifact.zip",
                "candidate_artifact_sha256": hashlib.sha256(
                    b"immutable candidate bytes"
                ).hexdigest(),
                "runtime": runtime_identity,
                "stdout": stdout.name,
                "stdout_sha256": hashlib.sha256(stdout.read_bytes()).hexdigest(),
                "stderr": stderr.name,
                "stderr_sha256": hashlib.sha256(stderr.read_bytes()).hexdigest(),
            })
        handoff = {
            "format": 1,
            "project": "x86qw",
            "status": status,
            "platform": "macOS-ARM64" if status != "not-run" else None,
            "candidate": identity if status != "not-run" else None,
            "environment": (
                {"system": "Darwin", "machine": "arm64"}
                if status != "not-run"
                else {"system": "Linux", "machine": "x86_64"}
            ),
            "runtime_executed": status != "not-run",
            "cases": cases if status != "not-run" else [],
            "reason": None if status == "passed" else "not executed",
        }
        path = evidence / "handoff.json"
        path.write_text(json.dumps(handoff) + "\n", encoding="utf-8")
        return path

    def _tree_hashes(self, root: Path) -> dict[str, str]:
        return {
            path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*")
            if path.is_file()
        }

    def test_aggregate_is_deterministic_redacted_pending_and_candidate_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, identity = self._candidate(root)
            handoff = self._handoff(root, identity)
            before = self._tree_hashes(candidate)
            first = root / "first-pending.json"
            second = root / "second-pending.json"

            aggregate = aggregate_pending_evidence(
                candidate=candidate,
                handoff=handoff,
                expected_candidate_sha256=identity["manifest_sha256"],
                output=first,
            )
            aggregate_pending_evidence(
                candidate=candidate,
                handoff=handoff,
                expected_candidate_sha256=identity["manifest_sha256"],
                output=second,
            )

            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(before, self._tree_hashes(candidate))
            self.assertEqual("pending", aggregate["status"])
            self.assertFalse(aggregate["signed"])
            self.assertFalse(aggregate["promotable"])
            self.assertEqual(identity, aggregate["candidate"])
            self.assertEqual(["macOS-ARM64"], list(aggregate["platforms"]))
            rendered = first.read_text(encoding="utf-8")
            self.assertNotIn("super-secret", rendered)
            self.assertNotIn("never-copy-this", rendered)
            self.assertNotIn("usuario-secreto", rendered)
            self.assertNotIn(".stdout.log", rendered)
            first_case = aggregate["platforms"]["macOS-ARM64"]["cases"][0]
            self.assertEqual(
                hashlib.sha256(
                    (handoff.parent / "01-install-clean-space-unicode.stdout.log").read_bytes()
                ).hexdigest(),
                first_case["stdout_sha256"],
            )
            self.assertEqual(
                {"size", "sha256"},
                set(first_case["runtime"]),
            )

    def test_candidate_digest_mismatch_or_identity_drift_creates_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, identity = self._candidate(root)
            handoff = self._handoff(root, identity)
            output = root / "pending.json"

            with self.assertRaisesRegex(NativeHandoffError, "candidate-sha256"):
                aggregate_pending_evidence(
                    candidate=candidate,
                    handoff=handoff,
                    expected_candidate_sha256="0" * 64,
                    output=output,
                )
            self.assertFalse(output.exists())

            value = json.loads(handoff.read_text(encoding="utf-8"))
            value["candidate"]["commit"] = "d" * 40
            handoff.write_text(json.dumps(value) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(NativeHandoffError, "candidato exato"):
                aggregate_pending_evidence(
                    candidate=candidate,
                    handoff=handoff,
                    expected_candidate_sha256=identity["manifest_sha256"],
                    output=output,
                )
            self.assertFalse(output.exists())

    def test_tampered_log_or_failed_case_creates_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, identity = self._candidate(root)
            handoff = self._handoff(root, identity)
            output = root / "pending.json"
            (handoff.parent / "01-install-clean-space-unicode.stdout.log").write_text(
                "tampered\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(NativeHandoffError, "stdout diverge"):
                aggregate_pending_evidence(
                    candidate=candidate,
                    handoff=handoff,
                    expected_candidate_sha256=identity["manifest_sha256"],
                    output=output,
                )
            self.assertFalse(output.exists())

            other = root / "other"
            other.mkdir()
            candidate, identity = self._candidate(other)
            handoff = self._handoff(other, identity, status="failed")
            with self.assertRaisesRegex(NativeHandoffError, "não é evidência"):
                aggregate_pending_evidence(
                    candidate=candidate,
                    handoff=handoff,
                    expected_candidate_sha256=identity["manifest_sha256"],
                    output=output,
                )
            self.assertFalse(output.exists())

    def test_missing_or_not_run_inputs_create_no_aggregate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, identity = self._candidate(root)
            output = root / "pending.json"
            with self.assertRaises(EvidenceNotRun):
                aggregate_pending_evidence(
                    candidate=root / "missing-candidate",
                    handoff=root / "missing-handoff.json",
                    expected_candidate_sha256=identity["manifest_sha256"],
                    output=output,
                )
            self.assertFalse(output.exists())

            handoff = self._handoff(root, identity, status="not-run")
            with self.assertRaises(EvidenceNotRun):
                aggregate_pending_evidence(
                    candidate=candidate,
                    handoff=handoff,
                    expected_candidate_sha256=identity["manifest_sha256"],
                    output=output,
                )
            self.assertFalse(output.exists())

    def test_output_cannot_overwrite_or_enter_candidate_or_reserved_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, identity = self._candidate(root)
            handoff = self._handoff(root, identity)
            destinations = (
                candidate / "pending.json",
                root / "release-evidence.json",
            )
            for output in destinations:
                with self.subTest(output=output), self.assertRaises(NativeHandoffError):
                    aggregate_pending_evidence(
                        candidate=candidate,
                        handoff=handoff,
                        expected_candidate_sha256=identity["manifest_sha256"],
                        output=output,
                    )
                self.assertFalse(output.exists())

            existing = root / "existing.json"
            existing.write_text("preserve\n", encoding="utf-8")
            with self.assertRaisesRegex(NativeHandoffError, "já existe"):
                aggregate_pending_evidence(
                    candidate=candidate,
                    handoff=handoff,
                    expected_candidate_sha256=identity["manifest_sha256"],
                    output=existing,
                )
            self.assertEqual("preserve\n", existing.read_text(encoding="utf-8"))

            target = root / "target.json"
            target.write_text("preserve\n", encoding="utf-8")
            symlink = root / "symlink.json"
            symlink.symlink_to(target)
            with self.assertRaises(NativeHandoffError):
                aggregate_pending_evidence(
                    candidate=candidate,
                    handoff=handoff,
                    expected_candidate_sha256=identity["manifest_sha256"],
                    output=symlink,
                )
            self.assertEqual("preserve\n", target.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
