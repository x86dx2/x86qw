from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from maintenance.tests.trust_support import build_repository, new_keyset, signed_root
from maintenance.tools.publish_tuf_metadata import TufPublicationError, stage_tuf_metadata


class PublishTufMetadataTests(unittest.TestCase):
    def _materialize(self, root: Path, *, include_root: bool = True) -> tuple[Path, Path, bytes]:
        catalog = b'{"format":1,"project":"x86qw","packages":[]}'
        keys = new_keyset()
        root_v2 = signed_root(keys, version=2, previous_root=keys["root"])
        repository = build_repository(
            keys, version=1, catalog=catalog, root_updates=(root_v2,),
        )
        source = root / "signed"
        metadata = source / "metadata"
        targets = source / "targets"
        for url, payload in repository.files.items():
            parsed = urlsplit(url)
            destination_root = targets if parsed.netloc == "targets.invalid" else metadata
            destination = destination_root / Path(parsed.path).name
            if destination_root is targets:
                destination = targets / Path(parsed.path.lstrip("/"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
        if include_root:
            (metadata / "1.root.json").write_bytes(repository.bootstrap_root)
            (metadata / "2.root.json").write_bytes(root_v2)
        root_file = root / "root.json"
        root_file.write_bytes(repository.bootstrap_root)
        catalog_file = root / "catalog.json"
        catalog_file.write_bytes(catalog)
        return source, catalog_file, root_file

    def test_valid_signed_metadata_is_staged_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, catalog, root = self._materialize(Path(temporary))
            stage = Path(temporary) / "stage"
            result = stage_tuf_metadata(
                metadata_dir=source, catalog=catalog, root=root, stage_dir=stage,
            )
            self.assertEqual("verified-staged", result["status"])
            self.assertTrue((stage / "metadata/1.root.json").is_file())
            self.assertTrue((stage / "metadata/timestamp.json").is_file())
            self.assertTrue((stage / "targets").is_dir())
            with self.assertRaises(TufPublicationError):
                stage_tuf_metadata(
                    metadata_dir=source, catalog=catalog, root=root, stage_dir=stage,
                )

    def test_missing_root_metadata_fails_closed_before_staging(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, catalog, root = self._materialize(Path(temporary), include_root=False)
            stage = Path(temporary) / "stage"
            with self.assertRaises(TufPublicationError):
                stage_tuf_metadata(
                    metadata_dir=source, catalog=catalog, root=root, stage_dir=stage,
                )
            self.assertFalse(stage.exists())

    def test_catalog_mismatch_fails_closed_before_staging(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, catalog, root = self._materialize(Path(temporary))
            catalog.write_text(
                json.dumps({"format": 1, "project": "attacker", "packages": []}),
                encoding="utf-8",
            )
            stage = Path(temporary) / "stage"
            with self.assertRaises(TufPublicationError):
                stage_tuf_metadata(
                    metadata_dir=source, catalog=catalog, root=root, stage_dir=stage,
                )
            self.assertFalse(stage.exists())


if __name__ == "__main__":
    unittest.main()
