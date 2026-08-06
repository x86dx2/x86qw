from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from maintenance.tools import verify_release_mirrors
from x86qw_runtime.io.downloader import (
    DownloadHTTPError,
    DownloadIntegrityError,
    DownloadResult,
    PinnedArtifact,
)


class ReleaseMirrorVerificationTests(unittest.TestCase):
    URLS = (
        "https://github.example.invalid/x86qw.zip",
        "https://gitlab.example.invalid/x86qw.zip",
    )
    SIZE = 123
    DIGEST = "a" * 64

    def _result(self, contract: PinnedArtifact) -> DownloadResult:
        return DownloadResult(
            url=contract.url,
            size=self.SIZE,
            sha256=self.DIGEST,
            attempts=1,
            headers={},
            path=contract.destination,
        )

    def test_every_declared_mirror_must_be_downloaded_and_match(self) -> None:
        calls: list[PinnedArtifact] = []

        def download(contract: PinnedArtifact) -> DownloadResult:
            calls.append(contract)
            return self._result(contract)

        with mock.patch.object(verify_release_mirrors, "download", side_effect=download):
            result = verify_release_mirrors.verify_mirrors(
                self.URLS,
                expected_size=self.SIZE,
                expected_sha256=self.DIGEST,
            )

        self.assertEqual(self.URLS, tuple(item["url"] for item in result))
        self.assertEqual(self.URLS, tuple(item.url for item in calls))
        self.assertTrue(all(item.expected_size == self.SIZE for item in calls))
        self.assertTrue(all(item.expected_sha256 == self.DIGEST for item in calls))

    def test_one_404_fails_after_other_mirror_but_never_falls_back(self) -> None:
        calls: list[PinnedArtifact] = []

        def download(contract: PinnedArtifact) -> DownloadResult:
            calls.append(contract)
            if len(calls) == 2:
                raise DownloadHTTPError(404, "not found", {})
            return self._result(contract)

        with mock.patch.object(verify_release_mirrors, "download", side_effect=download):
            with self.assertRaises(verify_release_mirrors.MirrorVerificationError):
                verify_release_mirrors.verify_mirrors(
                    self.URLS,
                    expected_size=self.SIZE,
                    expected_sha256=self.DIGEST,
                )

        self.assertEqual(2, len(calls))

    def test_corrupt_mirror_is_not_hidden_by_an_earlier_success(self) -> None:
        calls: list[PinnedArtifact] = []

        def download(contract: PinnedArtifact) -> DownloadResult:
            calls.append(contract)
            if len(calls) == 2:
                raise DownloadIntegrityError("digest mismatch")
            return self._result(contract)

        with mock.patch.object(verify_release_mirrors, "download", side_effect=download):
            with self.assertRaises(verify_release_mirrors.MirrorVerificationError):
                verify_release_mirrors.verify_mirrors(
                    self.URLS,
                    expected_size=self.SIZE,
                    expected_sha256=self.DIGEST,
                )

        self.assertEqual(2, len(calls))

    def test_invalid_urls_and_duplicates_fail_before_network(self) -> None:
        invalid_sets = (
            ("http://example.invalid/x86qw.zip",),
            ("https://user:password@example.invalid/x86qw.zip",),
            ("https://example.invalid/x86qw.zip?cache=1",),
            ("https://example.invalid/x86qw.zip", "https://example.invalid/x86qw.zip"),
        )
        with mock.patch.object(verify_release_mirrors, "download") as download:
            for urls in invalid_sets:
                with self.subTest(urls=urls):
                    with self.assertRaises(verify_release_mirrors.MirrorVerificationError):
                        verify_release_mirrors.verify_mirrors(
                            urls,
                            expected_size=self.SIZE,
                            expected_sha256=self.DIGEST,
                        )
            download.assert_not_called()

    def test_download_result_cannot_lie_about_the_pinned_identity(self) -> None:
        def download(contract: PinnedArtifact) -> DownloadResult:
            return DownloadResult(
                url=contract.url,
                size=contract.expected_size + 1,
                sha256="b" * 64,
                attempts=1,
                headers={},
                path=Path(contract.destination),
            )

        with mock.patch.object(verify_release_mirrors, "download", side_effect=download):
            with self.assertRaises(verify_release_mirrors.MirrorVerificationError):
                verify_release_mirrors.verify_mirrors(
                    self.URLS[:1],
                    expected_size=self.SIZE,
                    expected_sha256=self.DIGEST,
                )


if __name__ == "__main__":
    unittest.main()
