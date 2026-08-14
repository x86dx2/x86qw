from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from maintenance.tools.verify_tuf_timestamp_publication import (
    TimestampPublicationVerificationError,
    verify_timestamp_publication_receipt,
)


class VerifyTufTimestampPublicationTests(unittest.TestCase):
    def _fixture(self, workspace: Path) -> tuple[Path, dict[str, object], dict[str, Path]]:
        candidate = workspace / "candidate"
        candidate.mkdir()
        candidate_json = b'{"candidate":true}\n'
        catalog = b'{"format":1,"project":"x86qw","packages":[]}\n'
        (candidate / "candidate.json").write_bytes(candidate_json)
        (candidate / "catalog.json").write_bytes(catalog)

        key_id = "a" * 64
        renewal_report = {
            "format": 1,
            "project": "x86qw",
            "status": "timestamp-renewed",
            "mode": "timestamp-only",
            "key_id": key_id,
            "key_scope": "timestamp-only",
            "changed_files": ["metadata/timestamp.json"],
            "published": False,
        }
        verified = {
            "format": 1,
            "project": "x86qw",
            "status": "verified-timestamp-renewal",
            "changed_files": ["metadata/timestamp.json"],
            "published": False,
        }
        public = {
            "tuf": {"format": 1, "project": "x86qw", "status": "verified-public-tuf"},
            "bootstraps": {"format": 1, "project": "x86qw", "status": "verified-public-bootstraps"},
            "product": {"format": 1, "project": "x86qw", "status": "verified-public-product"},
        }
        files = {
            "renewal": workspace / "renewal.json",
            "verified": workspace / "verified.json",
            "tuf": workspace / "tuf.json",
            "bootstraps": workspace / "bootstraps.json",
            "product": workspace / "product.json",
        }
        files["renewal"].write_text(json.dumps(renewal_report) + "\n", encoding="utf-8")
        files["verified"].write_text(json.dumps(verified) + "\n", encoding="utf-8")
        files["tuf"].write_text(json.dumps(public["tuf"]) + "\n", encoding="utf-8")
        files["bootstraps"].write_text(json.dumps(public["bootstraps"]) + "\n", encoding="utf-8")
        files["product"].write_text(json.dumps(public["product"]) + "\n", encoding="utf-8")

        commit = "b" * 40
        candidate_commit = "c" * 40
        receipt = {
            "format": 1,
            "project": "x86qw",
            "status": "timestamp-published",
            "published": True,
            "changed_files": ["metadata/timestamp.json"],
            "checked_at": "2026-08-14T13:00:00Z",
            "release_code_commit": commit,
            "candidate": {
                "commit": candidate_commit,
                "run_id": "100",
                "artifact_id": "200",
                "artifact_name": f"candidate-{candidate_commit}-100-1",
                "candidate_json_sha256": hashlib.sha256(candidate_json).hexdigest(),
                "catalog_sha256": hashlib.sha256(catalog).hexdigest(),
            },
            "source_tuf": {
                "workflow_commit": commit,
                "run_id": "300",
                "artifact_id": "400",
                "artifact_name": f"tuf-metadata-{candidate_commit}-300-1",
            },
            "renewal": {
                "workflow_commit": commit,
                "run_id": "500",
                "artifact_id": "600",
                "artifact_name": f"tuf-timestamp-renewal-{candidate_commit}-500-1",
                "report_sha256": hashlib.sha256(files["renewal"].read_bytes()).hexdigest(),
                "key_id": key_id,
                "report": renewal_report,
                "verified": verified,
            },
            "publication": {
                "workflow": ".github/workflows/tuf-timestamp-publish.yml",
                "run_id": "700",
                "run_attempt": "1",
            },
            "public_verification": public,
        }
        return candidate, receipt, files

    def test_valid_receipt_binds_all_candidate_and_public_verification_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            candidate, receipt, files = self._fixture(Path(temporary))
            result = verify_timestamp_publication_receipt(
                receipt=receipt,
                candidate=candidate,
                renewal_report=files["renewal"],
                verified_renewal=files["verified"],
                public_tuf=files["tuf"],
                public_bootstraps=files["bootstraps"],
                public_product=files["product"],
            )
            self.assertEqual("verified-timestamp-publication", result["status"])
            self.assertTrue(result["published"])

    def test_candidate_digest_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            candidate, receipt, files = self._fixture(Path(temporary))
            receipt["candidate"]["catalog_sha256"] = "d" * 64  # type: ignore[index]
            with self.assertRaisesRegex(TimestampPublicationVerificationError, "catálogo"):
                verify_timestamp_publication_receipt(
                    receipt=receipt,
                    candidate=candidate,
                    renewal_report=files["renewal"],
                    verified_renewal=files["verified"],
                    public_tuf=files["tuf"],
                    public_bootstraps=files["bootstraps"],
                    public_product=files["product"],
                )

    def test_public_verification_failure_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            candidate, receipt, files = self._fixture(Path(temporary))
            files["product"].write_text(
                '{"format":1,"project":"x86qw","status":"failed"}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(TimestampPublicationVerificationError, "product"):
                verify_timestamp_publication_receipt(
                    receipt=receipt,
                    candidate=candidate,
                    renewal_report=files["renewal"],
                    verified_renewal=files["verified"],
                    public_tuf=files["tuf"],
                    public_bootstraps=files["bootstraps"],
                    public_product=files["product"],
                )


if __name__ == "__main__":
    unittest.main()
