import hashlib
import unittest

try:
    from x86qw_runtime.io import remote
    from x86qw_runtime.io import downloader
except ImportError:
    remote = None
    downloader = None


class RecordingReporter:
    def __init__(self):
        self.details = []
        self.warnings = []

    def detail(self, message):
        self.details.append(message)

    def warning(self, message):
        self.warnings.append(message)

    def download_progress(self, received, total, *, done=False):
        del received, total, done


class RemoteClientTests(unittest.TestCase):
    def test_rate_limit_diagnostic_redacts_the_request_query(self):
        """Remote policy errors must remain actionable without leaking query secrets."""

        self.assertIsNotNone(remote, "the canonical remote client is missing")
        assert remote is not None and downloader is not None
        reporter = RecordingReporter()

        def fail(_contract, **_callbacks):
            raise downloader.DownloadHTTPError(
                403,
                "rate limited",
                {"X-RateLimit-Remaining": "0"},
            )

        client = remote.RemoteClient(reporter, download_one=fail)
        with self.assertRaisesRegex(
            remote.InstallerError, "limite temporário.*GitHub",
        ):
            client.get(
                "https://example.invalid/catalog.json?token=top-secret",
                maximum_size=1024,
                attempts=1,
            )

        rendered = "\n".join(reporter.details + reporter.warnings)
        self.assertNotIn("top-secret", rendered)
        self.assertIn("https://example.invalid/<redigido>", rendered)

    def test_trust_roles_are_fetched_in_dependency_order_with_a_bound(self):
        """Role composition must fetch root, current, then snapshot as bounded metadata."""

        self.assertIsNotNone(remote, "the canonical remote client is missing")
        assert remote is not None and downloader is not None
        reporter = RecordingReporter()
        payloads = {
            "https://trust.example/root.json": b"root",
            "https://trust.example/current.json": b"current",
            "https://trust.example/snapshot.json": b"snapshot",
        }
        seen = []

        def fetch(contract, **_callbacks):
            seen.append((contract.url, contract.maximum_size))
            payload = payloads[contract.url]
            return downloader.DownloadResult(
                url=contract.url,
                size=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
                attempts=1,
                headers={},
                data=payload,
            )

        client = remote.RemoteClient(reporter, download_one=fetch)
        roles = client.get_metadata_roles(
            {name: url for name, url in (
                ("root", "https://trust.example/root.json"),
                ("current", "https://trust.example/current.json"),
                ("snapshot", "https://trust.example/snapshot.json"),
            )},
            maximum_size=1024,
            attempts=1,
        )
        self.assertEqual(
            [
                ("https://trust.example/root.json", 1024),
                ("https://trust.example/current.json", 1024),
                ("https://trust.example/snapshot.json", 1024),
            ],
            seen,
        )
        self.assertEqual(
            {"root": b"root", "current": b"current", "snapshot": b"snapshot"},
            roles,
        )


if __name__ == "__main__":
    unittest.main()
