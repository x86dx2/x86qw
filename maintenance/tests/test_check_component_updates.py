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
    def test_remote_fingerprint_discovers_exact_size_before_download(self) -> None:
        payload = b"upstream artifact"
        digest = hashlib.sha256(payload).hexdigest()

        def transfer(contract):
            self.assertEqual(len(payload), contract.expected_size)
            self.assertGreaterEqual(contract.maximum_size, contract.expected_size)
            contract.destination.write_bytes(payload)
            return mock.Mock(size=len(payload), sha256=digest)

        with mock.patch(
            "check_component_updates.remote_content_length", return_value=len(payload),
        ) as content_length, mock.patch(
            "check_component_updates.download", side_effect=transfer,
        ) as download:
            self.assertEqual(
                (len(payload), digest),
                remote_fingerprint("https://example.invalid/upstream.zip"),
            )

        content_length.assert_called_once_with("https://example.invalid/upstream.zip")
        download.assert_called_once()


if __name__ == "__main__":
    unittest.main()
