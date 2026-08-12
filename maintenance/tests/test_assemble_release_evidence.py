from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from maintenance.tools import assemble_release_evidence, release_candidate
from x86qw_runtime.contracts.native_evidence import CASE_ASSERTIONS, CANONICAL_CASES, NATIVE_EVIDENCE_FORMAT


ROOT = Path(__file__).resolve().parents[2]


class AssembleReleaseEvidenceTests(unittest.TestCase):
    def _candidate(self, root: Path) -> Path:
        source = root / "source"
        source.mkdir()
        (source / "artifact.zip").write_bytes(b"candidate")
        candidate = root / "candidate"
        release_candidate.prepare_candidate(
            source=source,
            output=candidate,
            version="1.0.0-rc.1",
            commit="a" * 40,
            generated_at="2026-08-11T00:00:00Z",
        )
        return candidate

    def _records(self, root: Path, candidate: Path) -> Path:
        records = root / "records"
        records.mkdir()
        identity = {
            "version": "1.0.0-rc.1",
            "commit": "a" * 40,
            "manifest_sha256": hashlib.sha256((candidate / "candidate.json").read_bytes()).hexdigest(),
        }
        cases = []
        for index, name in enumerate(CANONICAL_CASES):
            payload = f"{name}\n".encode()
            cases.append({
                "name": name,
                "command": ["x86qw-native-case-v1", name],
                "status": "passed",
                "exit_code": 0,
                "started_at": "2026-08-11T12:00:00Z",
                "duration_ms": index,
                "assertions": sorted(CASE_ASSERTIONS[name]),
                "artifacts": [{
                    "path": f"artifacts/{index}.log",
                    "kind": f"{name}-log",
                    "size": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }],
            })
        record = {
            "format": NATIVE_EVIDENCE_FORMAT,
            "project": "x86qw",
            "status": "complete",
            "platform": "macOS-ARM64",
            "recorded_at": "2026-08-11T12:00:00Z",
            "candidate": identity,
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
            "runtime_executed": True,
            "cases": cases,
            "secrets": "redacted",
            "signature": None,
        }
        (records / "macOS-ARM64.json").write_text(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return records

    def test_prepare_emits_canonical_unsigned_body_bound_to_candidate_and_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            records = self._records(root, candidate)
            body_path = root / "release-evidence-body.json"

            body = assemble_release_evidence.prepare_body(
                candidate=candidate,
                records_dir=records,
                output=body_path,
            )

            self.assertEqual(
                {"format", "project", "version", "commit", "status", "candidate", "platforms"},
                set(body),
            )
            self.assertNotIn("signature", body)
            self.assertEqual(
                body_path.read_bytes(), assemble_release_evidence.canonical_json_bytes(body),
            )

    def test_assemble_binds_external_signatures_without_private_key_material(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            records = self._records(root, candidate)
            body_path = root / "body.json"
            body = assemble_release_evidence.prepare_body(
                candidate=candidate,
                records_dir=records,
                output=body_path,
            )
            envelope = root / "signatures.json"
            envelope.write_text(json.dumps({
                "format": 1,
                "project": "x86qw",
                "candidate": body["candidate"],
                "body_sha256": hashlib.sha256(body_path.read_bytes()).hexdigest(),
                "signatures": [{"keyid": "a" * 64, "sig": "c2ln"}],
            }) + "\n", encoding="utf-8")
            output = root / "release-evidence.json"

            result = assemble_release_evidence.assemble(
                candidate=candidate,
                records_dir=records,
                body=body_path,
                signatures=envelope,
                output=output,
            )

            self.assertEqual("complete", result["status"])
            self.assertEqual([{"keyid": "a" * 64, "sig": "c2ln"}], result["signatures"])

    def test_assemble_rejects_body_digest_drift_and_private_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            records = self._records(root, candidate)
            body_path = root / "body.json"
            body = assemble_release_evidence.prepare_body(
                candidate=candidate,
                records_dir=records,
                output=body_path,
            )
            signatures = root / "signatures.json"
            signatures.write_text(json.dumps({
                "format": 1,
                "project": "x86qw",
                "candidate": body["candidate"],
                "body_sha256": "0" * 64,
                "signatures": [{"keyid": "a" * 64, "sig": "c2ln"}],
            }) + "\n", encoding="utf-8")
            with self.assertRaises(assemble_release_evidence.EvidenceAssemblyError):
                assemble_release_evidence.assemble(
                    candidate=candidate,
                    records_dir=records,
                    body=body_path,
                    signatures=signatures,
                    output=root / "bad.json",
                )

            signatures.write_text(json.dumps({
                "format": 1,
                "project": "x86qw",
                "candidate": body["candidate"],
                "body_sha256": hashlib.sha256(body_path.read_bytes()).hexdigest(),
                "signatures": [{
                    "keyid": "a" * 64,
                    "sig": "c2ln",
                    "private_key": "-----BEGIN PRIVATE KEY-----",
                }],
            }) + "\n", encoding="utf-8")
            with self.assertRaises(assemble_release_evidence.EvidenceAssemblyError):
                assemble_release_evidence.assemble(
                    candidate=candidate,
                    records_dir=records,
                    body=body_path,
                    signatures=signatures,
                    output=root / "bad-private.json",
                )

    def test_cli_requires_a_public_trust_root_for_assembly(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            records = self._records(root, candidate)
            body_path = root / "body.json"
            body = assemble_release_evidence.prepare_body(
                candidate=candidate,
                records_dir=records,
                output=body_path,
            )
            envelope = root / "signatures.json"
            envelope.write_text(json.dumps({
                "format": 1,
                "project": "x86qw",
                "candidate": body["candidate"],
                "body_sha256": hashlib.sha256(body_path.read_bytes()).hexdigest(),
                "signatures": [{"keyid": "a" * 64, "sig": "c2ln"}],
            }) + "\n", encoding="utf-8")
            output = root / "release-evidence.json"

            self.assertEqual(1, assemble_release_evidence.main([
                "assemble",
                "--candidate", str(candidate),
                "--records-dir", str(records),
                "--body", str(body_path),
                "--signatures", str(envelope),
                "--output", str(output),
            ]))
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
