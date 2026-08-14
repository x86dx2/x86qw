from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from maintenance.tools.native_handoff import (
    CANONICAL_CASES,
    NativeHandoffError,
    validate_case_receipt,
)
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
        entrypoint = candidate / "entrypoint.py"
        entrypoint.write_bytes(b"candidate entrypoint bytes")
        entrypoint_digest = hashlib.sha256(entrypoint.read_bytes()).hexdigest()
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
                },
                "entrypoint.py": {
                    "size": entrypoint.stat().st_size,
                    "sha256": entrypoint_digest,
                },
            },
            "artifact_count": 2,
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
        runtime.write_bytes((root / "candidate" / "entrypoint.py").read_bytes())
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
            receipt = evidence / f"receipts/{index:02d}-{name}.json"
            receipt.parent.mkdir(parents=True, exist_ok=True)
            receipt_value = {
                    "format": 1,
                    "project": "x86qw",
                    "protocol": "x86qw-native-case-v1",
                    "case": name,
                    "artifact": {
                        "name": "artifact.zip",
                        "size": len(b"immutable candidate bytes"),
                        "sha256": hashlib.sha256(b"immutable candidate bytes").hexdigest(),
                    },
                    "execution": {"status": "passed", "exit_code": 0},
                    "state": {
                        "before": (
                            "clean" if index == 1
                            else "uninstalled" if name == "lifecycle-purge"
                            else "installed"
                        ),
                        "after": (
                            "uninstalled"
                            if name in {"lifecycle-uninstall", "lifecycle-purge"}
                            else "installed"
                        ),
                    },
                }
            if name == "install-clean-space-unicode":
                receipt_value["observations"] = {
                    "launcher": "x86qw.sh",
                    "commands": [
                        {"name": "help", "exit_code": 0},
                        {"name": "version", "exit_code": 0},
                        {"name": "changes", "exit_code": 0},
                        {"name": "migrate", "exit_code": 0},
                    ],
                    "help_lists_changes": True,
                    "help_lists_migrate": True,
                    "version_matches": True,
                    "changes_executed": True,
                    "migrate_dry_run_executed": True,
                    "termination": "controlled",
                    "process_exit_code": 0,
                }
            if name == "mvdsv-mvd":
                receipt_value["observations"] = {
                    "service": "mvdsv",
                    "server_ready": True,
                    "map": "dm6",
                    "gamecode_log": "Loading vm file qwprogs.qvm...",
                    "mvd_valid": True,
                    "mvd_size": 5945,
                    "mvd_sha256": "a" * 64,
                    "termination": "controlled",
                    "process_exit_code": -15,
                }
            elif name == "qtv-stream":
                receipt_value["observations"] = {
                    "service": "qtv",
                    "http_ready": True,
                    "http_status": 200,
                    "upstream_map": "dm6",
                    "stream_readable": True,
                    "stream_header": "QTVSV 1.0\nBEGIN: native",
                    "stream_bytes": 128,
                    "termination": "controlled",
                    "process_exit_code": -15,
                }
            elif name == "qwfwd-forward":
                receipt_value["observations"] = {
                    "service": "qwfwd",
                    "udp_forwarded": True,
                    "response_returned": True,
                    "termination": "controlled",
                    "process_exit_code": -15,
                }
            elif name == "migration-0.7.13-real" or name == "lifecycle-migrate-apply":
                receipt_value["observations"] = {
                    "source_version": "0.7.13",
                    "fixture_state_sha256": "a" * 64,
                    "fixture_version_sha256": "b" * 64,
                    "state_before_sha256": "c" * 64,
                    "state_after_sha256": "d" * 64,
                    "migration_applied": True,
                    "state_converged": True,
                    "personal_preserved": True,
                    "pak_preserved": True,
                    "termination": "controlled",
                    "process_exit_code": 0,
                }
            elif name in {"lifecycle-update-apply", "lifecycle-upgrade-apply"}:
                receipt_value["observations"] = {
                    "state_before_sha256": "a" * 64,
                    "state_after_sha256": "b" * 64,
                    "state_converged": True,
                    "no_downgrade": True,
                    "profile_preserved": True,
                    "mutation_applied": True,
                    "personal_preserved": True,
                    "pak_preserved": True,
                    "termination": "controlled",
                    "process_exit_code": 0,
                }
            elif name == "game-ktx-frogbot":
                receipt_value["observations"] = {
                    "window_title": "x86QW dm6",
                    "map": "dm6",
                    "gamecode_log": "Loading vm file qwprogs.qvm...",
                    "content": {
                        "gamedir": "qw", "map": "dm6", "map_source": "qw/ktx.pk3",
                        "gamecode_package": "qw/ktx.pk3",
                    },
                    "frogbot_spawned": True,
                    "frogbot_skill": True,
                    "frogbot_named": True,
                    "frogbot_config_loaded": True,
                    "frogbot_log": "cmd botcmd skill 5; cmd botcmd addbot 5; k_fb_name_0 x86QW",
                    "termination": "controlled",
                    "process_exit_code": 0,
                }
            elif name == "lifecycle-repair-corruption":
                receipt_value["observations"] = {
                    "path": "mvdsv",
                    "repair_applied": True,
                    "corruption_restored": True,
                    "personal_preserved": True,
                    "pak_preserved": True,
                    "termination": "controlled",
                    "process_exit_code": 0,
                }
            elif name == "lifecycle-uninstall":
                receipt_value["observations"] = {
                    "installation_removed": True,
                    "personal_preserved": True,
                    "pak_preserved": True,
                    "termination": "controlled",
                    "process_exit_code": 0,
                }
            elif name == "lifecycle-purge":
                receipt_value["observations"] = {
                    "installation_removed": True,
                    "personal_removed": True,
                    "termination": "controlled",
                    "process_exit_code": 0,
                }
            receipt.write_text(
                json.dumps(receipt_value, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            cases.append({
                "name": name,
                "status": status if index == 1 else "passed",
                "exit_code": 0,
                "duration_ms": index,
                "candidate_artifact": "artifact.zip",
                "candidate_artifact_size": len(b"immutable candidate bytes"),
                "candidate_artifact_sha256": hashlib.sha256(
                    b"immutable candidate bytes"
                ).hexdigest(),
                "entrypoint": {
                    "artifact": "entrypoint.py",
                    "size": (root / "candidate" / "entrypoint.py").stat().st_size,
                    "sha256": hashlib.sha256(
                        (root / "candidate" / "entrypoint.py").read_bytes()
                    ).hexdigest(),
                },
                "runtime": runtime_identity,
                "receipt": f"receipts/{index:02d}-{name}.json",
                "receipt_sha256": hashlib.sha256(receipt.read_bytes()).hexdigest(),
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
                {
                    "system": "Darwin",
                    "machine": "arm64",
                    "chip": "Apple M3 Pro",
                    "model": "Mac15,6",
                }
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

    def test_service_receipts_require_real_service_observations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, identity = self._candidate(root)
            handoff = self._handoff(root, identity)
            mvdsv_index = CANONICAL_CASES.index("mvdsv-mvd") + 1
            receipt = handoff.parent / f"receipts/{mvdsv_index:02d}-mvdsv-mvd.json"
            value = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(
                "mvdsv",
                validate_case_receipt(
                    receipt,
                    candidate=candidate,
                    expected_case="mvdsv-mvd",
                )["observations"]["service"],
            )
            del value["observations"]
            receipt.write_text(json.dumps(value) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(NativeHandoffError, "observações"):
                validate_case_receipt(
                    receipt,
                    candidate=candidate,
                    expected_case="mvdsv-mvd",
                    require_native_observations=True,
                )

    def test_install_receipt_validates_the_installed_launcher_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, identity = self._candidate(root)
            handoff = self._handoff(root, identity)
            receipt = handoff.parent / "receipts/01-install-clean-space-unicode.json"
            value = json.loads(receipt.read_text(encoding="utf-8"))
            value["observations"] = {
                "launcher": "x86qw.sh",
                "commands": [
                    {"name": "help", "exit_code": 0},
                    {"name": "version", "exit_code": 0},
                    {"name": "changes", "exit_code": 0},
                    {"name": "migrate", "exit_code": 0},
                ],
                "help_lists_changes": True,
                "help_lists_migrate": True,
                "version_matches": True,
                "changes_executed": True,
                "migrate_dry_run_executed": True,
                "termination": "controlled",
                "process_exit_code": 0,
            }
            receipt.write_text(json.dumps(value) + "\n", encoding="utf-8")

            validated = validate_case_receipt(
                receipt,
                candidate=candidate,
                expected_case="install-clean-space-unicode",
                require_native_observations=True,
            )
            self.assertTrue(validated["observations"]["version_matches"])

    def test_pending_aggregate_preserves_the_installed_launcher_observation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, identity = self._candidate(root)
            handoff = self._handoff(root, identity)
            receipt = handoff.parent / "receipts/01-install-clean-space-unicode.json"
            value = json.loads(receipt.read_text(encoding="utf-8"))
            value["observations"] = {
                "launcher": "x86qw.sh",
                "commands": [
                    {"name": "help", "exit_code": 0},
                    {"name": "version", "exit_code": 0},
                    {"name": "changes", "exit_code": 0},
                    {"name": "migrate", "exit_code": 0},
                ],
                "help_lists_changes": True,
                "help_lists_migrate": True,
                "version_matches": True,
                "changes_executed": True,
                "migrate_dry_run_executed": True,
                "termination": "controlled",
                "process_exit_code": 0,
            }
            receipt.write_text(json.dumps(value) + "\n", encoding="utf-8")
            handoff_value = json.loads(handoff.read_text(encoding="utf-8"))
            handoff_value["cases"][0]["receipt_sha256"] = hashlib.sha256(
                receipt.read_bytes()
            ).hexdigest()
            handoff.write_text(json.dumps(handoff_value) + "\n", encoding="utf-8")
            output = root / "pending.json"

            aggregate_pending_evidence(
                candidate=candidate,
                handoff=handoff,
                expected_candidate_sha256=identity["manifest_sha256"],
                output=output,
            )

            first_case = json.loads(output.read_text(encoding="utf-8"))["platforms"][
                "macOS-ARM64"
            ]["cases"][0]
            self.assertEqual(
                "x86qw.sh",
                first_case["receipt"]["observations"]["launcher"],
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
