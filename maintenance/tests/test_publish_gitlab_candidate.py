from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from maintenance.tools import publish_gitlab_candidate


class PublishGitLabCandidateTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, dict[str, object], dict[str, object]]:
        payload = b"candidate installer"
        version = "1.0.0"
        filename = f"x86qw-installer-{version}.zip"
        path = root / "installer" / filename
        path.parent.mkdir()
        path.write_bytes(payload)
        record = {
            "component": "installer",
            "package": "x86qw-installer",
            "version": version,
            "filename": filename,
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "urls": [
                "https://gitlab.com/api/v4/projects/84813414/packages/generic/"
                f"x86qw-installer/{version}/{filename}",
            ],
        }
        manifest = {"version": version}
        return path, record, manifest

    def test_dry_run_never_queries_or_uploads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, record, manifest = self._fixture(root)
            with (
                mock.patch.object(publish_gitlab_candidate, "verify_candidate", return_value=manifest),
                mock.patch.object(publish_gitlab_candidate, "_catalog_record", return_value=record),
                mock.patch.object(publish_gitlab_candidate, "remote_sha256") as remote,
                mock.patch.object(publish_gitlab_candidate, "upload") as upload,
            ):
                result = publish_gitlab_candidate.publish_candidate(candidate=root)
            self.assertEqual("planned", result["status"])
            remote.assert_not_called()
            upload.assert_not_called()

    def test_existing_exact_mirror_is_reused_without_upload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, record, manifest = self._fixture(root)
            identity = (record["size"], record["sha256"])
            with (
                mock.patch.object(publish_gitlab_candidate, "verify_candidate", return_value=manifest),
                mock.patch.object(publish_gitlab_candidate, "_catalog_record", return_value=record),
                mock.patch.object(publish_gitlab_candidate, "remote_sha256", return_value=identity),
                mock.patch.object(publish_gitlab_candidate, "upload") as upload,
            ):
                result = publish_gitlab_candidate.publish_candidate(candidate=root, publish=True)
            self.assertEqual("published", result["status"])
            upload.assert_not_called()

    def test_divergent_mirror_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, record, manifest = self._fixture(root)
            with (
                mock.patch.object(publish_gitlab_candidate, "verify_candidate", return_value=manifest),
                mock.patch.object(publish_gitlab_candidate, "_catalog_record", return_value=record),
                mock.patch.object(publish_gitlab_candidate, "remote_sha256", return_value=(999, "f" * 64)),
                mock.patch.object(publish_gitlab_candidate, "upload") as upload,
            ):
                with self.assertRaises(publish_gitlab_candidate.GitLabPublisherError):
                    publish_gitlab_candidate.publish_candidate(candidate=root, publish=True)
            upload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
