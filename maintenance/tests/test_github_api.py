from __future__ import annotations

import io
import os
import subprocess
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "maintenance/tools"))

import github_api  # noqa: E402


class GitHubApiTests(unittest.TestCase):
    def tearDown(self) -> None:
        github_api.github_token.cache_clear()

    def test_existing_gh_authentication_is_reused(self) -> None:
        completed = subprocess.CompletedProcess(["gh", "auth", "token"], 0, "secret-token\n", "")
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("github_api.shutil.which", return_value="/usr/local/bin/gh"):
                with mock.patch("github_api.subprocess.run", return_value=completed):
                    with mock.patch("github_api.urllib.request.urlopen", return_value=io.BytesIO(b'{"ok": true}')) as opened:
                        self.assertEqual({"ok": True}, github_api.github_json("rate_limit"))
        request = opened.call_args.args[0]
        self.assertEqual("Bearer secret-token", request.headers["Authorization"])

    def test_environment_token_has_priority_without_calling_gh(self) -> None:
        with mock.patch.dict(os.environ, {"GH_TOKEN": "environment-token"}, clear=True):
            with mock.patch("github_api.subprocess.run") as run:
                self.assertEqual("environment-token", github_api.github_token())
        run.assert_not_called()

    def test_anonymous_rate_limit_error_explains_how_to_authenticate(self) -> None:
        error = urllib.error.HTTPError("https://api.github.com/test", 403, "rate limit", {}, None)
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("github_api.shutil.which", return_value=None):
                with mock.patch("github_api.urllib.request.urlopen", side_effect=error):
                    with self.assertRaisesRegex(ValueError, "gh auth login"):
                        github_api.github_json("test")


if __name__ == "__main__":
    unittest.main()
