from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from maintenance.tools import attach_release_evidence, release_candidate


class AttachReleaseEvidenceTests(unittest.TestCase):
    def _candidate(self, root: Path) -> Path:
        source = root / "input"
        source.mkdir()
        (source / "artifact.zip").write_bytes(b"candidate")
        candidate = root / "candidate"
        release_candidate.prepare_candidate(
            source=source,
            output=candidate,
            version="1.0.0-rc.1",
            commit="a" * 40,
            generated_at="2026-08-11T00:00:00Z",
        )
        return candidate

    def test_attaches_one_regular_evidence_file_without_rebuilding_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            evidence_dir = root / "evidence-artifact"
            evidence_dir.mkdir()
            evidence = evidence_dir / "release-evidence.json"
            evidence.write_text('{"signed":true}\n', encoding="utf-8")
            output = root / "attached"
            with mock.patch.object(attach_release_evidence, "validate_signed_evidence_coverage") as validate:
                attached = attach_release_evidence.attach(
                    candidate=candidate,
                    evidence=evidence_dir,
                    output=output,
                )
            validate.assert_called_once()
            self.assertEqual("1.0.0-rc.1", attached["version"])
            self.assertEqual(
                (candidate / "candidate.json").read_bytes(),
                (output / "candidate.json").read_bytes(),
            )
            self.assertEqual(evidence.read_bytes(), (output / "release-evidence.json").read_bytes())

    def test_rejects_multiple_evidence_files_and_existing_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            evidence_dir = root / "evidence-artifact"
            evidence_dir.mkdir()
            (evidence_dir / "one").write_text("x", encoding="utf-8")
            (evidence_dir / "two").write_text("y", encoding="utf-8")
            with self.assertRaisesRegex(attach_release_evidence.EvidenceAttachmentError, "exatamente um"):
                attach_release_evidence.attach(
                    candidate=candidate,
                    evidence=evidence_dir,
                    output=root / "attached",
                )

            evidence = evidence_dir / "release-evidence.json"
            evidence.write_text("{}\n", encoding="utf-8")
            output = root / "attached"
            output.mkdir()
            with self.assertRaisesRegex(attach_release_evidence.EvidenceAttachmentError, "já existe"):
                attach_release_evidence.attach(
                    candidate=candidate,
                    evidence=evidence,
                    output=output,
                )


if __name__ == "__main__":
    unittest.main()
