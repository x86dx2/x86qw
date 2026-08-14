from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from maintenance.tests.test_release_candidate import ReleaseCandidateTests
from maintenance.tools import release_candidate, release_receipt


ROOT = Path(__file__).resolve().parents[2]


class ReleaseReceiptTests(unittest.TestCase):
    def _candidate(self, root: Path) -> Path:
        source = root / "source"
        source.mkdir()
        (source / "x86qw-installer-1.0.0-rc.2.zip").write_bytes(b"installer")
        candidate = root / "candidate"
        release_candidate.prepare_candidate(
            source=source,
            output=candidate,
            version="1.0.0-rc.2",
            commit="a" * 40,
            generated_at="2026-08-13T00:00:00Z",
        )
        ReleaseCandidateTests()._write_complete_evidence(candidate)
        evidence_path = candidate / "release-evidence.json"
        evidence = json.loads(evidence_path.read_text())
        evidence.pop("signature")
        evidence["signatures"] = [
            {"keyid": "a" * 64, "sig": "c2lnbmF0dXJl"},
            {"keyid": "b" * 64, "sig": "c2lnbmF0dXJl"},
        ]
        evidence_path.write_text(json.dumps(evidence, sort_keys=True) + "\n")
        return candidate

    def _coordinates(self) -> dict[str, object]:
        return {
            "promotion": {
                "workflow": "Immutable candidate release",
                "run_id": "31752738048",
                "run_attempt": "1",
                "candidate_run_id": "31752738000",
                "candidate_artifact_id": "9001",
                "candidate_artifact_name": "candidate-" + "a" * 40 + "-1-1",
                "candidate_artifact_digest": "sha256:" + "b" * 64,
                "native_evidence_run_id": "31752738001",
                "native_evidence_artifact_id": "9002",
                "native_evidence_artifact_name": "native-m3-signed-" + "a" * 40 + "-1-1",
            },
            "publication": {
                "repository": "x86dx2/x86qw",
                "tag": "x86qw-installer-1.0.0-rc.2",
                "github_release": "https://github.com/x86dx2/x86qw/releases/tag/x86qw-installer-1.0.0-rc.2",
                "gitlab_project": "x86dx2/x86qw",
                "gitlab_asset": "https://gitlab.com/x86dx2/x86qw/-/releases/1.0.0-rc.2",
            },
            "tuf": {
                "workflow": ".github/workflows/publish-tuf.yml",
                "run_id": "31752738002",
                "artifact_id": "9003",
                "artifact_name": "tuf-metadata-1",
            },
            "deployment": {
                "endpoint": "https://x86qw.x86.com.br",
                "verification": "tuf,bootstraps,product,public-install",
            },
        }

    def test_materializes_root_and_receipt_bound_to_candidate_and_m3_observations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            manifest = json.loads((candidate / "candidate.json").read_text())
            with mock.patch.object(release_receipt, "verify_candidate", return_value=manifest):
                receipt = release_receipt.write_durable_assets(
                    candidate=candidate,
                    evidence_root=ROOT / "maintenance/trust/m3-root.json",
                    coordinates=self._coordinates(),
                )

            self.assertEqual("x86qw-release-receipt-v1", receipt["format"])
            self.assertEqual("1.0.0-rc.2", receipt["candidate"]["version"])
            self.assertEqual(
                hashlib.sha256((ROOT / "maintenance/trust/m3-root.json").read_bytes()).hexdigest(),
                receipt["evidence"]["root"]["sha256"],
            )
            self.assertEqual(
                ["install-clean-space-unicode", "install-existing-space-unicode"],
                receipt["evidence"]["platforms"]["macOS-ARM64"]["case_names"][:2],
            )
            self.assertTrue((candidate / "evidence-root.json").is_file())
            self.assertTrue((candidate / "release-receipt.json").is_file())
            self.assertEqual(
                (ROOT / "maintenance/trust/m3-root.json").read_bytes(),
                (candidate / "evidence-root.json").read_bytes(),
            )
            with mock.patch.object(release_receipt, "verify_candidate", return_value=manifest):
                self.assertEqual(receipt, release_receipt.validate_durable_assets(candidate))

    def test_final_receipt_binds_public_acceptance_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            coordinates = self._coordinates()
            coordinates["public_acceptance"] = {
                "commit": "c" * 40,
                "run_id": "31752738003",
                "artifact_id": "9004",
                "artifact_name": "public-acceptance-1.0.0-rc.2-31752738003-1",
                "version": "1.0.0-rc.2",
            }
            manifest = json.loads((candidate / "candidate.json").read_text())
            with mock.patch.object(release_receipt, "verify_candidate", return_value=manifest):
                receipt = release_receipt.write_durable_assets(
                    candidate=candidate,
                    evidence_root=ROOT / "maintenance/trust/m3-root.json",
                    coordinates=coordinates,
                )

            self.assertEqual(coordinates["public_acceptance"], receipt["public_acceptance"])
            with mock.patch.object(release_receipt, "verify_candidate", return_value=manifest):
                self.assertEqual(receipt, release_receipt.validate_durable_assets(candidate))

    def test_public_acceptance_handoff_rejects_malformed_coordinates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            coordinates = self._coordinates()
            coordinates["public_acceptance"] = {
                "commit": "not-a-commit",
                "run_id": "31752738003",
                "artifact_id": "9004",
                "artifact_name": "public-acceptance-1.0.0-rc.2-31752738003-1",
                "version": "1.0.0-rc.2",
            }
            manifest = json.loads((candidate / "candidate.json").read_text())
            with mock.patch.object(release_receipt, "verify_candidate", return_value=manifest):
                with self.assertRaises(release_receipt.ReleaseReceiptError):
                    release_receipt.write_durable_assets(
                        candidate=candidate,
                        evidence_root=ROOT / "maintenance/trust/m3-root.json",
                        coordinates=coordinates,
                    )

    def test_validation_rejects_receipt_asset_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            manifest = json.loads((candidate / "candidate.json").read_text())
            with mock.patch.object(release_receipt, "verify_candidate", return_value=manifest):
                release_receipt.write_durable_assets(
                    candidate=candidate,
                    evidence_root=ROOT / "maintenance/trust/m3-root.json",
                    coordinates=self._coordinates(),
                )
            document = json.loads((candidate / "release-receipt.json").read_text())
            document["assets"]["x86qw-installer-1.0.0-rc.2.zip"]["sha256"] = "0" * 64
            (candidate / "release-receipt.json").write_text(
                json.dumps(document, sort_keys=True) + "\n",
            )
            manifest = json.loads((candidate / "candidate.json").read_text())
            with mock.patch.object(release_receipt, "verify_candidate", return_value=manifest):
                with self.assertRaises(release_receipt.ReleaseReceiptError):
                    release_receipt.validate_durable_assets(candidate)

    def test_receipt_excludes_candidate_owned_native_smoke_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            internal = candidate / "runtime/native-smoke/macos-arm64/fixtures/0.7.13.zip"
            internal.parent.mkdir(parents=True)
            internal.write_bytes(b"native fixture")
            manifest = json.loads((candidate / "candidate.json").read_text())
            manifest["artifacts"]["runtime/native-smoke/macos-arm64/fixtures/0.7.13.zip"] = {
                "size": internal.stat().st_size,
                "sha256": hashlib.sha256(internal.read_bytes()).hexdigest(),
            }
            with mock.patch.object(release_receipt, "verify_candidate", return_value=manifest):
                receipt = release_receipt.write_durable_assets(
                    candidate=candidate,
                    evidence_root=ROOT / "maintenance/trust/m3-root.json",
                    coordinates=self._coordinates(),
                )
            self.assertNotIn("0.7.13.zip", receipt["assets"])

    def test_validation_rejects_a_candidate_root_that_differs_from_external_trust_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            trusted = root / "trusted-root.json"
            trusted.write_bytes((ROOT / "maintenance/trust/m3-root.json").read_bytes())
            alternate = root / "alternate-root.json"
            alternate.write_bytes(b"different trusted root")
            manifest = json.loads((candidate / "candidate.json").read_text())
            with mock.patch.object(release_receipt, "verify_candidate", return_value=manifest):
                release_receipt.write_durable_assets(
                    candidate=candidate,
                    evidence_root=trusted,
                    coordinates=self._coordinates(),
                )
            with mock.patch.object(release_receipt, "verify_candidate", return_value=manifest):
                with self.assertRaises(release_receipt.ReleaseReceiptError):
                    release_receipt.validate_durable_assets(candidate, trust_root=alternate)


if __name__ == "__main__":
    unittest.main()
