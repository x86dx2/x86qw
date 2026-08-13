from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from maintenance.tools import native_m3_harness
from x86qw_runtime.contracts.native_evidence import CASE_ASSERTIONS, CANONICAL_CASES


class NativeM3HarnessTests(unittest.TestCase):
    def test_hardware_gate_rejects_non_macos_before_probing_hardware(self) -> None:
        with mock.patch.object(native_m3_harness.host_platform, "system", return_value="Linux"), \
             mock.patch.object(native_m3_harness.subprocess, "run") as run:
            with self.assertRaisesRegex(native_m3_harness.NativeM3Error, "macOS arm64"):
                native_m3_harness._m3_environment()
            run.assert_not_called()

    def test_plan_binds_all_cases_to_the_exact_candidate_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "plan.json"
            path.write_text(
                json.dumps({
                    "format": 1,
                    "project": "x86qw",
                    "platform": "macOS-ARM64",
                    "candidate": {
                        "version": "1.0.0-rc.1",
                        "commit": "0" * 40,
                        "manifest_sha256": "0" * 64,
                    },
                    "cases": [],
                }),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(native_m3_harness.NativeM3Error, "todos os casos"):
                native_m3_harness._plan(
                    path,
                    {
                        "version": "1.0.0-rc.1",
                        "commit": "0" * 40,
                        "manifest_sha256": "0" * 64,
                    },
                )

    def test_plan_rejects_identity_mismatch_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "plan.json"
            path.write_text(
                json.dumps({
                    "format": 1,
                    "project": "x86qw",
                    "platform": "macOS-ARM64",
                    "candidate": {
                        "version": "1.0.0-rc.1",
                        "commit": "0" * 40,
                        "manifest_sha256": "0" * 64,
                    },
                    "cases": [],
                }),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(native_m3_harness.NativeM3Error, "diverge"):
                native_m3_harness._plan(
                    path,
                    {
                        "version": "1.0.0-rc.1",
                        "commit": "1" * 40,
                        "manifest_sha256": "0" * 64,
                    },
                )

    def test_plan_requires_an_execution_attestation_for_each_case(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "plan.json"
            identity = {
                "version": "1.0.0-rc.1",
                "commit": "0" * 40,
                "manifest_sha256": "0" * 64,
            }
            cases = []
            for name in CANONICAL_CASES:
                cases.append({
                    "name": name,
                    "command": ["x86qw.sh", "version"],
                    "assertions": sorted(CASE_ASSERTIONS[name]),
                    "artifacts": [
                        {
                            "path": f"{name}/{assertion}.log",
                            "kind": f"{name}-{assertion}",
                            "assertion": assertion,
                        }
                        for assertion in sorted(CASE_ASSERTIONS[name])
                    ],
                    "timeout_seconds": 30,
                })
            path.write_text(json.dumps({
                "format": 1,
                "project": "x86qw",
                "platform": "macOS-ARM64",
                "candidate": identity,
                "cases": cases,
            }), encoding="utf-8")
            with self.assertRaisesRegex(native_m3_harness.NativeM3Error, "atestação"):
                native_m3_harness._plan(path, identity)

    def test_observed_artifacts_require_the_case_nonce(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            artifact = output / "attestation.log"
            artifact.write_text("wrong nonce\n", encoding="utf-8")
            case = {
                "artifacts": [{
                    "path": "attestation.log",
                    "kind": "case-attestation",
                    "assertion": "process-exited",
                }],
            }
            with self.assertRaisesRegex(native_m3_harness.NativeM3Error, "ligada"):
                native_m3_harness._observed_artifacts(
                    case, output, nonce="expected", started_ns=0,
                )
            artifact.write_text("expected nonce\n", encoding="utf-8")
            observed = native_m3_harness._observed_artifacts(
                case, output, nonce="expected", started_ns=0,
            )
            self.assertEqual("case-attestation", observed[0]["kind"])

    def test_command_boundary_rejects_shell_control_and_fake_markers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for command in (
                [sys.executable, "-c", "print('ok'); print('fake')"],
                [sys.executable, "-c", "print('ok') | cat"],
            ):
                with self.subTest(command=command):
                    with self.assertRaises(native_m3_harness.NativeM3Error):
                        native_m3_harness._expand_command(
                            command,
                            candidate=root,
                            scratch=root,
                            output=root,
                            case=CANONICAL_CASES[0],
                        )

    def test_process_group_termination_reaches_native_case_descendants(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "child-terminated"
            ready = Path(temporary) / "child-ready"
            child_code = (
                "import signal,sys,time\n"
                "def stop(*_):\n"
                "    with open(sys.argv[1], 'w', encoding='utf-8'): pass\n"
                "    raise SystemExit(0)\n"
                "signal.signal(signal.SIGTERM, stop)\n"
                "with open(sys.argv[2], 'w', encoding='utf-8'): pass\n"
                "time.sleep(60)\n"
            )
            parent_code = (
                "import subprocess,sys,time\n"
                "subprocess.Popen([sys.executable, '-c', sys.argv[2], sys.argv[1], sys.argv[3]])\n"
                "time.sleep(60)\n"
            )
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    parent_code,
                    str(marker),
                    child_code,
                    str(ready),
                ],
                start_new_session=True,
            )
            try:
                for _ in range(100):
                    if ready.exists():
                        break
                    time.sleep(0.01)
                self.assertTrue(ready.is_file())
                native_m3_harness._terminate_process_group(process)
                process.wait(timeout=5)
                for _ in range(100):
                    if marker.exists():
                        break
                    time.sleep(0.01)
                self.assertTrue(marker.is_file())
            finally:
                if process.poll() is None:
                    native_m3_harness._terminate_process_group(process, force=True)
                    process.wait(timeout=5)

    def test_format_two_cli_delegates_to_the_canonical_macos_harness(self) -> None:
        from maintenance.tools import native_macos_harness

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = root / "plan.json"
            plan.write_text(json.dumps({"format": 2}), encoding="utf-8")
            with mock.patch.object(native_macos_harness, "main", return_value=0) as canonical:
                result = native_m3_harness.main([
                    "run",
                    "--candidate", str(root / "candidate"),
                    "--plan", str(plan),
                    "--output-dir", str(root / "output"),
                ])
            self.assertEqual(0, result)
            canonical.assert_called_once_with([
                "run",
                "--candidate", str(root / "candidate"),
                "--plan", str(plan),
                "--output-dir", str(root / "output"),
            ])

    def test_legacy_run_native_removes_shared_scratch_when_case_setup_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = root / "candidate"
            candidate.mkdir()
            output = root / "output"
            identity = {
                "version": "1.0.0-rc.1",
                "commit": "c" * 40,
                "manifest_sha256": "d" * 64,
            }
            environment = {
                "os": "macOS",
                "architecture": "arm64",
                "standard_user": True,
                "elevated": False,
                "distro": None,
                "distro_version": None,
                "glibc_version": None,
                "chip": "Apple M3 Pro",
                "model": "Mac15,6",
            }
            plan = [{
                "name": CANONICAL_CASES[0],
                "command": ["x86qw.sh", "version"],
                "assertions": [],
                "artifacts": [],
                "timeout_seconds": 10,
            }]

            with (
                mock.patch.object(native_m3_harness, "_m3_environment", return_value=environment),
                mock.patch.object(native_m3_harness, "_identity", return_value=identity),
                mock.patch.object(native_m3_harness, "_plan", return_value=plan),
                mock.patch.object(
                    native_m3_harness,
                    "_expand_command",
                    side_effect=native_m3_harness.NativeM3Error("falha de preparação simulada"),
                ),
            ):
                with self.assertRaisesRegex(native_m3_harness.NativeM3Error, "falha de preparação simulada"):
                    native_m3_harness.run_native(
                        candidate=candidate,
                        plan_path=root / "plan.json",
                        output_dir=output,
                    )

            self.assertFalse((output / "scratch").exists())


if __name__ == "__main__":
    unittest.main()
