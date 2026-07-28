from __future__ import annotations

import io
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "maintenance/tools"))

from public_upstreams import (  # noqa: E402
    git_remote_revision,
    git_remote_tree,
    github_commit_revision,
    github_latest_release,
    remote_content_length,
)


class Response(io.BytesIO):
    def __init__(self, body: bytes = b"", *, url: str = "https://example.invalid/", length: str = "1") -> None:
        super().__init__(body)
        self.url = url
        self.headers = {"Content-Length": length}

    def geturl(self) -> str:
        return self.url


class PublicUpstreamTests(unittest.TestCase):
    def test_git_remote_revision_uses_the_public_git_protocol(self) -> None:
        result = subprocess.CompletedProcess([], 0, "a" * 40 + "\trefs/heads/master\n", "")
        with mock.patch("public_upstreams.subprocess.run", return_value=result) as run:
            self.assertEqual("a" * 40, git_remote_revision("https://example.invalid/repo.git", "refs/heads/master"))
        self.assertNotIn("api.github.com", " ".join(run.call_args.args[0]))

    def test_git_tree_is_read_without_checking_out_blobs(self) -> None:
        outputs = [
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 0, "b" * 40 + "\n", ""),
            subprocess.CompletedProcess([], 0, b"100644 blob " + b"c" * 40 + b"\tqw/file.pk3\0", b""),
        ]
        with mock.patch("public_upstreams.subprocess.run", side_effect=outputs) as run:
            revision, entries = git_remote_tree("https://example.invalid/repo.git", "master")
        self.assertEqual("b" * 40, revision)
        self.assertEqual(("qw/file.pk3", "c" * 40), (entries[0].path, entries[0].sha1))
        clone = run.call_args_list[0].args[0]
        self.assertIn("--filter=blob:none", clone)
        self.assertIn("--no-checkout", clone)

    def test_release_commit_and_size_use_public_web_urls_without_authorization(self) -> None:
        release = Response(url="https://github.com/QW-Group/ktx/releases/tag/1.47")
        commit = Response(
            b'<a href="/QW-Group/ezquake-source/commit/' + b"d" * 40 + b'">commit</a>',
        )
        artifact = Response(length="403006")
        with mock.patch("public_upstreams.urllib.request.urlopen", side_effect=[release, commit, artifact]) as opened:
            self.assertEqual("1.47", github_latest_release("QW-Group/ktx"))
            self.assertEqual("d" * 40, github_commit_revision("QW-Group/ezquake-source", "d" * 7))
            self.assertEqual(403006, remote_content_length("https://github.com/example/download.zip"))
        for call in opened.call_args_list:
            request = call.args[0]
            self.assertNotIn("Authorization", dict(request.header_items()))


if __name__ == "__main__":
    unittest.main()
