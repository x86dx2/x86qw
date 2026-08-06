from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FULL_ACTION = re.compile(r"uses:\s+[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}(?:\s+#.*)?$")


class ReleaseWorkflowTests(unittest.TestCase):
    def test_all_actions_are_pinned_and_site_uses_lockfile(self):
        workflows = [
            ROOT / ".github/workflows/validate.yml",
            ROOT / ".github/workflows/release.yml",
        ]
        for path in workflows:
            source = path.read_text(encoding="utf-8")
            uses = [
                line.strip().removeprefix("- ")
                for line in source.splitlines()
                if line.strip().removeprefix("- ").startswith("uses:")
            ]
            self.assertTrue(uses, path)
            external = [line for line in uses if not line.startswith("uses: ./")]
            self.assertTrue(external, path)
            self.assertTrue(all(FULL_ACTION.fullmatch(line) for line in external), external)
        self.assertTrue((ROOT / "site/package.json").is_file())
        self.assertTrue((ROOT / "site/package-lock.json").is_file())
        self.assertIn("npm ci", workflows[0].read_text(encoding="utf-8"))

    def test_release_builds_once_then_reuses_one_digest_bound_candidate(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertEqual(1, source.count("release_candidate.py prepare"))
        self.assertEqual(1, source.count("maintenance/manage.py build"))
        self.assertIn("artifact-digest", source)
        self.assertIn("artifact-ids:", source)
        self.assertIn("release_candidate.py rehearse", source)
        self.assertIn("release_candidate.py promote", source)
        self.assertLess(source.index("verify-release-mirrors"), source.index("metadata-last"))
        self.assertNotIn("gh release create", source)
        self.assertNotIn("maintenance/manage.py publish", source)

    def test_candidate_binds_native_runtime_bytes_before_any_smoke(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("for runtime_root in clients servers services; do", source)
        self.assertIn('find "dist/$runtime_root" -type f -print0', source)
        self.assertIn(
            '"release-work/input/runtime/$runtime_root/$relative"',
            source,
        )
        self.assertLess(
            source.index("for runtime_root in clients servers services; do"),
            source.index("release_candidate.py prepare"),
        )

    def test_candidate_declares_candidate_owned_native_entrypoint_before_prepare(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("maintenance/tools/native_case_entrypoint.py", source)
        self.assertIn("runtime/native-smoke/macos-arm64/x86qw-native-smoke", source)
        self.assertIn("runtime/native-smoke/macos-arm64/entrypoint.json", source)
        self.assertLess(
            source.index("native_case_entrypoint.py"),
            source.index("release_candidate.py prepare"),
        )

    def test_release_keeps_rehearsal_separate_from_fail_closed_promotion(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("mode == 'rehearsal'", source)
        self.assertIn("mode == 'promote-1.0'", source)
        self.assertIn("environment: release", source)
        self.assertIn("release-evidence.json", source)
        self.assertIn("M3", source)


if __name__ == "__main__":
    unittest.main()
