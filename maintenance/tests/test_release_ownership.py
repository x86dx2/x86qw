from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class ReleaseOwnershipTests(unittest.TestCase):
    def _module(self):
        from maintenance.tools import release_ownership

        return release_ownership

    def _entry(
        self,
        path: str,
        payload: bytes,
        *,
        ownership: str = "project",
        basis: str = "project-source",
        license_concluded: str = "MIT",
        license_url: str = "https://github.com/x86dx2/x86qw/blob/abc123/LICENSE",
        source: str = "dist/example.txt",
        members: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        return {
            "path": path,
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "kind": "archive" if members is not None else "file",
            "ownership": ownership,
            "ownership_basis": basis,
            "source": source,
            "license_concluded": license_concluded,
            "license_url": license_url,
            "copyright_text": "Copyright (c) 2026 x86dx2" if ownership == "project" else "NOASSERTION",
            "members": members or [],
        }

    def test_validate_document_accepts_explicit_project_and_upstream_members(self):
        module = self._module()
        upstream = self._entry(
            "payload/id1/pak0.pak",
            b"PACK opaque",
            ownership="upstream",
            basis="registered-game-data",
            license_concluded="NOASSERTION",
            license_url=None,
            source="dist/game-data/id1/pak0.pak",
        )
        own_member = self._entry(
            "_x86qw/component.json",
            b"metadata",
            source="generated:component-metadata",
        )
        archive_payload = b"archive bytes"
        archive = self._entry(
            "content/core.zip",
            archive_payload,
            ownership="mixed",
            basis="composed-archive",
            license_concluded="NOASSERTION",
            license_url=None,
            source="build-component-package",
            members=[upstream, own_member],
        )
        document = {"format": 1, "project": "x86qw", "artifacts": [archive]}
        validated = module.validate_document(document)
        self.assertEqual("mixed", validated["artifacts"][0]["ownership"])
        flattened = module.flatten_entries(validated)
        self.assertEqual(
            {"content/core.zip", "content/core.zip::_x86qw/component.json", "content/core.zip::payload/id1/pak0.pak"},
            set(flattened),
        )

    def test_validate_document_rejects_upstream_marked_mit(self):
        module = self._module()
        entry = self._entry(
            "content/upstream.zip",
            b"upstream",
            ownership="upstream",
            basis="upstream-release",
            license_concluded="MIT",
            license_url="https://github.com/example/release/blob/v1/LICENSE",
            source="upstream-release",
        )
        with self.assertRaises(module.OwnershipError):
            module.validate_document({"format": 1, "project": "x86qw", "artifacts": [entry]})

    def test_validate_document_rejects_mutable_project_license_url(self):
        module = self._module()
        entry = self._entry(
            "metadata.json",
            b"metadata",
            license_url="https://github.com/x86dx2/x86qw/blob/main/LICENSE",
        )
        with self.assertRaises(module.OwnershipError):
            module.validate_document({"format": 1, "project": "x86qw", "artifacts": [entry]})

    def test_merge_fragments_is_deterministic_and_rejects_conflicts(self):
        module = self._module()
        first = {
            "format": 1,
            "project": "x86qw",
            "artifacts": [self._entry("z.txt", b"z")],
        }
        second = {
            "format": 1,
            "project": "x86qw",
            "artifacts": [self._entry("a.txt", b"a")],
        }
        merged_a = module.merge_documents([first, second])
        merged_b = module.merge_documents([second, first])
        self.assertEqual(module.canonical_bytes(merged_a), module.canonical_bytes(merged_b))
        self.assertEqual(["a.txt", "z.txt"], [item["path"] for item in merged_a["artifacts"]])

        conflict = {
            "format": 1,
            "project": "x86qw",
            "artifacts": [self._entry("z.txt", b"different")],
        }
        with self.assertRaises(module.OwnershipError):
            module.merge_documents([first, conflict])

    def test_load_fragment_rejects_duplicate_json_keys(self):
        module = self._module()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fragment.json"
            path.write_text(
                '{"format":1,"project":"x86qw","project":"x86qw","artifacts":[]}',
                encoding="utf-8",
            )
            with self.assertRaises(module.OwnershipError):
                module.load_fragment(path)

    def test_archive_declared_project_is_derived_only_when_all_members_are_project(self):
        module = self._module()
        own = self._entry("own.txt", b"own")
        archive_payload = b"archive"
        archive = self._entry(
            "installer/owned.zip",
            archive_payload,
            ownership="project",
            basis="project-source",
            source="build-installer",
            members=[own],
        )
        document = module.validate_document({"format": 1, "project": "x86qw", "artifacts": [archive]})
        self.assertEqual("project", document["artifacts"][0]["ownership"])

        mixed = dict(own)
        mixed["path"] = "upstream.txt"
        mixed["ownership"] = "upstream"
        mixed["ownership_basis"] = "upstream-release"
        mixed["license_concluded"] = "NOASSERTION"
        mixed["license_url"] = None
        mixed["copyright_text"] = "NOASSERTION"
        archive["ownership"] = "project"
        archive["members"] = [own, mixed]
        with self.assertRaises(module.OwnershipError):
            module.validate_document({"format": 1, "project": "x86qw", "artifacts": [archive]})

    def test_validate_document_rejects_portable_collisions_and_excessive_depth(self):
        module = self._module()
        first = self._entry("content/Readme.txt", b"one")
        second = self._entry("content/readme.txt", b"two")
        with self.assertRaises(module.OwnershipError):
            module.validate_document({"format": 1, "project": "x86qw", "artifacts": [first, second]})
        migration_fixture = self._entry(
            "runtime/native-smoke/macos-arm64/fixtures/migrations/0.7.13/"
            "bundle/x86qw-installer-0.7.13/VERSION",
            b"fixture",
        )
        accepted = module.validate_document(
            {"format": 1, "project": "x86qw", "artifacts": [migration_fixture]}
        )
        self.assertEqual(migration_fixture["path"], accepted["artifacts"][0]["path"])
        deep = self._entry("a/b/c/d/e/f/g/h/i/j/k/l/m/n/o/p/q.txt", b"deep")
        with self.assertRaises(module.OwnershipError):
            module.validate_document({"format": 1, "project": "x86qw", "artifacts": [deep]})


if __name__ == "__main__":
    unittest.main()
