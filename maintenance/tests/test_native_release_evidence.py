from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from maintenance.tools import native_release_evidence, release_candidate
from x86qw_runtime.contracts.native_evidence import (
    CASE_ASSERTIONS,
    CANONICAL_CASES,
    NATIVE_EVIDENCE_FORMAT,
    REQUIRED_NATIVE_PLATFORMS,
)


ROOT = Path(__file__).resolve().parents[2]


class NativeReleaseEvidenceTests(unittest.TestCase):
    def _candidate(self, root: Path) -> Path:
        source = root / "build"
        source.mkdir()
        (source / "artifact.zip").write_bytes(b"native candidate")
        candidate = root / "candidate"
        release_candidate.prepare_candidate(
            source=source,
            output=candidate,
            version="1.0.0-rc.1",
            commit="c" * 40,
            generated_at="2026-08-04T00:00:00Z",
        )
        return candidate

    def _environment(self, platform: str) -> dict[str, object]:
        if platform == "Linux-X64":
            return {
                "os": "Linux",
                "architecture": "x86_64",
                "standard_user": True,
                "elevated": False,
                "distro": "ubuntu",
                "distro_version": "24.04",
                "glibc_version": "2.39",
            }
        if platform == "Windows-X64":
            return {
                "os": "Windows",
                "architecture": "x64",
                "standard_user": True,
                "elevated": False,
                "distro": None,
                "distro_version": None,
                "glibc_version": None,
            }
        if platform == "macOS-ARM64":
            architecture = "arm64"
        else:
            architecture = "x86_64"
        return {
            "os": "macOS",
            "architecture": architecture,
            "standard_user": True,
            "elevated": False,
            "distro": None,
            "distro_version": None,
            "glibc_version": None,
        }

    def _hardware(self, platform: str) -> dict[str, str] | None:
        if platform == "macOS-ARM64":
            return {"chip": "Apple M3 Pro", "model": "Mac15,6"}
        return None

    def _cases(self) -> list[dict[str, object]]:
        return [
            {
                "name": name,
                "command": ["x86qw-native-smoke", name],
                "status": "passed",
                "exit_code": 0,
                "started_at": "2026-08-04T12:00:00Z",
                "duration_ms": 100,
                "assertions": sorted(CASE_ASSERTIONS[name]),
                "artifacts": [
                    {
                        "path": f"logs/{name}.log",
                        "kind": f"{name}-log",
                        "size": 1,
                        "sha256": "a" * 64,
                    },
                ],
            }
            for name in CANONICAL_CASES
        ]

    def _report(self, candidate: Path, path: Path, *, platform: str = "Linux-X64") -> Path:
        manifest = candidate / "candidate.json"
        cases = self._cases()
        for case in cases:
            artifact = case["artifacts"][0]
            artifact_path = path.parent / artifact["path"]
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            payload = f"native evidence: {case['name']}\n".encode("utf-8")
            artifact_path.write_bytes(payload)
            artifact["size"] = len(payload)
            artifact["sha256"] = hashlib.sha256(payload).hexdigest()
        report = {
            "format": NATIVE_EVIDENCE_FORMAT,
            "status": "passed",
            "platform": platform,
            "completed_at": "2026-08-04T12:00:00Z",
            "secrets": "redacted",
            "candidate": {
                "version": "1.0.0-rc.1",
                "commit": "c" * 40,
                "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
            },
            "environment": self._environment(platform),
            "cases": cases,
            "runtime_executed": True,
        }
        hardware = self._hardware(platform)
        if hardware is not None:
            report["hardware"] = hardware
        path.write_text(
            json.dumps(report),
            encoding="utf-8",
        )
        return path

    def _native_record(self, candidate: Path, platform: str) -> dict[str, object]:
        manifest = candidate / "candidate.json"
        record = {
            "format": NATIVE_EVIDENCE_FORMAT,
            "project": "x86qw",
            "status": "complete",
            "platform": platform,
            "recorded_at": "2026-08-04T00:00:00Z",
            "candidate": {
                "version": "1.0.0-rc.1",
                "commit": "c" * 40,
                "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
            },
            "environment": self._environment(platform),
            "runtime_executed": True,
            "cases": self._cases(),
            "secrets": "redacted",
            "signature": None,
        }
        hardware = self._hardware(platform)
        if hardware is not None:
            record["hardware"] = hardware
        return record

    def test_native_evidence_requires_explicit_handoff_execution_attestation(self):
        with tempfile.TemporaryDirectory() as temporary:
            candidate = self._candidate(Path(temporary))
            output = candidate / "native.json"
            report = self._report(candidate, Path(temporary) / "smoke.json")
            payload = json.loads(report.read_text(encoding="utf-8"))
            payload.pop("runtime_executed")
            report.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "maintenance/tools/native_release_evidence.py"),
                    "--candidate", str(candidate),
                    "--platform", "Linux-X64",
                    "--report", str(report),
                    "--output", str(output),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertFalse(output.exists())

    def test_native_evidence_records_exact_candidate_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            candidate = self._candidate(Path(temporary))
            output = Path(temporary) / "native.json"
            report = self._report(candidate, Path(temporary) / "smoke.json")
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "maintenance/tools/native_release_evidence.py"),
                    "--candidate", str(candidate),
                    "--platform", "Linux-X64",
                    "--report", str(report),
                    "--output", str(output),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            evidence = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("complete", evidence["status"])
            self.assertEqual("1.0.0-rc.1", evidence["candidate"]["version"])
            self.assertEqual("c" * 40, evidence["candidate"]["commit"])
            self.assertEqual("Linux-X64", evidence["platform"])

    def test_native_evidence_rejects_missing_smoke_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            candidate = self._candidate(Path(temporary))
            output = Path(temporary) / "native.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "maintenance/tools/native_release_evidence.py"),
                    "--candidate", str(candidate),
                    "--platform", "Linux-X64",
                    "--report", str(Path(temporary) / "missing.json"),
                    "--output", str(output),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertFalse(output.exists())

    def test_native_evidence_rejects_unredacted_smoke_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            candidate = self._candidate(Path(temporary))
            output = Path(temporary) / "native.json"
            report = self._report(candidate, Path(temporary) / "smoke.json")
            report.write_text(
                report.read_text(encoding="utf-8").replace('"secrets": "redacted"', '"secrets": "present"'),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "maintenance/tools/native_release_evidence.py"),
                    "--candidate", str(candidate),
                    "--platform", "Linux-X64",
                    "--report", str(report),
                    "--output", str(output),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertFalse(output.exists())

    def test_native_evidence_output_can_use_a_deterministic_timestamp(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            report = self._report(candidate, root / "smoke.json", platform="Linux-X64")
            output = root / "native.json"
            evidence = native_release_evidence.write_native_evidence(
                candidate=candidate,
                platform="Linux-X64",
                report=report,
                output=output,
                recorded_at="2026-08-04T12:34:56Z",
            )
            self.assertEqual("2026-08-04T12:34:56Z", evidence["recorded_at"])
            self.assertEqual(evidence, json.loads(output.read_text(encoding="utf-8")))
            with self.assertRaises(release_candidate.CandidateError):
                native_release_evidence.write_native_evidence(
                    candidate=candidate,
                    platform="Linux-X64",
                    report=report,
                    output=output,
                    recorded_at="2026-08-04T12:34:56Z",
                )

    def test_native_evidence_redacts_runner_command_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            report = self._report(candidate, root / "smoke.json")
            payload = json.loads(report.read_text(encoding="utf-8"))
            payload["cases"][0]["command"] = [
                "/private/runner/candidate/python",
                "/private/runner/candidate/entrypoint.py",
                "--case",
                CANONICAL_CASES[0],
            ]
            report.write_text(json.dumps(payload), encoding="utf-8")

            evidence = native_release_evidence.write_native_evidence(
                candidate=candidate,
                platform="Linux-X64",
                report=report,
                output=root / "native.json",
            )

            self.assertEqual(
                ["x86qw-native-case-v1", CANONICAL_CASES[0]],
                evidence["cases"][0]["command"],
            )
            self.assertNotIn("/private/runner", json.dumps(evidence))

    def test_native_evidence_reads_artifacts_from_explicit_handoff_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            handoff_root = root / "native-smoke"
            report = self._report(candidate, handoff_root / "smoke-report.json")
            output = root / "native-evidence.json"
            evidence = native_release_evidence.write_native_evidence(
                candidate=candidate,
                platform="Linux-X64",
                report=report,
                artifact_root=handoff_root,
                output=output,
            )
            self.assertEqual("Linux-X64", evidence["platform"])
            self.assertTrue(output.is_file())

    def test_native_evidence_rejects_symlinked_ancestor_in_explicit_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            handoff_root = root / "native-smoke"
            report = self._report(candidate, handoff_root / "smoke-report.json")
            outside = root / "outside"
            outside.mkdir()
            (outside / "logs").mkdir()
            original = handoff_root / "logs"
            original.rename(outside / "logs")
            original.symlink_to(outside / "logs", target_is_directory=True)

            with self.assertRaises(release_candidate.CandidateError):
                native_release_evidence.write_native_evidence(
                    candidate=candidate,
                    platform="Linux-X64",
                    report=report,
                    artifact_root=handoff_root,
                    output=root / "native-evidence.json",
                )

    def test_native_coverage_requires_exact_platforms_and_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            evidence_dir = root / "evidence"
            evidence_dir.mkdir()
            expected = tuple(sorted(REQUIRED_NATIVE_PLATFORMS, key=str.casefold))
            for platform in expected:
                (evidence_dir / f"{platform}.json").write_text(
                    json.dumps(self._native_record(candidate, platform)) + "\n",
                    encoding="utf-8",
                )
            records = native_release_evidence.validate_native_evidence(
                candidate=candidate,
                evidence_dir=evidence_dir,
                expected_platforms=expected,
            )
            self.assertEqual(expected, tuple(record["platform"] for record in records))

    def test_canonical_native_coverage_rejects_missing_and_conditional_intel(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            evidence_dir = root / "evidence"
            evidence_dir.mkdir()
            expected = tuple(sorted(REQUIRED_NATIVE_PLATFORMS, key=str.casefold))
            for platform in expected:
                (evidence_dir / f"{platform}.json").write_text(
                    json.dumps(self._native_record(candidate, platform)) + "\n",
                    encoding="utf-8",
                )

            (evidence_dir / "macOS-ARM64.json").unlink()
            with self.assertRaises(release_candidate.CandidateError):
                native_release_evidence.validate_native_evidence(
                    candidate=candidate,
                    evidence_dir=evidence_dir,
                    expected_platforms=expected,
                )

            (evidence_dir / "macOS-ARM64.json").write_text(
                json.dumps(self._native_record(candidate, "macOS-ARM64")) + "\n",
                encoding="utf-8",
            )
            (evidence_dir / "macOS-X64.json").write_text(
                json.dumps(self._native_record(candidate, "macOS-X64")) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(release_candidate.CandidateError):
                native_release_evidence.validate_native_evidence(
                    candidate=candidate,
                    evidence_dir=evidence_dir,
                    expected_platforms=expected,
                )

            (evidence_dir / "extra.json").write_text(
                json.dumps(self._native_record(candidate, "other-X64")) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(release_candidate.CandidateError):
                native_release_evidence.validate_native_evidence(
                    candidate=candidate,
                    evidence_dir=evidence_dir,
                    expected_platforms=expected,
                )

    def test_native_coverage_rejects_duplicate_platform_and_signed_identity_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            evidence_dir = root / "evidence"
            evidence_dir.mkdir()
            expected = ("Linux-X64", "macOS-X64")
            for index, platform in enumerate(expected):
                (evidence_dir / f"report-{index}.json").write_text(
                    json.dumps(self._native_record(candidate, platform)) + "\n",
                    encoding="utf-8",
                )
            duplicate = self._native_record(candidate, "Linux-X64")
            (evidence_dir / "duplicate.json").write_text(json.dumps(duplicate) + "\n", encoding="utf-8")
            with self.assertRaises(release_candidate.CandidateError):
                native_release_evidence.validate_native_evidence(
                    candidate=candidate,
                    evidence_dir=evidence_dir,
                    expected_platforms=expected,
                )

            (evidence_dir / "duplicate.json").unlink()
            aggregate = {
                "format": 1,
                "project": "x86qw",
                "version": "1.0.0-rc.1",
                "commit": "c" * 40,
                "status": "complete",
                "candidate": self._native_record(candidate, "Linux-X64")["candidate"],
                "platforms": {
                    platform: self._native_record(candidate, platform)
                    for platform in expected
                },
                "signature": {"keyid": "a" * 64, "sig": "c2ln"},
            }
            aggregate_path = root / "signed-evidence.json"
            aggregate_path.write_text(json.dumps(aggregate) + "\n", encoding="utf-8")
            native_release_evidence.validate_signed_evidence_coverage(
                candidate=candidate,
                evidence=aggregate_path,
                expected_platforms=expected,
                unsigned_evidence_dir=evidence_dir,
            )
            aggregate["signatures"] = [aggregate.pop("signature")]
            aggregate_path.write_text(json.dumps(aggregate) + "\n", encoding="utf-8")
            native_release_evidence.validate_signed_evidence_coverage(
                candidate=candidate,
                evidence=aggregate_path,
                expected_platforms=expected,
                unsigned_evidence_dir=evidence_dir,
            )
            aggregate["platforms"]["Linux-X64"]["cases"][0]["artifacts"][0]["sha256"] = "b" * 64
            aggregate_path.write_text(json.dumps(aggregate) + "\n", encoding="utf-8")
            with self.assertRaises(release_candidate.CandidateError):
                native_release_evidence.validate_signed_evidence_coverage(
                    candidate=candidate,
                    evidence=aggregate_path,
                    expected_platforms=expected,
                    unsigned_evidence_dir=evidence_dir,
                )
            aggregate["platforms"]["Linux-X64"]["cases"][0]["artifacts"][0]["sha256"] = "a" * 64
            aggregate["platforms"].pop("macOS-X64")
            aggregate_path.write_text(json.dumps(aggregate) + "\n", encoding="utf-8")
            with self.assertRaises(release_candidate.CandidateError):
                native_release_evidence.validate_signed_evidence_coverage(
                    candidate=candidate,
                    evidence=aggregate_path,
                    expected_platforms=expected,
                )

    def test_signed_aggregate_must_match_unsigned_case_bodies(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            expected = ("Linux-X64", "macOS-X64")
            unsigned_dir = root / "unsigned"
            unsigned_dir.mkdir()
            unsigned = {
                platform: self._native_record(candidate, platform)
                for platform in expected
            }
            for platform, record in unsigned.items():
                (unsigned_dir / f"{platform}.json").write_text(
                    json.dumps(record) + "\n", encoding="utf-8",
                )
            aggregate = {
                "format": 1,
                "project": "x86qw",
                "version": "1.0.0-rc.1",
                "commit": "c" * 40,
                "status": "complete",
                "candidate": unsigned["Linux-X64"]["candidate"],
                "platforms": unsigned,
                "signature": {"keyid": "a" * 64, "sig": "c2ln"},
            }
            aggregate_path = root / "signed-evidence.json"
            aggregate_path.write_text(json.dumps(aggregate) + "\n", encoding="utf-8")
            native_release_evidence.validate_signed_evidence_coverage(
                candidate=candidate,
                evidence=aggregate_path,
                expected_platforms=expected,
                unsigned_evidence_dir=unsigned_dir,
            )

            aggregate["platforms"]["Linux-X64"]["cases"][0]["duration_ms"] = 101
            aggregate_path.write_text(json.dumps(aggregate) + "\n", encoding="utf-8")
            with self.assertRaises(release_candidate.CandidateError):
                native_release_evidence.validate_signed_evidence_coverage(
                    candidate=candidate,
                    evidence=aggregate_path,
                    expected_platforms=expected,
                    unsigned_evidence_dir=unsigned_dir,
                )


if __name__ == "__main__":
    unittest.main()
