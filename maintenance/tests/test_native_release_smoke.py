from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from maintenance.tools import native_release_smoke, release_candidate
from x86qw_runtime.contracts.native_evidence import CASE_ASSERTIONS, CANONICAL_CASES, NATIVE_EVIDENCE_FORMAT


ROOT = Path(__file__).resolve().parents[2]


class NativeReleaseSmokeTests(unittest.TestCase):
    def _candidate(self, root: Path) -> Path:
        source = root / "build"
        source.mkdir()
        (source / "artifact.zip").write_bytes(b"native smoke candidate")
        candidate = root / "candidate"
        release_candidate.prepare_candidate(
            source=source,
            output=candidate,
            version="1.0.0-rc.1",
            commit="d" * 40,
            generated_at="2026-08-04T00:00:00Z",
        )
        return candidate

    def _handoff(self, candidate: Path, path: Path, *, platform: str = "Linux-X64") -> Path:
        manifest = candidate / "candidate.json"
        identity = {
            "version": "1.0.0-rc.1",
            "commit": "d" * 40,
            "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        }
        environment = {
            "os": "Linux",
            "architecture": "x86_64",
            "standard_user": True,
            "elevated": False,
            "distro": "ubuntu",
            "distro_version": "24.04",
            "glibc_version": "2.39",
        }
        cases = []
        for name in CANONICAL_CASES:
            artifact_path = path.parent / "logs" / f"{name}.log"
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            payload = f"native evidence: {name}\n".encode("utf-8")
            artifact_path.write_bytes(payload)
            cases.append({
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
                        "size": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    },
                ],
            })
        path.write_text(
            json.dumps(
                {
                    "format": NATIVE_EVIDENCE_FORMAT,
                    "project": "x86qw",
                    "status": "passed",
                    "platform": platform,
                    "completed_at": "2026-08-04T12:00:00Z",
                    "candidate": identity,
                    "environment": environment,
                    "runtime_executed": True,
                    "cases": cases,
                    "secrets": "redacted",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def test_normalize_requires_exact_canonical_cases_and_binds_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            handoff = self._handoff(candidate, root / "handoff.json")
            report = native_release_smoke.normalize_native_smoke(
                candidate=candidate,
                platform="Linux-X64",
                handoff=handoff,
            )
            self.assertEqual(
                tuple(f"x86qw-native-smoke {name}" for name in CANONICAL_CASES),
                tuple(" ".join(case["command"]) for case in report["cases"]),
            )
            self.assertTrue(report["runtime_executed"])

            payload = json.loads(handoff.read_text(encoding="utf-8"))
            payload["cases"][0]["status"] = "skipped"
            handoff.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            with self.assertRaises(release_candidate.CandidateError):
                native_release_smoke.normalize_native_smoke(
                    candidate=candidate,
                    platform="Linux-X64",
                    handoff=handoff,
                )

    def test_macos_arm64_handoff_requires_m3_hardware_attestation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            handoff = self._handoff(candidate, root / "handoff.json")
            payload = json.loads(handoff.read_text(encoding="utf-8"))
            payload["platform"] = "macOS-ARM64"
            payload["environment"] = {
                "os": "macOS",
                "architecture": "arm64",
                "standard_user": True,
                "elevated": False,
                "distro": None,
                "distro_version": None,
                "glibc_version": None,
            }
            payload["hardware"] = {"chip": "Apple M3 Pro", "model": "Mac15,6"}
            handoff.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            native_release_smoke.normalize_native_smoke(
                candidate=candidate,
                platform="macOS-ARM64",
                handoff=handoff,
            )

            payload.pop("hardware")
            handoff.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(release_candidate.CandidateError, "hardware"):
                native_release_smoke.normalize_native_smoke(
                    candidate=candidate,
                    platform="macOS-ARM64",
                    handoff=handoff,
                )

            payload["hardware"] = {"chip": "Apple M2 Pro", "model": "Mac14,9"}
            handoff.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(release_candidate.CandidateError, "Apple M3"):
                native_release_smoke.normalize_native_smoke(
                    candidate=candidate,
                    platform="macOS-ARM64",
                    handoff=handoff,
                )

            payload["cases"][0]["status"] = "passed"
            payload["cases"][0]["command"] = ["x86qw", "--help"]
            payload["candidate"]["commit"] = "e" * 40
            handoff.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            with self.assertRaises(release_candidate.CandidateError):
                native_release_smoke.normalize_native_smoke(
                    candidate=candidate,
                    platform="Linux-X64",
                    handoff=handoff,
                )

            payload["cases"] = payload["cases"][:2]
            handoff.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            with self.assertRaises(release_candidate.CandidateError):
                native_release_smoke.normalize_native_smoke(
                    candidate=candidate,
                    platform="Linux-X64",
                    handoff=handoff,
                )

    def test_normalize_rejects_mock_commands_and_identity_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            handoff = self._handoff(candidate, root / "handoff.json")
            payload = json.loads(handoff.read_text(encoding="utf-8"))
            payload["cases"][0]["command"] = ["mock-runtime", "--help"]
            handoff.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            with self.assertRaises(release_candidate.CandidateError):
                native_release_smoke.normalize_native_smoke(
                    candidate=candidate,
                    platform="Linux-X64",
                    handoff=handoff,
                )

    def test_normalize_accepts_candidate_paths_that_contain_fixture_word(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            handoff = self._handoff(candidate, root / "handoff.json")
            payload = json.loads(handoff.read_text(encoding="utf-8"))
            payload["cases"][0]["command"] = [
                "python3",
                "/private/tmp/candidate-with-fixtures/x86qw-native-smoke",
                "--case",
                "install-clean-space-unicode",
            ]
            handoff.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            report = native_release_smoke.normalize_native_smoke(
                candidate=candidate,
                platform="Linux-X64",
                handoff=handoff,
            )
            self.assertIn("candidate-with-fixtures", report["cases"][0]["command"][1])

    def test_normalize_rejects_incomplete_linux_environment_and_elevated_windows(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            handoff = self._handoff(candidate, root / "handoff.json")
            payload = json.loads(handoff.read_text(encoding="utf-8"))
            payload["environment"]["glibc_version"] = None
            handoff.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            with self.assertRaises(release_candidate.CandidateError):
                native_release_smoke.normalize_native_smoke(
                    candidate=candidate,
                    platform="Linux-X64",
                    handoff=handoff,
                )

            payload["environment"] = {
                "os": "Windows",
                "architecture": "x64",
                "standard_user": True,
                "elevated": True,
                "distro": None,
                "distro_version": None,
                "glibc_version": None,
            }
            payload["platform"] = "Windows-X64"
            handoff.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            with self.assertRaises(release_candidate.CandidateError):
                native_release_smoke.normalize_native_smoke(
                    candidate=candidate,
                    platform="Windows-X64",
                    handoff=handoff,
                )

    def test_normalize_rejects_elevated_environment_and_invalid_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            handoff = self._handoff(candidate, root / "handoff.json")
            payload = json.loads(handoff.read_text(encoding="utf-8"))
            payload["environment"]["elevated"] = True
            handoff.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            with self.assertRaises(release_candidate.CandidateError):
                native_release_smoke.normalize_native_smoke(
                    candidate=candidate,
                    platform="Linux-X64",
                    handoff=handoff,
                )

            payload["environment"]["elevated"] = False
            payload["cases"][0]["artifacts"][0]["sha256"] = "not-a-sha256"
            handoff.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            with self.assertRaises(release_candidate.CandidateError):
                native_release_smoke.normalize_native_smoke(
                    candidate=candidate,
                    platform="Linux-X64",
                    handoff=handoff,
                )

    def test_normalize_rejects_artifact_ancestor_symlink_escape(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            handoff = self._handoff(candidate, root / "handoff.json")
            outside = root / "outside"
            outside.mkdir()
            (outside / "logs").mkdir()
            original = root / "logs"
            original.rename(outside / "logs")
            original.symlink_to(outside / "logs", target_is_directory=True)

            with self.assertRaises(release_candidate.CandidateError):
                native_release_smoke.normalize_native_smoke(
                    candidate=candidate,
                    platform="Linux-X64",
                    handoff=handoff,
                )

    def test_normalize_rejects_symlinked_handoff_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            real_root = root / "handoff-real"
            real_root.mkdir()
            handoff = self._handoff(candidate, real_root / "handoff.json")
            alias = root / "handoff-alias"
            alias.symlink_to(real_root, target_is_directory=True)

            with self.assertRaises(release_candidate.CandidateError):
                native_release_smoke.normalize_native_smoke(
                    candidate=candidate,
                    platform="Linux-X64",
                    handoff=alias / "handoff.json",
                )

    def test_cli_writes_once_and_fails_closed_without_handoff(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            handoff = self._handoff(candidate, root / "handoff.json")
            output = root / "smoke-report.json"
            command = [
                sys.executable,
                str(ROOT / "maintenance/tools/native_release_smoke.py"),
                "--candidate", str(candidate),
                "--platform", "Linux-X64",
                "--handoff", str(handoff),
                "--output", str(output),
            ]
            result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertEqual(0, result.returncode, result.stderr)
            first = output.read_bytes()
            second = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertNotEqual(0, second.returncode)
            self.assertEqual(first, output.read_bytes())


if __name__ == "__main__":
    unittest.main()
