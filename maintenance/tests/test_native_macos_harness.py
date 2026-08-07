from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from maintenance.tools.native_handoff import (
    CANONICAL_CASES,
    NativeHandoffError,
    candidate_identity,
    validate_evidence_file,
)
from maintenance.tools.native_macos_harness import (
    HardwareObservation,
    _stage_entrypoint,
    detect_m3_hardware,
    execute_cases,
    select_platform,
)
from maintenance.tools.native_plan_adapter import generate_native_plan
from maintenance.tools.release_candidate import prepare_candidate


CONTRACT_PATH = "runtime/native-smoke/macos-arm64/entrypoint.json"
ENTRYPOINT_PATH = "runtime/native-smoke/macos-arm64/x86qw-native-smoke"


class NativeMacosHarnessTests(unittest.TestCase):
    def test_staging_preserves_entrypoint_bytes_with_newlines(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "entrypoint"
            destination = root / "runtime" / "entrypoint"
            payload = b"#!/usr/bin/env python3\r\nprint('literal')\n\x00"
            source.write_bytes(payload)
            destination.parent.mkdir()

            runtime = _stage_entrypoint(
                source=source,
                destination=destination,
                expected_size=len(payload),
                expected_digest=hashlib.sha256(payload).hexdigest(),
            )

            self.assertEqual(payload, destination.read_bytes())
            self.assertEqual(len(payload), runtime["size"])
            self.assertEqual(hashlib.sha256(payload).hexdigest(), runtime["sha256"])
            if os.name != "nt":
                self.assertTrue(os.access(destination, os.X_OK))

    def test_platform_selection_requires_an_explicit_apple_m3_observation(self) -> None:
        self.assertEqual(
            ("execute", "macOS-ARM64"),
            select_platform(
                system="Darwin",
                machine="arm64",
                chip="Apple M3 Pro",
                candidate_available=True,
            )[:2],
        )
        for chip in (None, "Apple M1 Pro", "Apple M2 Pro", "Apple M4 Pro", "unknown"):
            with self.subTest(chip=chip):
                mode, platform, reason = select_platform(
                    system="Darwin",
                    machine="arm64",
                    chip=chip,
                    candidate_available=True,
                )
                self.assertEqual("not-run", mode)
                self.assertIsNone(platform)
                self.assertIn("M3", reason)

    def test_m3_detector_uses_private_json_system_profiler_and_redacts_sensitive_fields(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []

        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({
                    "SPHardwareDataType": [{
                        "chip": "Apple M3 Pro",
                        "machine_model": "Mac15,6",
                        "machine_name": "MacBook Pro",
                        "serial_number": "DO-NOT-STORE",
                    }],
                }),
                stderr="",
            )

        observation = detect_m3_hardware(
            system="Darwin", machine="arm64", runner=runner,
        )

        self.assertEqual(
            HardwareObservation(chip="Apple M3 Pro", model="Mac15,6"),
            observation,
        )
        self.assertEqual([["/usr/sbin/system_profiler", "SPHardwareDataType", "-json"]], [call[0] for call in calls])
        self.assertNotIn("DO-NOT-STORE", repr(observation))
        self.assertEqual(2, calls[0][1]["timeout"])

    def _candidate(self, root: Path) -> tuple[Path, dict[str, str]]:
        candidate = root / "candidate"
        candidate.mkdir()
        entrypoint = candidate / ENTRYPOINT_PATH
        entrypoint.parent.mkdir(parents=True)
        entrypoint.write_text(
            """from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def argument(name: str) -> str:
    index = sys.argv.index(name)
    return sys.argv[index + 1]


candidate = Path(argument("--candidate-root"))
case = argument("--case")
scratch = Path(argument("--scratch-root"))
receipt = Path(argument("--receipt"))
artifact_name = "runtime/test/native-runtime"
artifact = candidate / artifact_name
target = scratch / "installation target"
state = scratch / "lifecycle-state.json"
if case == "install-clean-space-unicode":
    if target.exists() or state.exists():
        raise SystemExit(81)
    target.mkdir(parents=True)
    state.write_text("installed\\n", encoding="utf-8")
elif not target.is_dir() or not state.is_file():
    raise SystemExit(82)
(scratch / "execucoes.txt").open("a", encoding="utf-8").write(case + "\\n")
receipt.parent.mkdir(parents=True, exist_ok=True)
receipt.write_text(json.dumps({
    "format": 1,
    "project": "x86qw",
    "protocol": "x86qw-native-case-v1",
    "case": case,
    "artifact": {
        "name": artifact_name,
        "size": artifact.stat().st_size,
        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
    },
    "execution": {"status": "passed", "exit_code": 0},
    "state": {
        "before": "clean" if case == "install-clean-space-unicode" else "installed",
        "after": "uninstalled" if case == "lifecycle-uninstall" else "installed",
    },
}, sort_keys=True) + "\\n", encoding="utf-8")
print(f"executed {case}")
""",
            encoding="utf-8",
        )
        entrypoint.chmod(0o644)
        artifact = candidate / "runtime/test/native-runtime"
        artifact.parent.mkdir(parents=True)
        artifact.write_bytes(b"candidate-owned runtime")
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
                "runtime/test/native-runtime": {
                    "size": artifact.stat().st_size,
                    "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
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
                    "arguments": [
                        "--candidate-root", "{candidate}", "--case", name,
                        "--scratch-root", "{scratch}", "--receipt", "{receipt}",
                    ],
                    "timeout_seconds": 10,
                }
                for name in CANONICAL_CASES
            ],
        }

    def test_platform_selection_runs_only_on_real_macos_arm64_with_candidate(self) -> None:
        self.assertEqual(
            ("execute", "macOS-ARM64"),
            select_platform(
                system="Darwin",
                machine="arm64",
                chip="Apple M3 Pro",
                candidate_available=True,
            )[:2],
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
            chip="Apple M3 Pro",
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

            journal = output / "scratch" / "execucoes.txt"
            self.assertEqual(list(CANONICAL_CASES), journal.read_text(encoding="utf-8").splitlines())
            self.assertEqual(list(CANONICAL_CASES), [result["name"] for result in results])
            self.assertTrue(all(result["status"] == "passed" for result in results))
            self.assertTrue(all(result["exit_code"] == 0 for result in results))
            self.assertTrue(all((root / "logs" / result["stdout"]).is_file() for result in results))
            if os.name != "nt":
                self.assertFalse(os.access(candidate / ENTRYPOINT_PATH, os.X_OK))
                self.assertTrue(all(os.access(result["runtime"]["path"], os.X_OK) for result in results))

            handoff_path = root / "logs" / "handoff.json"
            handoff = {
                "format": 1,
                "project": "x86qw",
                "status": "passed",
                "platform": "macOS-ARM64",
                "candidate": identity,
                "environment": {
                    "system": "Darwin",
                    "machine": "arm64",
                    "chip": "Apple M3 Pro",
                    "model": "Mac15,6",
                },
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

    @unittest.skipIf(os.name == "nt", "artefatos macOS/POSIX não executáveis no Windows")
    def test_real_f_candidate_and_python_entrypoint_share_lifecycle_state_and_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input"
            source.mkdir()
            installer = source / "installer/x86qw-installer-1.0.0.zip"
            installer.parent.mkdir(parents=True)
            installer_code = b"""from pathlib import Path
import sys

action = next((item for item in sys.argv[1:] if item in {
    'install', 'update', 'upgrade', 'verify', 'repair', 'cleanup', 'uninstall',
}), None)
target = Path(sys.argv[-1])
if action == 'install':
    target.mkdir(parents=True, exist_ok=True)
    (target / 'managed-state').write_text('installed\\n', encoding='utf-8')
elif action in {'update', 'upgrade', 'verify', 'repair', 'cleanup', 'uninstall'}:
    if not target.is_dir() or not (target / 'managed-state').is_file():
        raise SystemExit(41)
else:
    raise SystemExit(42)
"""
            with zipfile.ZipFile(installer, "w") as archive:
                archive.writestr("bin/x86qw.pyz", installer_code)

            def client_archive(channel: str) -> None:
                archive_path = source / f"runtime/clients/ezquake/{channel}/fixture/macos-universal/{channel}.zip"
                archive_path.parent.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(archive_path, "w") as archive:
                    archive.writestr(
                        "ezQuake.app/Contents/MacOS/ezQuake",
                        b"#!/bin/sh\nexit 0\n",
                    )

            client_archive("stable")
            client_archive("nightly")
            for relative in (
                "runtime/servers/mvdsv/fixture/x86qw/runtime/macos-arm64/mvdsv",
                "runtime/services/qtv/fixture/x86qw/runtime/macos-arm64/qtv",
                "runtime/services/qwfwd/fixture/x86qw/runtime/macos-arm64/qwfwd",
            ):
                service = source / relative
                service.parent.mkdir(parents=True, exist_ok=True)
                service.write_bytes(b"#!/bin/sh\nexit 0\n")

            entrypoint = source / ENTRYPOINT_PATH
            entrypoint.parent.mkdir(parents=True, exist_ok=True)
            entrypoint.write_bytes(
                (Path(__file__).resolve().parents[2] / "maintenance/native_case_entrypoint.py").read_bytes()
            )
            contract = source / CONTRACT_PATH
            contract.write_text(
                json.dumps({
                    "format": 1,
                    "project": "x86qw",
                    "platform": "macOS-ARM64",
                    "protocol": "x86qw-native-case-v1",
                    "entrypoint_artifact": ENTRYPOINT_PATH,
                }, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            candidate = root / "candidate"
            prepare_candidate(
                source=source,
                output=candidate,
                version="1.0.0",
                commit="a" * 40,
                generated_at="2026-08-07T00:00:00Z",
            )
            candidate_sha256 = hashlib.sha256(
                (candidate / "candidate.json").read_bytes()
            ).hexdigest()
            plan_path = root / "native-plan.json"
            plan = generate_native_plan(
                candidate=candidate,
                expected_candidate_sha256=candidate_sha256,
                entrypoint_contract=CONTRACT_PATH,
                output=plan_path,
            )
            before = {
                path.relative_to(candidate).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in candidate.rglob("*") if path.is_file()
            }

            results = execute_cases(
                candidate=candidate,
                plan=plan,
                output_dir=root / "handoff",
            )

            self.assertEqual(list(CANONICAL_CASES), [item["name"] for item in results])
            self.assertTrue(all(item["status"] == "passed" for item in results))
            self.assertNotEqual(ENTRYPOINT_PATH, results[0]["candidate_artifact"])
            self.assertTrue(all((root / "handoff" / item["receipt"]).is_file() for item in results))
            after = {
                path.relative_to(candidate).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in candidate.rglob("*") if path.is_file()
            }
            self.assertEqual(before, after)

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
                "environment": {
                    "system": "Darwin",
                    "machine": "arm64",
                    "chip": "Apple M3 Pro",
                    "model": "Mac15,6",
                },
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
