from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "maintenance/tools"))

from check_component_updates import remote_fingerprint  # noqa: E402


class ComponentUpdateDownloadTests(unittest.TestCase):
    def test_remote_fingerprint_requires_the_reviewed_identity_before_download(self) -> None:
        payload = b"upstream artifact"
        digest = hashlib.sha256(payload).hexdigest()

        def transfer(contract):
            self.assertEqual(len(payload), contract.expected_size)
            self.assertEqual(digest, contract.expected_sha256)
            self.assertGreaterEqual(contract.maximum_size, contract.expected_size)
            contract.destination.write_bytes(payload)
            return mock.Mock(size=len(payload), sha256=digest)

        with mock.patch(
            "check_component_updates.download", side_effect=transfer,
        ) as download:
            self.assertEqual(
                (len(payload), digest),
                remote_fingerprint(
                    "https://example.invalid/upstream.zip", len(payload), digest,
                ),
            )

        download.assert_called_once()


if __name__ == "__main__":
    unittest.main()
