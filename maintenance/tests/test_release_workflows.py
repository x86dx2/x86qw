from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FULL_ACTION = re.compile(r"uses:\s+[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}(?:\s+#.*)?$")


class ReleaseWorkflowTests(unittest.TestCase):
    def test_direct_python_entrypoints_used_by_release_workflows_start(self):
        scripts = (
            "maintenance/tools/build_installer_bundle.py",
            "maintenance/tools/build_release_catalog.py",
            "maintenance/tools/release_candidate.py",
            "maintenance/tools/check_release_blockers.py",
            "maintenance/tools/verify_external_handoff.py",
            "maintenance/tools/attach_release_evidence.py",
            "maintenance/tools/publish_github_candidate.py",
            "maintenance/tools/publish_gitlab_candidate.py",
            "maintenance/tools/verify_release_mirrors.py",
            "maintenance/tools/publish_tuf_metadata.py",
            "maintenance/tools/render_release_site.py",
            "maintenance/tools/assemble_site_release.py",
            "maintenance/tools/verify_public_tuf.py",
            "maintenance/tools/verify_public_bootstraps.py",
        )
        for relative in scripts:
            with self.subTest(script=relative):
                result = subprocess.run(
                    [sys.executable, str(ROOT / relative), "--help"],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(0, result.returncode, result.stderr)

    def test_all_actions_are_pinned_and_site_uses_lockfile(self):
        workflows = [
            ROOT / ".github/workflows/validate.yml",
            ROOT / ".github/workflows/release.yml",
            ROOT / ".github/workflows/sign-native-evidence.yml",
            ROOT / ".github/workflows/tuf-monitor.yml",
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
        self.assertIn('--release-title "$RELEASE_TITLE"', source)
        self.assertIn("artifact-ids:", source)
        self.assertIn("release_candidate.py rehearse", source)
        self.assertIn("release_candidate.py promote", source)
        self.assertLess(source.index("verify-release-mirrors"), source.index("metadata-last"))
        self.assertNotIn("gh release create", source)
        self.assertNotIn("maintenance/manage.py publish", source)

    def test_candidate_downloads_flatten_artifact_layout(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        lines = source.splitlines()
        starts = [
            index
            for index, line in enumerate(lines)
            if "uses: actions/download-artifact@" in line
        ]
        blocks = []
        for start in starts:
            indent = len(lines[start]) - len(lines[start].lstrip())
            end = next(
                (
                    index
                    for index in range(start + 1, len(lines))
                    if lines[index].startswith(" " * indent + "- ")
                ),
                len(lines),
            )
            blocks.append("\n".join(lines[start:end]))

        self.assertEqual(10, len(blocks))
        for block in blocks:
            self.assertIn("artifact-ids:", block)
            self.assertRegex(block, r"(?m)^\s+merge-multiple:\s*true\s*$")
            self.assertIn("path:", block)

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

    def test_candidate_staging_uses_portable_directory_creation(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertNotIn("install -Dm", source)
        self.assertIn(
            'destination="release-work/input/content/$relative"',
            source,
        )
        self.assertIn(
            'destination="release-work/input/runtime/$runtime_root/$relative"',
            source,
        )
        self.assertEqual(2, source.count('mkdir -p "$(dirname "$destination")"'))

    def test_candidate_contains_the_rendered_public_site_before_transport(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        build = source.split("      - name: Verify candidate before transport", 1)[0]
        self.assertIn("maintenance/tools/render_release_site.py", build)
        self.assertIn("--source site/public", build)
        self.assertIn("--catalog release-work/input/catalog.json", build)
        self.assertIn("--product release-work/input/product.json", build)
        self.assertIn("--bootstrap-source dist/installer/bin", build)
        self.assertIn("--output release-work/input/site/public", build)
        self.assertIn("rm -rf release-work/input/site/public/api/v1/trust", build)
        self.assertLess(
            build.index("--product release-work/input/product.json"),
            build.index("release_candidate.py prepare"),
        )
        self.assertNotIn(
            "dist/installer/bin/install.sh release-work/input/site/public/install.sh",
            build,
        )

    def test_metadata_last_uses_the_candidate_site_without_rebinding(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        metadata = source.split("      - name: Assemble one immutable public site tree", 1)[1]
        metadata = metadata.split("      - name: Dry-run the exact public site before deployment", 1)[0]
        self.assertIn("--site-source candidate/site/public", metadata)
        self.assertNotIn("--site-source site/public", metadata)
        self.assertNotIn("--bootstrap-source", metadata)

    def test_candidate_declares_candidate_owned_native_entrypoint_before_prepare(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("maintenance/native_case_entrypoint.py", source)
        self.assertIn("runtime/native-smoke/macos-arm64/x86qw-native-smoke", source)
        self.assertIn("runtime/native-smoke/macos-arm64/entrypoint.json", source)
        self.assertLess(
            source.index("native_case_entrypoint.py"),
            source.index("release_candidate.py prepare"),
        )

    def test_candidate_entrypoint_contract_is_present_and_closed(self):
        contract = ROOT / "maintenance/native/macos-arm64/entrypoint.json"
        self.assertEqual(
            {
                "format": 1,
                "project": "x86qw",
                "platform": "macOS-ARM64",
                "protocol": "x86qw-native-case-v1",
                "entrypoint_artifact": "runtime/native-smoke/macos-arm64/x86qw-native-smoke",
            },
            json.loads(contract.read_text(encoding="utf-8")),
        )
        self.assertTrue((ROOT / "maintenance/native_case_entrypoint.py").is_file())

    def test_build_once_exposes_the_exact_candidate_artifact_name(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("candidate-artifact-name:", source)
        self.assertIn(
            'candidate-${CANDIDATE_COMMIT}-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}',
            source,
        )

    def test_release_keeps_rehearsal_separate_from_fail_closed_promotion(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("mode == 'rehearsal'", source)
        self.assertIn("mode == 'promote-1.0'", source)
        self.assertIn("environment: release", source)
        self.assertIn("release-evidence.json", source)
        self.assertIn("M3", source)

    def test_promotion_rechecks_live_p0_p1_blockers_before_m3_gate(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("release-blockers:", source)
        self.assertIn("maintenance/tools/check_release_blockers.py", source)
        self.assertIn("issues: read", source)
        self.assertLess(source.index("release-blockers:"), source.index("promotion-gate:"))
        gate = source[source.index("promotion-gate:"):]
        self.assertIn("release-blockers", gate.split("verify-release-mirrors:", 1)[0])

    def test_promotion_attaches_external_m3_evidence_without_rebuild(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("native_evidence_run_id", source)
        self.assertIn("native_evidence_artifact_id", source)
        self.assertIn("attach-native-evidence:", source)
        self.assertIn("actions: read", source)
        self.assertIn("maintenance/tools/attach_release_evidence.py", source)
        self.assertIn("run-id:", source)
        self.assertIn("M3_TRUST_ROOT_B64", source)
        self.assertNotIn("--trust-root m3/root.json", source)
        self.assertIn("needs.attach-native-evidence.outputs.artifact-id", source)

    def test_metadata_last_uses_the_publish_tuf_metadata_cli_contract(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        metadata_step = source.split("      - name: Authenticate signed TUF against the exact candidate catalog", 1)[1]
        metadata_step = metadata_step.split("      - name: Assemble one immutable public site tree", 1)[0]
        self.assertIn("--metadata-dir signed-tuf", metadata_step)
        self.assertIn("--stage-dir release-work/trust", metadata_step)
        self.assertNotIn("--signed-repository", metadata_step)
        self.assertNotIn("--previous-repository", metadata_step)
        self.assertNotIn("--output release-work/trust", metadata_step)

    def test_metadata_last_verifies_the_external_tuf_artifact_provenance(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        metadata_step = source.split("  metadata-last:\n", 1)[1]
        self.assertIn("tuf_metadata_artifact_name:", source)
        self.assertIn("tuf_metadata_workflow:", source)
        self.assertIn("maintenance/tools/verify_external_handoff.py", metadata_step)
        self.assertIn('--artifact "$TUF_ARTIFACT_NAME"', metadata_step)
        self.assertIn('--artifact-id "$TUF_ARTIFACT_ID"', metadata_step)
        self.assertIn('--workflow "$TUF_WORKFLOW"', metadata_step)

    def test_metadata_last_assembles_the_rendered_candidate_site(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("--site-source candidate/site/public", source)
        self.assertNotIn("--output release-work/site-preview", source)
        self.assertNotIn("--site-source site/public", source)


if __name__ == "__main__":
    unittest.main()
