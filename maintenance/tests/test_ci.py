from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class ContinuousIntegrationTests(unittest.TestCase):
    def test_pull_request_workflow_is_read_only_and_multiplatform(self):
        workflow = (ROOT / ".github/workflows/validate.yml").read_text(encoding="utf-8")
        self.assertIn("pull_request:", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("ubuntu-latest", workflow)
        self.assertIn("macos-latest", workflow)
        self.assertIn("windows-latest", workflow)
        self.assertIn('python: ["3.10", "3.13"]', workflow)
        self.assertIn("git lfs pull", workflow)
        self.assertIn("git lfs fsck", workflow)
        self.assertIn("./maintenance/manage.py verify", workflow)
        self.assertIn("git diff --check", workflow)
        self.assertIn("wrangler@4.114.0 deploy --dry-run", workflow)
        self.assertNotIn("secrets.", workflow)

    def test_publication_is_manual_protected_and_depends_on_validation(self):
        workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("needs: validate", workflow)
        self.assertIn("environment: release", workflow)
        self.assertIn("git diff --exit-code", workflow)
        self.assertIn("./maintenance/manage.py publish --dry-run", workflow)
        self.assertIn("./maintenance/manage.py publish", workflow)
        self.assertNotIn("pull_request:", workflow)

    def test_large_runtime_and_demo_payloads_are_lfs_managed(self):
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("dist/**/*.mvd filter=lfs", attributes)
        self.assertIn("dist/servers/**/x86qw/runtime/** filter=lfs", attributes)
        self.assertIn("dist/services/**/x86qw/runtime/** filter=lfs", attributes)


if __name__ == "__main__":
    unittest.main()
