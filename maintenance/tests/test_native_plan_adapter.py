from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from maintenance.tools.native_handoff import NativeHandoffError, validate_plan
from maintenance.tools.native_plan_adapter import PlanNotRun, generate_native_plan


EXPECTED_CASES = (
    "install-clean-space-unicode",
    "install-existing-space-unicode",
    "client-stable-window-map-exit",
    "client-nightly-window-map-exit",
    "game-ktx",
    "game-final-arena",
    "game-pro-x",
    "game-team-fortress",
    "game-td2",
    "mvdsv-mvd",
    "qtv-stream",
    "qwfwd-forward",
    "lifecycle-update",
    "lifecycle-upgrade",
    "lifecycle-verify",
    "lifecycle-repair",
    "lifecycle-cleanup",
    "lifecycle-uninstall",
)
CONTRACT_PATH = "runtime/native-smoke/macos-arm64/entrypoint.json"
ENTRYPOINT_PATH = "runtime/native-smoke/macos-arm64/x86qw-native-smoke"


class NativePlanAdapterTests(unittest.TestCase):
    def _candidate(
        self,
        root: Path,
        *,
        include_contract: bool = True,
        include_entrypoint: bool = True,
    ) -> tuple[Path, dict[str, str]]:
        candidate = root / "candidate"
        candidate.mkdir(parents=True)
        artifacts: dict[str, dict[str, object]] = {}
        if include_entrypoint:
            entrypoint = candidate / ENTRYPOINT_PATH
            entrypoint.parent.mkdir(parents=True)
            entrypoint.write_bytes(b"#!/bin/sh\nexit 0\n")
            artifacts[ENTRYPOINT_PATH] = {
                "size": entrypoint.stat().st_size,
                "sha256": hashlib.sha256(entrypoint.read_bytes()).hexdigest(),
            }
        if include_contract:
            contract = candidate / CONTRACT_PATH
            contract.parent.mkdir(parents=True, exist_ok=True)
            contract.write_text(json.dumps({
                "format": 1,
                "project": "x86qw",
                "platform": "macOS-ARM64",
                "protocol": "x86qw-native-case-v1",
                "entrypoint_artifact": ENTRYPOINT_PATH,
            }, sort_keys=True) + "\n", encoding="utf-8")
            artifacts[CONTRACT_PATH] = {
                "size": contract.stat().st_size,
                "sha256": hashlib.sha256(contract.read_bytes()).hexdigest(),
            }
        payload = candidate / "runtime/clients/stable.zip"
        payload.parent.mkdir(parents=True, exist_ok=True)
        payload.write_bytes(b"client bytes")
        artifacts["runtime/clients/stable.zip"] = {
            "size": payload.stat().st_size,
            "sha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
        }
        manifest = {
            "format": 1,
            "project": "x86qw",
            "version": "1.0.0-rc.1",
            "commit": "c" * 40,
            "generated_at": "2026-08-06T00:00:00Z",
            "artifacts": artifacts,
            "artifact_count": len(artifacts),
            "metadata": {},
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

    def test_contract_owned_entrypoint_generates_literal_portable_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first_candidate, first_identity = self._candidate(root / "one")
            second_candidate, second_identity = self._candidate(root / "two")
            first = root / "first-plan.json"
            second = root / "second-plan.json"

            plan = generate_native_plan(
                candidate=first_candidate,
                expected_candidate_sha256=first_identity["manifest_sha256"],
                entrypoint_contract=CONTRACT_PATH,
                output=first,
            )
            generate_native_plan(
                candidate=second_candidate,
                expected_candidate_sha256=second_identity["manifest_sha256"],
                entrypoint_contract=CONTRACT_PATH,
                output=second,
            )

            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(2, plan["format"])
            self.assertEqual(first_identity, plan["candidate"])
            self.assertEqual(CONTRACT_PATH, plan["entrypoint"]["contract_artifact"])
            self.assertEqual(ENTRYPOINT_PATH, plan["entrypoint"]["artifact"])
            self.assertEqual(list(EXPECTED_CASES), [case["name"] for case in plan["cases"]])
            self.assertEqual(
                [
                    ["--candidate-root", "{candidate}", "--case", name]
                    for name in EXPECTED_CASES
                ],
                [case["arguments"] for case in plan["cases"]],
            )
            self.assertEqual(18, len(validate_plan(plan, candidate=first_candidate)))

    def test_current_f_shape_without_contract_is_not_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, identity = self._candidate(root, include_contract=False, include_entrypoint=False)
            output = root / "plan.json"

            with self.assertRaises(PlanNotRun):
                generate_native_plan(
                    candidate=candidate,
                    expected_candidate_sha256=identity["manifest_sha256"],
                    entrypoint_contract=CONTRACT_PATH,
                    output=output,
                )
            self.assertFalse(output.exists())

    def test_unregistered_or_hash_divergent_contract_and_entrypoint_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, identity = self._candidate(root)
            output = root / "plan.json"
            manifest = json.loads((candidate / "candidate.json").read_text(encoding="utf-8"))
            manifest["artifacts"].pop(CONTRACT_PATH)
            (candidate / "candidate.json").write_text(
                json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
            )
            changed_identity = hashlib.sha256((candidate / "candidate.json").read_bytes()).hexdigest()
            with self.assertRaisesRegex(NativeHandoffError, "registrado"):
                generate_native_plan(
                    candidate=candidate,
                    expected_candidate_sha256=changed_identity,
                    entrypoint_contract=CONTRACT_PATH,
                    output=output,
                )
            self.assertFalse(output.exists())

            other = root / "other"
            other.mkdir()
            candidate, identity = self._candidate(other)
            (candidate / ENTRYPOINT_PATH).write_bytes(b"tampered")
            with self.assertRaisesRegex(NativeHandoffError, "candidato exato"):
                generate_native_plan(
                    candidate=candidate,
                    expected_candidate_sha256=identity["manifest_sha256"],
                    entrypoint_contract=CONTRACT_PATH,
                    output=output,
                )
            self.assertFalse(output.exists())

    def test_old_external_runtime_plan_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, identity = self._candidate(root)
            legacy = {
                "format": 1,
                "project": "x86qw",
                "platform": "macOS-ARM64",
                "candidate": identity,
                "cases": [],
            }
            with self.assertRaisesRegex(NativeHandoffError, "formato"):
                validate_plan(legacy, candidate=candidate)

    def test_candidate_sha_output_and_unsafe_paths_fail_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, identity = self._candidate(root)
            before = {
                path.relative_to(candidate): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in candidate.rglob("*") if path.is_file()
            }
            output = root / "plan.json"
            with self.assertRaisesRegex(NativeHandoffError, "candidate-sha256"):
                generate_native_plan(
                    candidate=candidate,
                    expected_candidate_sha256="0" * 64,
                    entrypoint_contract=CONTRACT_PATH,
                    output=output,
                )
            with self.assertRaises(NativeHandoffError):
                generate_native_plan(
                    candidate=candidate,
                    expected_candidate_sha256=identity["manifest_sha256"],
                    entrypoint_contract="../entrypoint.json",
                    output=output,
                )
            with self.assertRaises(NativeHandoffError):
                generate_native_plan(
                    candidate=candidate,
                    expected_candidate_sha256=identity["manifest_sha256"],
                    entrypoint_contract=CONTRACT_PATH,
                    output=candidate / "native-plan.json",
                )
            output.write_text("preserve\n", encoding="utf-8")
            with self.assertRaisesRegex(NativeHandoffError, "já existe"):
                generate_native_plan(
                    candidate=candidate,
                    expected_candidate_sha256=identity["manifest_sha256"],
                    entrypoint_contract=CONTRACT_PATH,
                    output=output,
                )
            after = {
                path.relative_to(candidate): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in candidate.rglob("*") if path.is_file()
            }
            self.assertEqual(before, after)
            self.assertEqual("preserve\n", output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
