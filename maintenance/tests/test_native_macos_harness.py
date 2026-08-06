from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from maintenance.tools.native_handoff import (
    CANONICAL_CASES,
    NativeHandoffError,
    candidate_identity,
    validate_evidence_file,
)
from maintenance.tools.native_macos_harness import execute_cases, select_platform


CONTRACT_PATH = "runtime/native-smoke/macos-arm64/entrypoint.json"
ENTRYPOINT_PATH = "runtime/native-smoke/macos-arm64/x86qw-native-smoke"


class NativeMacosHarnessTests(unittest.TestCase):
    def _candidate(self, root: Path) -> tuple[Path, dict[str, str]]:
        candidate = root / "candidate"
        candidate.mkdir()
        entrypoint = candidate / ENTRYPOINT_PATH
        entrypoint.parent.mkdir(parents=True)
        entrypoint.write_text(
            """#!/bin/sh
test "$1" = "--candidate-root" || exit 64
test "$3" = "--case" || exit 64
printf '%s\\n' "$4" >> "$TMPDIR/execucoes.txt"
printf 'executed %s\\n' "$4"
""",
            encoding="utf-8",
        )
        entrypoint.chmod(0o644)
        contract = candidate / CONTRACT_PATH
        contract.write_text(
            json.dumps(
                {
                    "format": 1,
                    "project": "x86qw",
                    "platform": "macOS-ARM64",
                    "protocol": "x86qw-native-case-v1",
                    "entrypoint_artifact": ENTRYPOINT_PATH,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        entrypoint_bytes = entrypoint.read_bytes()
        contract_bytes = contract.read_bytes()
        manifest = {
            "format": 2,
            "project": "x86qw",
            "version": "1.0.0-rc.1",
            "commit": "c" * 40,
            "artifacts": {
                CONTRACT_PATH: {
                    "size": len(contract_bytes),
                    "sha256": hashlib.sha256(contract_bytes).hexdigest(),
                },
                ENTRYPOINT_PATH: {
                    "size": len(entrypoint_bytes),
                    "sha256": hashlib.sha256(entrypoint_bytes).hexdigest(),
                },
            },
        }
        manifest_path = candidate / "candidate.json"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
        identity = {
            "version": manifest["version"],
            "commit": manifest["commit"],
            "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        }
        return candidate, identity

    def _plan(self, candidate: Path, identity: dict[str, str]) -> dict[str, object]:
        contract_bytes = (candidate / CONTRACT_PATH).read_bytes()
        entrypoint_bytes = (candidate / ENTRYPOINT_PATH).read_bytes()
        return {
            "format": 2,
            "project": "x86qw",
            "platform": "macOS-ARM64",
            "candidate": identity,
            "entrypoint": {
                "contract_artifact": CONTRACT_PATH,
                "contract_sha256": hashlib.sha256(contract_bytes).hexdigest(),
                "artifact": ENTRYPOINT_PATH,
                "size": len(entrypoint_bytes),
                "sha256": hashlib.sha256(entrypoint_bytes).hexdigest(),
            },
            "cases": [
                {
                    "name": name,
                    "arguments": ["--candidate-root", "{candidate}", "--case", name],
                    "timeout_seconds": 10,
                }
                for name in CANONICAL_CASES
            ],
        }

    def test_platform_selection_runs_only_on_real_macos_arm64_with_candidate(self) -> None:
        self.assertEqual(
            ("execute", "macOS-ARM64"),
            select_platform(system="Darwin", machine="arm64", candidate_available=True)[:2],
        )
        for system, machine, available in (
            ("Darwin", "x86_64", True),
            ("Linux", "aarch64", True),
            ("Windows", "AMD64", True),
            ("Darwin", "arm64", False),
        ):
            with self.subTest(system=system, machine=machine, available=available):
                mode, platform, reason = select_platform(
                    system=system,
                    machine=machine,
                    candidate_available=available,
                )
                self.assertEqual("not-run", mode)
                self.assertIsNone(platform)
                self.assertTrue(reason)

        mode, platform, reason = select_platform(
            system="Darwin",
            machine="arm64",
            candidate_available=True,
            plan_available=False,
        )
        self.assertEqual("not-run", mode)
        self.assertIsNone(platform)
        self.assertIn("plano", reason)

    def test_portable_executor_runs_the_complete_lifecycle_in_canonical_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, identity = self._candidate(root)
            output = root / "logs"
            results = execute_cases(
                candidate=candidate,
                plan=self._plan(candidate, identity),
                output_dir=output,
            )

            journal = output / "execucoes.txt"
            self.assertEqual(list(CANONICAL_CASES), journal.read_text(encoding="utf-8").splitlines())
            self.assertEqual(list(CANONICAL_CASES), [result["name"] for result in results])
            self.assertTrue(all(result["status"] == "passed" for result in results))
            self.assertTrue(all(result["exit_code"] == 0 for result in results))
            self.assertTrue(all((root / "logs" / result["stdout"]).is_file() for result in results))
            self.assertFalse(os.access(candidate / ENTRYPOINT_PATH, os.X_OK))
            self.assertTrue(all(os.access(result["runtime"]["path"], os.X_OK) for result in results))

            handoff_path = root / "logs" / "handoff.json"
            handoff = {
                "format": 1,
                "project": "x86qw",
                "status": "passed",
                "platform": "macOS-ARM64",
                "candidate": identity,
                "environment": {"system": "Darwin", "machine": "arm64"},
                "runtime_executed": True,
                "cases": results,
                "reason": None,
            }
            handoff_path.write_text(json.dumps(handoff) + "\n", encoding="utf-8")
            self.assertEqual("passed", validate_evidence_file(handoff_path, candidate=candidate)["status"])

            handoff["cases"][0]["runtime"]["sha256"] = "0" * 64
            handoff_path.write_text(json.dumps(handoff) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(NativeHandoffError, "runtime exato"):
                validate_evidence_file(handoff_path, candidate=candidate)

    def test_candidate_mismatch_is_rejected_before_any_command_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, identity = self._candidate(root)
            plan = self._plan(candidate, identity)
            plan["candidate"] = {**identity, "manifest_sha256": "0" * 64}

            with self.assertRaisesRegex(NativeHandoffError, "candidato exato"):
                execute_cases(candidate=candidate, plan=plan, output_dir=root / "logs")

            self.assertFalse((root / "logs").exists())

    def test_entrypoint_mismatch_is_rejected_before_any_command_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, identity = self._candidate(root)
            plan = self._plan(candidate, identity)
            plan["entrypoint"]["sha256"] = "0" * 64

            with self.assertRaisesRegex(NativeHandoffError, "entrypoint"):
                execute_cases(candidate=candidate, plan=plan, output_dir=root / "logs")

            self.assertFalse((root / "logs").exists())

    def test_missing_or_not_run_handoff_is_never_accepted_as_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, _identity = self._candidate(root)
            with self.assertRaisesRegex(NativeHandoffError, "ausente"):
                validate_evidence_file(root / "missing.json", candidate=candidate)

            preview = root / "preview.json"
            preview.write_text(
                json.dumps(
                    {
                        "format": 1,
                        "project": "x86qw",
                        "status": "not-run",
                        "platform": None,
                        "candidate": None,
                        "environment": {"system": "Linux", "machine": "x86_64"},
                        "runtime_executed": False,
                        "cases": [],
                        "reason": "host não é macOS arm64",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(NativeHandoffError, "não é evidência"):
                validate_evidence_file(preview, candidate=candidate)

    def test_cli_keeps_missing_candidate_as_not_run_without_requiring_a_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "preview"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "maintenance.tools.native_macos_harness",
                    "run",
                    "--candidate",
                    str(root / "missing-candidate"),
                    "--plan",
                    str(root / "missing-plan.json"),
                    "--output-dir",
                    str(output),
                ],
                cwd=Path(__file__).resolve().parents[2],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(2, result.returncode, result.stderr)
            preview = json.loads((output / "handoff.json").read_text(encoding="utf-8"))
            self.assertEqual("not-run", preview["status"])
            self.assertFalse(preview["runtime_executed"])

    def test_evidence_validator_rejects_candidate_identity_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, identity = self._candidate(root)
            value = {
                "format": 1,
                "project": "x86qw",
                "status": "passed",
                "platform": "macOS-ARM64",
                "candidate": {**identity, "commit": "d" * 40},
                "environment": {"system": "Darwin", "machine": "arm64"},
                "runtime_executed": True,
                "cases": [],
                "reason": None,
            }
            evidence = root / "handoff.json"
            evidence.write_text(json.dumps(value) + "\n", encoding="utf-8")

            self.assertEqual(identity, candidate_identity(candidate))
            with self.assertRaisesRegex(NativeHandoffError, "candidato exato"):
                validate_evidence_file(evidence, candidate=candidate)


if __name__ == "__main__":
    unittest.main()
