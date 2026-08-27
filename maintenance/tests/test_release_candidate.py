from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from x86qw_runtime.contracts.native_evidence import (
    CASE_ASSERTIONS,
    CANONICAL_CASES,
    NATIVE_EVIDENCE_FORMAT,
    REQUIRED_NATIVE_PLATFORMS,
)


ROOT = Path(__file__).resolve().parents[2]


class ReleaseCandidateTests(unittest.TestCase):
    def _candidate_module(self):
        from maintenance.tools import release_candidate

        return release_candidate

    def _trust_root(self) -> Path:
        return ROOT / "maintenance/tests/fixtures/trust/root.json"

    def _native_environment(self, platform: str) -> dict[str, object]:
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
            return {
                "os": "macOS",
                "architecture": "arm64",
                "standard_user": True,
                "elevated": False,
                "distro": None,
                "distro_version": None,
                "glibc_version": None,
            }
        raise AssertionError(f"fixture de plataforma inesperada: {platform}")

    def _native_hardware(self, platform: str) -> dict[str, str] | None:
        if platform == "macOS-ARM64":
            return {"chip": "Apple M3 Pro", "model": "Mac15,6"}
        return None

    def _native_cases(self) -> list[dict[str, object]]:
        payload = b"native evidence fixture\n"
        digest = hashlib.sha256(payload).hexdigest()
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
                        "size": len(payload),
                        "sha256": digest,
                    },
                ],
            }
            for name in CANONICAL_CASES
        ]

    def _write_complete_evidence(self, candidate: Path, *, signature: object | None = None) -> dict[str, object]:
        manifest = json.loads((candidate / "candidate.json").read_text(encoding="utf-8"))
        manifest_sha256 = hashlib.sha256((candidate / "candidate.json").read_bytes()).hexdigest()
        identity = {
            "version": manifest["version"],
            "commit": manifest["commit"],
            "manifest_sha256": manifest_sha256,
        }
        signature = signature if signature is not None else {
            "keyid": "a" * 64,
            "sig": "c2lnbmF0dXJl",
        }
        cases = self._native_cases()
        for case in cases:
            artifact = case["artifacts"][0]
            # Native handoff artifacts live beside the handoff/evidence file,
            # not inside the immutable candidate tree (which would make them
            # unregistered candidate payloads).
            artifact_path = candidate.parent / artifact["path"]
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_bytes(b"native evidence fixture\n")
        platforms: dict[str, dict[str, object]] = {}
        for platform in sorted(REQUIRED_NATIVE_PLATFORMS):
            report = {
                "format": NATIVE_EVIDENCE_FORMAT,
                "project": "x86qw",
                "status": "complete",
                "platform": platform,
                "recorded_at": "2026-08-04T00:00:00Z",
                "candidate": dict(identity),
                "environment": self._native_environment(platform),
                "runtime_executed": True,
                "cases": cases,
                "secrets": "redacted",
                "signature": None,
            }
            hardware = self._native_hardware(platform)
            if hardware is not None:
                report["hardware"] = hardware
            platforms[platform] = report
        evidence = {
            "format": 1,
            "project": "x86qw",
            "version": manifest["version"],
            "commit": manifest["commit"],
            "status": "complete",
            "candidate": identity,
            "platforms": platforms,
            "signature": signature,
        }
        (candidate / "release-evidence.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return evidence

    def test_prepare_requires_an_explicit_deterministic_timestamp(self):
        module = self._candidate_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "build"
            source.mkdir()
            (source / "artifact.zip").write_bytes(b"candidate")
            with self.assertRaises(module.CandidateError):
                module.prepare_candidate(
                    source=source,
                    output=root / "candidate",
                    version="1.0.0",
                    commit="a" * 40,
                )

    def test_prepare_rejects_staged_public_tuf_metadata(self):
        module = self._candidate_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "build/site/public/api/v1/trust/metadata"
            source.mkdir(parents=True)
            (source / "timestamp.json").write_text("stale metadata\n", encoding="utf-8")
            with self.assertRaises(module.CandidateError):
                module.prepare_candidate(
                    source=root / "build",
                    output=root / "candidate",
                    version="1.0.0",
                    commit="a" * 40,
                    generated_at="2026-08-04T00:00:00Z",
                )

    def test_prepare_writes_manifest_sbom_and_provenance_for_exact_inputs(self):
        module = self._candidate_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "build"
            source.mkdir()
            artifact = source / "x86qw-installer-1.0.0.zip"
            artifact.write_bytes(b"candidate bytes")
            metadata = source / "catalog.json"
            metadata.write_text('{"version":"1.0.0"}\n', encoding="utf-8")
            output = root / "candidate"

            result = module.prepare_candidate(
                source=source,
                output=output,
                version="1.0.0",
                commit="a" * 40,
                generated_at="2026-08-04T00:00:00Z",
            )

            self.assertEqual("1.0.0", result["version"])
            self.assertEqual("a" * 40, result["commit"])
            manifest = json.loads((output / "candidate.json").read_text(encoding="utf-8"))
            self.assertEqual(2, manifest["format"])
            identity_manifest = dict(manifest)
            identity_manifest["candidate_sha256"] = None
            expected_identity = hashlib.sha256(
                (json.dumps(identity_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
            ).hexdigest()
            self.assertEqual(expected_identity, manifest["candidate_sha256"])
            self.assertIn("x86qw-installer-1.0.0.zip", manifest["artifacts"])
            self.assertEqual(
                hashlib.sha256(artifact.read_bytes()).hexdigest(),
                manifest["artifacts"]["x86qw-installer-1.0.0.zip"]["sha256"],
            )
            sbom = json.loads((output / "sbom.spdx.json").read_text())
            self.assertEqual("SPDX-2.3", sbom["spdxVersion"])
            self.assertEqual(
                {"created": "2026-08-04T00:00:00Z", "creators": ["Tool: x86QW release-candidate"]},
                sbom["creationInfo"],
            )
            files = {item["fileName"]: item for item in sbom["files"]}
            self.assertEqual("NOASSERTION", files["x86qw-installer-1.0.0.zip"]["licenseConcluded"])
            self.assertEqual("NOASSERTION", files["catalog.json"]["licenseConcluded"])
            self.assertEqual("NOASSERTION", files["x86qw-installer-1.0.0.zip"]["copyrightText"])
            provenance = json.loads((output / "provenance.json").read_text(encoding="utf-8"))
            self.assertEqual("a" * 40, provenance["subject"]["commit"])
            self.assertFalse((output / "release-evidence.json").exists())

    def test_candidate_without_native_evidence_validates_and_promotes_locally(self):
        module = self._candidate_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "build"
            source.mkdir()
            (source / "artifact.zip").write_bytes(b"candidate")
            candidate = root / "candidate"

            module.prepare_candidate(
                source=source,
                output=candidate,
                version="1.0.0",
                commit="d" * 40,
                generated_at="2026-08-04T00:00:00Z",
            )

            with mock.patch.object(module, "verify_release_evidence") as verify_evidence:
                module.verify_candidate(candidate)
                module.promote_candidate(candidate, root / "promoted")
                verify_evidence.assert_not_called()
            self.assertEqual(b"candidate", (root / "promoted" / "artifact.zip").read_bytes())

    def test_rehearse_cli_copies_an_exact_candidate_without_signing(self):
        module = self._candidate_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "build"
            source.mkdir()
            (source / "artifact.zip").write_bytes(b"candidate")
            candidate = root / "candidate"
            destination = root / "rehearsed"
            module.prepare_candidate(
                source=source,
                output=candidate,
                version="1.0.0-rc.1",
                commit="e" * 40,
                generated_at="2026-08-04T00:00:00Z",
            )

            self.assertEqual(0, module.main(["rehearse", str(candidate), str(destination)]))
            module.verify_candidate(destination)
            self.assertEqual(b"candidate", (destination / "artifact.zip").read_bytes())

    def test_workflow_shaped_candidate_binds_explicit_ownership_to_sbom(self):
        module = self._candidate_module()
        from maintenance.tools import release_ownership

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "release-input"
            (source / "installer").mkdir(parents=True)
            (source / "content").mkdir(parents=True)
            installer = source / "installer" / "x86qw-installer-1.0.0.zip"
            content = source / "content" / "ktx-1.0.0.zip"
            installer.write_bytes(b"installer archive")
            content.write_bytes(b"mixed content archive")
            own_member = {
                "path": "x86qw.pyz",
                "size": 8,
                "sha256": hashlib.sha256(b"zipapp!!").hexdigest(),
                "kind": "archive",
                "ownership": "project",
                "ownership_basis": "build-output",
                "source": "build:x86qw.pyz",
                "license_concluded": "MIT",
                "license_url": "https://github.com/x86dx2/x86qw/blob/x86qw-installer-1.0.0/LICENSE",
                "copyright_text": "Copyright (c) 2026 x86dx2",
                "members": [],
            }
            installer_entry = {
                "path": "installer/x86qw-installer-1.0.0.zip",
                "size": installer.stat().st_size,
                "sha256": hashlib.sha256(installer.read_bytes()).hexdigest(),
                "kind": "archive",
                "ownership": "project",
                "ownership_basis": "build-output",
                "source": "build-installer-bundle",
                "license_concluded": "MIT",
                "license_url": "https://github.com/x86dx2/x86qw/blob/x86qw-installer-1.0.0/LICENSE",
                "copyright_text": "Copyright (c) 2026 x86dx2",
                "members": [own_member],
            }
            upstream_member = dict(own_member)
            upstream_member.update({
                "path": "payload/upstream.pk3",
                "size": 8,
                "sha256": hashlib.sha256(b"upstream").hexdigest(),
                "ownership": "upstream",
                "ownership_basis": "upstream-release",
                "source": "upstream-release",
                "license_concluded": "NOASSERTION",
                "license_url": None,
                "copyright_text": "NOASSERTION",
            })
            content_entry = {
                "path": "content/ktx-1.0.0.zip",
                "size": content.stat().st_size,
                "sha256": hashlib.sha256(content.read_bytes()).hexdigest(),
                "kind": "archive",
                "ownership": "mixed",
                "ownership_basis": "composed-archive",
                "source": "build-component-package",
                "license_concluded": "NOASSERTION",
                "license_url": None,
                "copyright_text": "NOASSERTION",
                "members": [upstream_member, dict(own_member, path="_x86qw/component.json")],
            }
            fragment = root / "ownership.json"
            fragment.write_bytes(
                release_ownership.canonical_bytes({
                    "format": 1,
                    "project": "x86qw",
                    "artifacts": [content_entry, installer_entry],
                })
            )
            output = root / "candidate"
            module.prepare_candidate(
                source=source,
                output=output,
                version="1.0.0",
                commit="a" * 40,
                generated_at="2026-08-04T00:00:00Z",
                ownership_fragments=[fragment],
            )
            ownership = json.loads((output / "ownership.json").read_text(encoding="utf-8"))
            self.assertEqual(2, len(ownership["artifacts"]))
            sbom = json.loads((output / "sbom.spdx.json").read_text(encoding="utf-8"))
            files = {item["fileName"]: item for item in sbom["files"]}
            self.assertEqual("MIT", files["installer/x86qw-installer-1.0.0.zip"]["licenseConcluded"])
            self.assertEqual("MIT", files["installer/x86qw-installer-1.0.0.zip::x86qw.pyz"]["licenseConcluded"])
            self.assertEqual("NOASSERTION", files["content/ktx-1.0.0.zip"]["licenseConcluded"])
            self.assertEqual("NOASSERTION", files["content/ktx-1.0.0.zip::payload/upstream.pk3"]["licenseConcluded"])
            module.verify_candidate(output, allow_pending_evidence=True)

            ownership["artifacts"].pop()
            (output / "ownership.json").write_bytes(
                release_ownership.canonical_bytes(ownership)
            )
            with self.assertRaises(module.CandidateError):
                module.verify_candidate(output, allow_pending_evidence=True)

    def test_spdx_contract_rejects_incomplete_or_misclassified_documents(self):
        module = self._candidate_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "build"
            source.mkdir()
            (source / "artifact.zip").write_bytes(b"upstream bundle")
            (source / "catalog.json").write_text("{}\n", encoding="utf-8")

            def prepare(name: str) -> tuple[Path, dict[str, object]]:
                candidate = root / name
                module.prepare_candidate(
                    source=source,
                    output=candidate,
                    version="1.0.0",
                    commit="a" * 40,
                    generated_at="2026-08-04T00:00:00Z",
                )
                path = candidate / "sbom.spdx.json"
                return path, json.loads(path.read_text(encoding="utf-8"))

            path, value = prepare("missing-creation")
            value.pop("creationInfo")
            path.write_text(json.dumps(value) + "\n", encoding="utf-8")
            with self.assertRaises(module.CandidateError):
                module.verify_candidate(root / "missing-creation", allow_pending_evidence=True)

            path, value = prepare("missing-license")
            value["files"][0].pop("licenseConcluded")
            path.write_text(json.dumps(value) + "\n", encoding="utf-8")
            with self.assertRaises(module.CandidateError):
                module.verify_candidate(root / "missing-license", allow_pending_evidence=True)

            path, value = prepare("duplicate-id")
            value["files"][1]["SPDXID"] = value["files"][0]["SPDXID"]
            path.write_text(json.dumps(value) + "\n", encoding="utf-8")
            with self.assertRaises(module.CandidateError):
                module.verify_candidate(root / "duplicate-id", allow_pending_evidence=True)

            path, value = prepare("upstream-mit")
            upstream = next(item for item in value["files"] if item["fileName"] == "artifact.zip")
            upstream["licenseConcluded"] = "MIT"
            upstream["copyrightText"] = "Copyright (c) 2026 x86QW contributors"
            path.write_text(json.dumps(value) + "\n", encoding="utf-8")
            with self.assertRaises(module.CandidateError):
                module.verify_candidate(root / "upstream-mit", allow_pending_evidence=True)

    def test_prepare_never_replaces_a_destination_created_after_preflight(self):
        module = self._candidate_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "build"
            source.mkdir()
            (source / "artifact.zip").write_bytes(b"candidate")
            output = root / "candidate"
            real_copytree = module.shutil.copytree

            def race_copytree(source_path, destination_path, **kwargs):
                Path(destination_path).mkdir()
                (Path(destination_path) / "artifact.zip").write_bytes(b"concurrent")
                return real_copytree(source_path, destination_path, **kwargs)

            with mock.patch.object(module.shutil, "copytree", side_effect=race_copytree):
                with self.assertRaises(module.CandidateError):
                    module.prepare_candidate(
                        source=source,
                        output=output,
                        version="1.0.0",
                        commit="a" * 40,
                        generated_at="2026-08-04T00:00:00Z",
                    )
            self.assertEqual(b"concurrent", (output / "artifact.zip").read_bytes())

    def test_prepare_rejects_casefolded_artifact_path_collisions(self):
        module = self._candidate_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "build"
            source.mkdir()
            first = source / "Artifact.zip"
            second = source / "artifact.zip"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            if len(list(source.iterdir())) < 2:
                self.skipTest("filesystem local não permite criar caminhos com case distinto")
            with self.assertRaises(module.CandidateError):
                module.prepare_candidate(
                    source=source,
                    output=root / "candidate",
                    version="1.0.0",
                    commit="a" * 40,
                    generated_at="2026-08-04T00:00:00Z",
                )

    def test_verify_rejects_tampered_candidate_and_promotion_never_overwrites(self):
        module = self._candidate_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "build"
            source.mkdir()
            (source / "artifact.zip").write_bytes(b"immutable")
            candidate = root / "candidate"
            module.prepare_candidate(
                source=source,
                output=candidate,
                version="1.0.0",
                commit="b" * 40,
                generated_at="2026-08-04T00:00:00Z",
            )
            module.verify_candidate(candidate, allow_pending_evidence=True)
            (candidate / "artifact.zip").write_bytes(b"changed")
            with self.assertRaises(module.CandidateError):
                module.verify_candidate(candidate, allow_pending_evidence=True)

            formatted = root / "formatted"
            module.prepare_candidate(
                source=source,
                output=formatted,
                version="1.0.0",
                commit="6" * 40,
                generated_at="2026-08-04T00:00:00Z",
            )
            manifest_path = formatted / "candidate.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["format"] = True
            manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
            with self.assertRaises(module.CandidateError):
                module.verify_candidate(formatted, allow_pending_evidence=True)

            duplicate = root / "duplicate"
            module.prepare_candidate(
                source=source,
                output=duplicate,
                version="1.0.0",
                commit="6" * 40,
                generated_at="2026-08-04T00:00:00Z",
            )
            (duplicate / "candidate.json").write_text(
                '{"format": 1, "format": 1}\n',
                encoding="utf-8",
            )
            with self.assertRaises(module.CandidateError):
                module.verify_candidate(duplicate, allow_pending_evidence=True)

            fresh = root / "fresh"
            module.prepare_candidate(
                source=source,
                output=fresh,
                version="1.0.0",
                commit="b" * 40,
                generated_at="2026-08-04T00:00:00Z",
            )
            self._write_complete_evidence(fresh)
            destination = root / "promoted"
            module.promote_candidate(fresh, destination)
            self.assertEqual(b"immutable", (destination / "artifact.zip").read_bytes())
            with mock.patch.object(module, "verify_release_evidence"):
                with self.assertRaises(module.CandidateError):
                    module.promote_candidate(fresh, destination, trust_root=self._trust_root())

    def test_promotion_refuses_destination_created_after_preflight(self):
        module = self._candidate_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "build"
            source.mkdir()
            (source / "artifact.zip").write_bytes(b"candidate")
            candidate = root / "candidate"
            module.prepare_candidate(
                source=source,
                output=candidate,
                version="1.0.0",
                commit="b" * 40,
                generated_at="2026-08-04T00:00:00Z",
            )
            self._write_complete_evidence(candidate)
            destination = root / "promoted"
            def race_publish(source_path, destination_path):
                Path(destination_path).mkdir()
                (Path(destination_path) / "artifact.zip").write_bytes(b"concurrent")
                raise FileExistsError(destination_path)

            with mock.patch.object(module, "verify_release_evidence"), mock.patch.object(
                module, "_atomic_publish_no_replace", side_effect=race_publish,
            ):
                with self.assertRaises(module.CandidateError):
                    module.promote_candidate(
                        candidate,
                        destination,
                        trust_root=self._trust_root(),
                    )
            self.assertEqual(b"concurrent", (destination / "artifact.zip").read_bytes())

    def test_promotion_keeps_private_staging_invisible_after_publish_failure(self):
        module = self._candidate_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "build"
            source.mkdir()
            (source / "artifact.zip").write_bytes(b"candidate")
            candidate = root / "candidate"
            module.prepare_candidate(
                source=source,
                output=candidate,
                version="1.0.0",
                commit="b" * 40,
                generated_at="2026-08-04T00:00:00Z",
            )
            self._write_complete_evidence(candidate)
            destination = root / "promoted"

            def failed_publish(source_path, destination_path):
                # The private staging tree may be inspected by the failure
                # hook, but it must never become visible as destination.
                self.assertTrue(Path(source_path).is_dir())
                raise OSError("atomic publish unavailable")

            with mock.patch.object(module, "verify_release_evidence"), mock.patch.object(
                module, "_atomic_publish_no_replace", side_effect=failed_publish,
            ):
                with self.assertRaises(module.CandidateError):
                    module.promote_candidate(
                        candidate,
                        destination,
                        trust_root=self._trust_root(),
                    )
            self.assertFalse(destination.exists())

    def test_promotion_revalidates_the_staged_snapshot_after_source_mutation(self):
        module = self._candidate_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "build"
            source.mkdir()
            (source / "artifact.zip").write_bytes(b"candidate")
            candidate = root / "candidate"
            module.prepare_candidate(
                source=source,
                output=candidate,
                version="1.0.0",
                commit="b" * 40,
                generated_at="2026-08-04T00:00:00Z",
            )
            self._write_complete_evidence(candidate)
            destination = root / "promoted"
            real_copy2 = module.shutil.copy2

            def copy2_then_mutate(source_path, destination_path, **kwargs):
                result = real_copy2(source_path, destination_path, **kwargs)
                if Path(source_path).name == "artifact.zip":
                    Path(source_path).write_bytes(b"changed after preflight")
                return result

            with mock.patch.object(module, "verify_release_evidence"), mock.patch.object(
                module.shutil, "copy2", side_effect=copy2_then_mutate,
            ):
                with self.assertRaises(module.CandidateError):
                    module.promote_candidate(
                        candidate,
                        destination,
                        trust_root=self._trust_root(),
                    )
            self.assertFalse(destination.exists())

    def test_strict_trust_root_rejects_structural_evidence_signature(self):
        module = self._candidate_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "build"
            source.mkdir()
            (source / "artifact.zip").write_bytes(b"candidate")
            candidate = root / "candidate"
            module.prepare_candidate(
                source=source,
                output=candidate,
                version="1.0.0",
                commit="c" * 40,
                generated_at="2026-08-04T00:00:00Z",
            )
            self._write_complete_evidence(candidate)
            with self.assertRaises(module.CandidateError):
                module.verify_candidate(
                    candidate,
                    trust_root=ROOT / "maintenance/tests/fixtures/trust/root.json",
                )

    def test_verify_rejects_malformed_provenance_without_traceback(self):
        module = self._candidate_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "build"
            source.mkdir()
            (source / "artifact.zip").write_bytes(b"candidate")
            candidate = root / "candidate"
            module.prepare_candidate(
                source=source,
                output=candidate,
                version="1.0.0",
                commit="d" * 40,
                generated_at="2026-08-04T00:00:00Z",
            )
            (candidate / "provenance.json").write_text("{\"subject\": null}\n", encoding="utf-8")
            with self.assertRaises(module.CandidateError):
                module.verify_candidate(candidate)

    def test_verify_rejects_checksum_drift_and_unsafe_artifact_paths(self):
        module = self._candidate_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "build"
            source.mkdir()
            (source / "artifact.zip").write_bytes(b"candidate")
            candidate = root / "candidate"
            module.prepare_candidate(
                source=source,
                output=candidate,
                version="1.0.0",
                commit="e" * 40,
                generated_at="2026-08-04T00:00:00Z",
            )
            (candidate / "checksums.txt").write_text("0" * 64 + "  artifact.zip\n", encoding="utf-8")
            with self.assertRaises(module.CandidateError):
                module.verify_candidate(candidate)

            fresh = root / "fresh"
            module.prepare_candidate(
                source=source,
                output=fresh,
                version="1.0.0",
                commit="e" * 40,
                generated_at="2026-08-04T00:00:00Z",
            )
            manifest = json.loads((fresh / "candidate.json").read_text(encoding="utf-8"))
            metadata = manifest.pop("artifacts")
            manifest["artifacts"] = {"../escape.zip": next(iter(metadata.values()))}
            (fresh / "candidate.json").write_text(
                json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8",
            )
            with self.assertRaises(module.CandidateError):
                module.verify_candidate(fresh)

    def test_optional_evidence_is_validated_but_not_required_for_local_promotion(self):
        module = self._candidate_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "build"
            source.mkdir()
            (source / "artifact.zip").write_bytes(b"candidate")
            candidate = root / "candidate"
            module.prepare_candidate(
                source=source,
                output=candidate,
                version="1.0.0",
                commit="f" * 40,
                generated_at="2026-08-04T00:00:00Z",
            )

            module.verify_candidate(candidate)
            module.promote_candidate(candidate, root / "pending-promoted")

            evidence_path = candidate / "release-evidence.json"
            evidence = {
                "format": 1,
                "project": "x86qw",
                "version": "1.0.0",
                "commit": "f" * 40,
                "status": "failed",
                "candidate": "candidate.json",
                "platforms": {},
                "signature": None,
            }
            evidence_path.write_text(json.dumps(evidence) + "\n", encoding="utf-8")
            with self.assertRaises(module.CandidateError):
                module.verify_candidate(candidate)
            with self.assertRaises(module.CandidateError):
                module.promote_candidate(
                    candidate,
                    root / "failed-promoted",
                    trust_root=self._trust_root(),
                )

            self._write_complete_evidence(candidate)
            module.verify_candidate(candidate)
            module.promote_candidate(candidate, root / "promoted")
            with mock.patch.object(module, "verify_release_evidence"):
                module.promote_candidate(
                    candidate,
                    root / "promoted-with-trust",
                    trust_root=self._trust_root(),
                )

    def test_promotion_requires_the_canonical_native_platform_set(self):
        module = self._candidate_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "build"
            source.mkdir()
            (source / "artifact.zip").write_bytes(b"candidate")

            missing = root / "missing"
            module.prepare_candidate(
                source=source,
                output=missing,
                version="1.0.0",
                commit="1" * 40,
                generated_at="2026-08-04T00:00:00Z",
            )
            evidence = self._write_complete_evidence(missing)
            evidence["platforms"].pop("macOS-ARM64")
            (missing / "release-evidence.json").write_text(
                json.dumps(evidence) + "\n", encoding="utf-8",
            )
            with self.assertRaisesRegex(module.CandidateError, "plataformas"):
                module.verify_candidate(missing)
            with self.assertRaises(module.CandidateError):
                module.promote_candidate(
                    missing,
                    root / "missing-promoted",
                    trust_root=self._trust_root(),
                )

            extra = root / "extra"
            module.prepare_candidate(
                source=source,
                output=extra,
                version="1.0.0",
                commit="2" * 40,
                generated_at="2026-08-04T00:00:00Z",
            )
            evidence = self._write_complete_evidence(extra)
            evidence["platforms"]["macOS-X64"] = evidence["platforms"]["macOS-ARM64"]
            (extra / "release-evidence.json").write_text(
                json.dumps(evidence) + "\n", encoding="utf-8",
            )
            with self.assertRaisesRegex(module.CandidateError, "plataformas"):
                module.verify_candidate(extra)

    def test_verify_requires_bound_candidate_identity_and_signature(self):
        module = self._candidate_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "build"
            source.mkdir()
            (source / "artifact.zip").write_bytes(b"candidate")

            for field, value in (
                ("version", "9.9.9"),
                ("commit", "2" * 40),
                ("manifest_sha256", "0" * 64),
            ):
                candidate = root / f"candidate-{field}"
                module.prepare_candidate(
                    source=source,
                    output=candidate,
                    version="1.0.0",
                    commit="1" * 40,
                    generated_at="2026-08-04T00:00:00Z",
                )
                evidence = self._write_complete_evidence(candidate)
                evidence["candidate"][field] = value
                evidence["platforms"]["macOS-ARM64"]["candidate"][field] = value
                (candidate / "release-evidence.json").write_text(
                    json.dumps(evidence) + "\n", encoding="utf-8",
                )
                with self.assertRaises(module.CandidateError):
                    module.verify_candidate(candidate)

            candidate = root / "candidate-signature"
            module.prepare_candidate(
                source=source,
                output=candidate,
                version="1.0.0",
                commit="2" * 40,
                generated_at="2026-08-04T00:00:00Z",
            )
            self._write_complete_evidence(candidate, signature=None)
            evidence_path = candidate / "release-evidence.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["signature"] = None
            with self.assertRaises(module.CandidateError):
                evidence_path.write_text(json.dumps(evidence) + "\n", encoding="utf-8")
                module.verify_candidate(candidate)

    def test_verify_accepts_legacy_sbom_namespace_for_pre_migration_candidates(self):
        module = self._candidate_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "build"
            source.mkdir()
            (source / "artifact.zip").write_bytes(b"candidate")
            candidate = root / "candidate"
            module.prepare_candidate(
                source=source,
                output=candidate,
                version="1.0.0",
                commit="1" * 40,
                generated_at="2026-08-04T00:00:00Z",
            )

            sbom_path = candidate / "sbom.spdx.json"
            sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
            sbom["documentNamespace"] = (
                "https://x86qw.x86.com.br/release/1.0.0/" + "1" * 40
            )
            sbom_path.write_bytes(module._json_bytes(sbom))

            manifest_path = candidate / "candidate.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["metadata"]["sbom.spdx.json"] = {
                "size": sbom_path.stat().st_size,
                "sha256": hashlib.sha256(sbom_path.read_bytes()).hexdigest(),
            }
            manifest["candidate_sha256"] = module._candidate_digest(manifest)
            manifest_path.write_bytes(module._json_bytes(manifest))

            verified = module.verify_candidate(candidate)
            self.assertEqual(verified["version"], "1.0.0")

    def test_verify_binds_sbom_and_provenance_to_manifest(self):
        module = self._candidate_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "build"
            source.mkdir()
            (source / "artifact.zip").write_bytes(b"candidate")
            candidate = root / "candidate"
            module.prepare_candidate(
                source=source,
                output=candidate,
                version="1.0.0",
                commit="3" * 40,
                generated_at="2026-08-04T00:00:00Z",
            )
            self._write_complete_evidence(candidate)

            sbom_path = candidate / "sbom.spdx.json"
            sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
            sbom["files"][0]["checksums"][0]["checksumValue"] = "0" * 64
            sbom_path.write_text(json.dumps(sbom) + "\n", encoding="utf-8")
            with self.assertRaises(module.CandidateError):
                module.verify_candidate(candidate)

            module.prepare_candidate(
                source=source,
                output=root / "fresh",
                version="1.0.0",
                commit="3" * 40,
                generated_at="2026-08-04T00:00:00Z",
            )
            fresh = root / "fresh"
            self._write_complete_evidence(fresh)
            provenance_path = fresh / "provenance.json"
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            provenance["materials"][0]["sha256"] = "0" * 64
            provenance_path.write_text(json.dumps(provenance) + "\n", encoding="utf-8")
            with self.assertRaises(module.CandidateError):
                module.verify_candidate(fresh)

    def test_manifest_binds_exact_immutable_metadata_bytes(self):
        module = self._candidate_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "build"
            source.mkdir()
            (source / "artifact.zip").write_bytes(b"candidate")

            candidate = root / "candidate"
            module.prepare_candidate(
                source=source,
                output=candidate,
                version="1.0.0",
                commit="8" * 40,
                generated_at="2026-08-04T00:00:00Z",
            )
            manifest = json.loads((candidate / "candidate.json").read_text(encoding="utf-8"))
            self.assertEqual(set(module.BOUND_METADATA_NAMES), set(manifest["metadata"]))
            for name in module.BOUND_METADATA_NAMES:
                self.assertEqual(
                    {
                        "size": (candidate / name).stat().st_size,
                        "sha256": hashlib.sha256((candidate / name).read_bytes()).hexdigest(),
                    },
                    manifest["metadata"][name],
                )

            # A semantically equivalent reserialization is still a byte drift
            # and must fail before the document is interpreted.
            sbom_path = candidate / "sbom.spdx.json"
            sbom_path.write_text(
                json.dumps(json.loads(sbom_path.read_text(encoding="utf-8")), sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(module.CandidateError):
                module.verify_candidate(candidate, allow_pending_evidence=True)

            for name in module.BOUND_METADATA_NAMES:
                fresh = root / ("fresh-" + name.replace(".", "-"))
                module.prepare_candidate(
                    source=source,
                    output=fresh,
                    version="1.0.0",
                    commit="8" * 40,
                    generated_at="2026-08-04T00:00:00Z",
                )
                path = fresh / name
                path.write_bytes(path.read_bytes() + b"x")
                with self.assertRaises(module.CandidateError):
                    module.verify_candidate(fresh, allow_pending_evidence=True)

            fresh = root / "extra-metadata"
            module.prepare_candidate(
                source=source,
                output=fresh,
                version="1.0.0",
                commit="8" * 40,
                generated_at="2026-08-04T00:00:00Z",
            )
            manifest_path = fresh / "candidate.json"
            value = json.loads(manifest_path.read_text(encoding="utf-8"))
            value["metadata"]["unexpected.json"] = {"size": 0, "sha256": "0" * 64}
            manifest_path.write_text(json.dumps(value) + "\n", encoding="utf-8")
            with self.assertRaises(module.CandidateError):
                module.verify_candidate(fresh, allow_pending_evidence=True)

            complete = root / "complete"
            module.prepare_candidate(
                source=source,
                output=complete,
                version="1.0.0",
                commit="8" * 40,
                generated_at="2026-08-04T00:00:00Z",
            )
            self._write_complete_evidence(complete)
            module.verify_candidate(complete)

    def test_pending_compatibility_requires_explicit_opt_in(self):
        module = self._candidate_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "build"
            source.mkdir()
            (source / "artifact.zip").write_bytes(b"candidate")
            candidate = root / "candidate"
            module.prepare_candidate(
                source=source,
                output=candidate,
                version="1.0.0",
                commit="4" * 40,
                generated_at="2026-08-04T00:00:00Z",
            )
            evidence = {
                "format": 1,
                "project": "x86qw",
                "version": "1.0.0",
                "commit": "4" * 40,
                "status": "pending",
                "candidate": "candidate.json",
                "platforms": {},
                "signature": None,
            }
            (candidate / "release-evidence.json").write_text(
                json.dumps(evidence) + "\n", encoding="utf-8",
            )
            with self.assertRaises(module.CandidateError):
                module.verify_candidate(candidate)
            module.verify_candidate(candidate, allow_pending_evidence=True)

    def test_manifest_identity_digest_is_required_even_before_native_evidence(self):
        module = self._candidate_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "build"
            source.mkdir()
            (source / "artifact.zip").write_bytes(b"candidate")
            candidate = root / "candidate"
            module.prepare_candidate(
                source=source,
                output=candidate,
                version="1.0.0",
                commit="5" * 40,
                generated_at="2026-08-04T00:00:00Z",
            )
            manifest_path = candidate / "candidate.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["candidate_sha256"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
            with self.assertRaises(module.CandidateError):
                module.verify_candidate(candidate, allow_pending_evidence=True)

    def test_manifest_schema_and_timestamp_are_closed(self):
        module = self._candidate_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "build"
            source.mkdir()
            (source / "artifact.zip").write_bytes(b"candidate")
            with self.assertRaises(module.CandidateError):
                module.prepare_candidate(
                    source=source,
                    output=root / "invalid-time",
                    version="1.0.0",
                    commit="6" * 40,
                    generated_at="2026-08-04T00:00:00+00:00",
                )

            candidate = root / "candidate"
            module.prepare_candidate(
                source=source,
                output=candidate,
                version="1.0.0",
                commit="6" * 40,
                generated_at="2026-08-04T00:00:00Z",
            )
            manifest_path = candidate / "candidate.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["format"] = 1
            manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
            with self.assertRaises(module.CandidateError):
                module.verify_candidate(candidate, allow_pending_evidence=True)

            candidate_extra = root / "candidate-extra"
            module.prepare_candidate(
                source=source,
                output=candidate_extra,
                version="1.0.0",
                commit="6" * 40,
                generated_at="2026-08-04T00:00:00Z",
            )
            manifest_path = candidate_extra / "candidate.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["unexpected"] = True
            manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
            with self.assertRaises(module.CandidateError):
                module.verify_candidate(candidate_extra, allow_pending_evidence=True)

            fresh = root / "fresh"
            module.prepare_candidate(
                source=source,
                output=fresh,
                version="1.0.0",
                commit="6" * 40,
                generated_at="2026-08-04T00:00:00Z",
            )
            manifest_path = fresh / "candidate.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["artifact_count"] = True
            digest_manifest = dict(manifest)
            digest_manifest["candidate_sha256"] = None
            manifest["candidate_sha256"] = hashlib.sha256(
                (json.dumps(digest_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
            ).hexdigest()
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(module.CandidateError):
                module.verify_candidate(fresh, allow_pending_evidence=True)

    def test_complete_evidence_schema_is_closed(self):
        module = self._candidate_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "build"
            source.mkdir()
            (source / "artifact.zip").write_bytes(b"candidate")
            candidate = root / "candidate"
            module.prepare_candidate(
                source=source,
                output=candidate,
                version="1.0.0",
                commit="7" * 40,
                generated_at="2026-08-04T00:00:00Z",
            )
            evidence = self._write_complete_evidence(candidate)
            evidence["unexpected"] = True
            (candidate / "release-evidence.json").write_text(
                json.dumps(evidence) + "\n", encoding="utf-8",
            )
            with self.assertRaises(module.CandidateError):
                module.verify_candidate(candidate)

    def test_complete_evidence_accepts_only_the_canonical_signature_list_shape(self):
        module = self._candidate_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "build"
            source.mkdir()
            (source / "artifact.zip").write_bytes(b"candidate")
            candidate = root / "candidate"
            module.prepare_candidate(
                source=source,
                output=candidate,
                version="1.0.0",
                commit="8" * 40,
                generated_at="2026-08-04T00:00:00Z",
            )
            evidence = self._write_complete_evidence(candidate)
            signature = evidence.pop("signature")
            evidence["signatures"] = [signature]
            (candidate / "release-evidence.json").write_text(
                json.dumps(evidence) + "\n", encoding="utf-8",
            )
            module.verify_candidate(candidate)
            evidence["signature"] = signature
            (candidate / "release-evidence.json").write_text(
                json.dumps(evidence) + "\n", encoding="utf-8",
            )
            with self.assertRaises(module.CandidateError):
                module.verify_candidate(candidate)

    def test_cli_verifies_candidate_without_rebuilding(self):
        module_path = ROOT / "maintenance/tools/release_candidate.py"
        self.assertTrue(module_path.is_file())
        result = subprocess.run(
            [sys.executable, str(module_path), "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("prepare", result.stdout)
        self.assertIn("verify", result.stdout)
        self.assertIn("promote", result.stdout)


if __name__ == "__main__":
    unittest.main()
