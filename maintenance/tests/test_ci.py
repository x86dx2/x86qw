from __future__ import annotations

import json
import subprocess
import sys
import tempfile
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
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("maintenance/tools/check_committed_diff.py", workflow)
        self.assertIn("wrangler@4.114.0 deploy --dry-run", workflow)
        self.assertNotIn("secrets.", workflow)

    def test_committed_diff_gate_uses_event_shas_and_rejects_committed_whitespace(self):
        script = ROOT / "maintenance/tools/check_committed_diff.py"
        source = script.read_text(encoding="utf-8")
        self.assertIn('event_name == "pull_request"', source)
        self.assertIn('event_name == "push"', source)
        self.assertIn('"git", "diff", "--check"', source)
        self.assertIn('"git", "show", "--check"', source)
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            subprocess.run(["git", "init", "-q", repository], check=True)
            subprocess.run(["git", "config", "user.email", "ci@example.invalid"], cwd=repository, check=True)
            subprocess.run(["git", "config", "user.name", "CI fixture"], cwd=repository, check=True)
            fixture = repository / "fixture.txt"
            fixture.write_text("válido\n", encoding="utf-8")
            subprocess.run(["git", "add", "fixture.txt"], cwd=repository, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=repository, check=True)
            base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()
            fixture.write_text("espaço inválido   \n", encoding="utf-8")
            subprocess.run(["git", "add", "fixture.txt"], cwd=repository, check=True)
            subprocess.run(["git", "commit", "-qm", "bad whitespace"], cwd=repository, check=True)
            head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()
            event = repository / "event.json"
            event.write_text(json.dumps({
                "pull_request": {"base": {"sha": base}, "head": {"sha": head}},
            }), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(script), "--event-name", "pull_request", "--event-file", str(event)],
                cwd=repository, text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("trailing whitespace", result.stdout + result.stderr)

    def test_publication_is_manual_protected_and_depends_on_validation(self):
        workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("needs: validate", workflow)
        self.assertIn("environment: release", workflow)
        self.assertIn("git diff --exit-code", workflow)
        self.assertIn("./maintenance/manage.py publish --dry-run", workflow)
        self.assertIn("./maintenance/manage.py publish", workflow)
        self.assertIn("GLAB_TOKEN: ${{ secrets.GITLAB_TOKEN }}", workflow)
        self.assertIn("GLAB_TOKEN=\"${GLAB_TOKEN//$'\\r'/}\"", workflow)
        self.assertIn("GLAB_TOKEN=\"${GLAB_TOKEN//$'\\n'/}\"", workflow)
        self.assertIn('export GITLAB_TOKEN="${GLAB_TOKEN}"', workflow)
        self.assertNotIn("pull_request:", workflow)

    def test_large_runtime_and_demo_payloads_are_lfs_managed(self):
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("dist/**/*.mvd filter=lfs", attributes)
        self.assertIn("dist/servers/**/x86qw/runtime/** filter=lfs", attributes)
        self.assertIn("dist/services/**/x86qw/runtime/** filter=lfs", attributes)


if __name__ == "__main__":
    unittest.main()
