from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def load_candidate_module():
    path = ROOT / "maintenance/tools/release_candidate.py"
    if not path.is_file():
        raise AssertionError("release_candidate.py ainda não existe")
    spec = importlib.util.spec_from_file_location("release_candidate_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReleaseCandidateTests(unittest.TestCase):
    def prepare(self, root: Path):
        module = load_candidate_module()
        source = root / "input"
        source.mkdir(parents=True)
        (source / "artifact.zip").write_bytes(b"immutable candidate bytes")
        candidate = root / "candidate"
        manifest = module.prepare_candidate(
            source=source,
            output=candidate,
            version="1.0.0",
            commit="a" * 40,
            generated_at="2026-08-06T00:00:00Z",
        )
        return module, candidate, manifest

    def test_prepare_and_verify_bind_exact_bytes_without_m3(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            module, candidate, manifest = self.prepare(root)

            self.assertEqual(
                hashlib.sha256(b"immutable candidate bytes").hexdigest(),
                manifest["artifacts"]["artifact.zip"]["sha256"],
            )
            self.assertFalse((candidate / "release-evidence.json").exists())
            ownership = json.loads((candidate / "ownership.json").read_text())
            entry = ownership["artifacts"][0]
            self.assertEqual("unclassified", entry["ownership"])
            self.assertEqual("NOASSERTION", entry["license_concluded"])
            self.assertEqual("candidate-input:artifact.zip", entry["source"])
            provenance = json.loads((candidate / "provenance.json").read_text())
            self.assertEqual(
                "https://github.com/x86dx2/x86qw/tree/" + "a" * 40,
                provenance["subject"]["source"],
            )
            self.assertEqual(manifest, module.verify_candidate(candidate))

    def test_verify_rejects_payload_or_bound_metadata_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            module, candidate, _manifest = self.prepare(root)
            (candidate / "artifact.zip").write_bytes(b"changed")
            with self.assertRaisesRegex(module.CandidateError, "artefato"):
                module.verify_candidate(candidate)

            module, candidate, _manifest = self.prepare(root / "second")
            ownership = candidate / "ownership.json"
            ownership.write_bytes(ownership.read_bytes() + b" ")
            with self.assertRaisesRegex(module.CandidateError, "metadata"):
                module.verify_candidate(candidate)

    def test_rehearsal_is_evidence_free_exact_copy_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            module, candidate, _manifest = self.prepare(root)
            rehearsal = root / "rehearsal"

            module.rehearse_candidate(candidate, rehearsal)
            source_files = {
                path.relative_to(candidate): path.read_bytes()
                for path in candidate.rglob("*") if path.is_file()
            }
            copied_files = {
                path.relative_to(rehearsal): path.read_bytes()
                for path in rehearsal.rglob("*") if path.is_file()
            }
            self.assertEqual(source_files, copied_files)
            with self.assertRaisesRegex(module.CandidateError, "já existe"):
                module.rehearse_candidate(candidate, rehearsal)

    def test_1_0_promotion_fails_closed_without_real_m3_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            module, candidate, _manifest = self.prepare(root)
            destination = root / "promoted"

            with self.assertRaisesRegex(module.CandidateError, "M3"):
                module.promote_candidate(candidate, destination, trust_root=root / "root.json")
            self.assertFalse(destination.exists())

            (candidate / "release-evidence.json").write_text("{}\n", encoding="utf-8")
            (root / "root.json").write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(module.CandidateError, "M3"):
                module.promote_candidate(candidate, destination, trust_root=root / "root.json")
            self.assertFalse(destination.exists())

    def test_prepare_and_rehearsal_never_import_m3_modules(self):
        module = load_candidate_module()
        self.assertIsNone(module.M3_IMPORT_ERROR)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _module, candidate, _manifest = self.prepare(root)
            module.rehearse_candidate(candidate, root / "rehearsal")


if __name__ == "__main__":
    unittest.main()
