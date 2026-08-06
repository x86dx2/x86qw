from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from maintenance.tools.release_candidate import CandidateError, prepare_candidate
from maintenance.tools.verify_release_metadata import (
    ReleaseMetadataError,
    verify_candidate_metadata,
)
from x86qw_runtime.trust import MAX_METADATA_BYTES


ROOT = Path(__file__).resolve().parents[2]
TRUST = ROOT / "maintenance/inventory/trust"


class ReleaseMetadataGateTests(unittest.TestCase):
    def _candidate(self, root: Path, version: str = "0.7.3") -> Path:
        source = root / "source"
        (source / "installer").mkdir(parents=True)
        shutil.copy2(
            ROOT / "dist/installer/packages/0.7.3/x86qw-installer-0.7.3.zip",
            source / "installer/x86qw-installer-0.7.3.zip",
        )
        candidate = root / "candidate"
        prepare_candidate(
            source=source,
            output=candidate,
            version=version,
            commit="a" * 40,
            generated_at="2026-08-04T00:00:00Z",
        )
        return candidate

    def test_signed_metadata_must_authenticate_exact_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = verify_candidate_metadata(
                candidate=self._candidate(root),
                root=TRUST / "root.json",
                current=TRUST / "current.json",
                snapshot=TRUST / "snapshot.json",
                catalog=ROOT / "site/public/api/v1/catalog.json",
                expected_release="0.7.3",
            )
            self.assertEqual("verified", result["status"])
            self.assertEqual("0.7.3", result["release"])

    def test_release_mismatch_fails_before_any_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            candidate = self._candidate(Path(temporary), version="1.0.0")
            with self.assertRaises(ReleaseMetadataError):
                verify_candidate_metadata(
                    candidate=candidate,
                    root=TRUST / "root.json",
                    current=TRUST / "current.json",
                    snapshot=TRUST / "snapshot.json",
                    catalog=ROOT / "site/public/api/v1/catalog.json",
                    expected_release="1.0.0",
                )

    def test_candidate_installer_digest_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            manifest_path = candidate / "candidate.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["artifacts"]["installer/x86qw-installer-0.7.3.zip"]["sha256"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(CandidateError):
                verify_candidate_metadata(
                    candidate=candidate,
                    root=TRUST / "root.json",
                    current=TRUST / "current.json",
                    snapshot=TRUST / "snapshot.json",
                    catalog=ROOT / "site/public/api/v1/catalog.json",
                )

    def test_oversized_metadata_is_rejected_before_json_processing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            path = root / "oversized.json"
            path.write_bytes(b"{" + b"x" * MAX_METADATA_BYTES + b"}")
            with self.assertRaises(ReleaseMetadataError):
                verify_candidate_metadata(
                    candidate=candidate,
                    root=path,
                    current=path,
                    snapshot=path,
                    catalog=path,
                )


if __name__ == "__main__":
    unittest.main()
