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
            "maintenance/tools/prepare_tuf_handoff.py",
            "maintenance/tools/render_release_site.py",
            "maintenance/tools/assemble_site_release.py",
            "maintenance/tools/verify_public_tuf.py",
            "maintenance/tools/verify_public_bootstraps.py",
            "maintenance/tools/verify_public_product.py",
            "maintenance/tools/public_install_smoke.py",
            "maintenance/tools/verify_public_acceptance.py",
            "maintenance/tools/tuf_operation_drill.py",
            "maintenance/tools/materialize_lfs.py",
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
            ROOT / ".github/workflows/tuf-metadata-handoff.yml",
            ROOT / ".github/workflows/tuf-monitor.yml",
            ROOT / ".github/workflows/public-acceptance.yml",
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

    def test_tuf_handoff_is_protected_and_binds_the_exact_candidate(self):
        source = (ROOT / ".github/workflows/tuf-metadata-handoff.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("workflow_dispatch:", source)
        self.assertIn("environment: release", source)
        self.assertIn("actions: read", source)
        self.assertIn("verify_external_handoff.py", source)
        self.assertIn('--run-id "$CANDIDATE_RUN_ID"', source)
        self.assertIn('--artifact "$CANDIDATE_ARTIFACT_NAME"', source)
        self.assertIn('--artifact-id "$CANDIDATE_ARTIFACT_ID"', source)
        self.assertIn("--workflow .github/workflows/release.yml", source)
        self.assertNotIn('--run-id "${{ inputs.candidate_run_id }}"', source)
        self.assertIn("prepare_tuf_handoff.py", source)
        self.assertIn("publish_tuf_metadata.py", source)
        self.assertIn("candidate/catalog.json", source)
        self.assertIn("overwrite: false", source)
        self.assertIn("retention-days: 90", source)
        self.assertIn("tuf-metadata-${{ inputs.candidate_commit }}-${{ github.run_id }}-${{ github.run_attempt }}", source)
        self.assertNotIn("CLOUDFLARE_API_TOKEN", source)

    def test_tuf_handoff_fetches_sha_and_installs_trust_backend(self):
        source = (ROOT / ".github/workflows/tuf-metadata-handoff.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("ref: main", source)
        self.assertNotIn("ref: ${{ inputs.candidate_commit }}", source)
        self.assertIn("Install pinned trust dependencies from vendored wheels", source)
        self.assertIn(
            "python -m pip install --no-index --find-links maintenance/vendor/wheels --require-hashes -r maintenance/requirements-trust.txt",
            source,
        )
        self.assertIn('git fetch --no-tags origin "$CANDIDATE_COMMIT"', source)
        self.assertIn('git checkout --detach "$CANDIDATE_COMMIT"', source)
        self.assertIn('test "$(git rev-parse HEAD)" = "$CANDIDATE_COMMIT"', source)
        self.assertIn("Checkout immutable candidate commit", source)

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

    def test_release_publish_permission_is_explicit_without_broadening_other_jobs(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertNotRegex(source, r"(?m)^permissions:\n  contents: (?:read|write)\n\nconcurrency:")
        self.assertIn("  publish-assets:\n", source)
        publish = source.split("  publish-assets:\n", 1)[1].split("\n  publish-gitlab:\n", 1)[0]
        self.assertIn("    permissions:\n      contents: read\n", publish)
        self.assertIn("GH_TOKEN: ${{ secrets.RELEASE_GH_TOKEN }}", publish)
        self.assertNotIn("GH_TOKEN: ${{ github.token }}", publish)
        for job_name in ("build-once", "portable-verify", "approval-preview", "approval", "verify-release-mirrors"):
            start = source.index(f"  {job_name}:\n")
            following = re.search(r"\n  [A-Za-z0-9_-]+:\n", source[start + 1:])
            end = start + 1 + following.start() if following else len(source)
            block = source[start:end]
            self.assertIn("    permissions:\n      contents: read\n", block, job_name)

    def test_build_once_fetches_history_for_public_migration_fixtures(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        build = source.split("  build-once:\n", 1)[1].split("\n  portable-verify:\n", 1)[0]
        self.assertIn("fetch-depth: 0", build)
        self.assertIn("fetch-tags: true", build)

    def test_promotion_reuses_a_verified_prebuilt_candidate_without_rebuild(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        build = source.split("  build-once:\n", 1)[1].split("\n  portable-verify:\n", 1)[0]
        for input_name in (
            "candidate_run_id:",
            "candidate_artifact_id:",
            "candidate_artifact_name:",
            "candidate_sha256:",
        ):
            self.assertIn(input_name, source)
        self.assertIn("Validate prebuilt candidate provenance", build)
        self.assertIn("maintenance/tools/verify_external_handoff.py", build)
        self.assertIn("Download immutable candidate artifact", build)
        self.assertIn("candidate_sha256", build)
        self.assertIn("if: ${{ inputs.mode == 'rehearsal' }}", build)
        self.assertIn("if: ${{ inputs.mode != 'rehearsal' }}", build)

    def test_release_jobs_fetch_candidate_sha_after_main_checkout(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertEqual(6, source.count("name: Checkout immutable candidate commit"))
        self.assertEqual(11, source.count("ref: main"))
        self.assertNotIn("ref: ${{ inputs.candidate_commit }}", source)
        self.assertEqual(6, source.count('git fetch --no-tags origin "$CANDIDATE_COMMIT"'))
        self.assertEqual(6, source.count('git checkout --detach "$CANDIDATE_COMMIT"'))
        self.assertEqual(7, source.count('test "$(git rev-parse HEAD)" = "$CANDIDATE_COMMIT"'))

    def test_publication_jobs_pin_release_code_separately_from_candidate_bytes(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        for job_name in ("publish-assets:", "publish-gitlab:", "verify-release-mirrors:", "metadata-last:"):
            start = source.index(f"  {job_name}\n")
            following = re.search(r"\n  [A-Za-z0-9_-]+:\n", source[start + 1:])
            end = start + 1 + following.start() if following else len(source)
            block = source[start:end]
            self.assertIn("name: Pin release orchestration commit", block)
            self.assertIn('RELEASE_COMMIT: ${{ github.sha }}', block)
            self.assertIn('git fetch --no-tags origin "$RELEASE_COMMIT"', block)
            self.assertNotIn("name: Checkout immutable candidate commit", block)
            self.assertNotIn('git checkout --detach "$CANDIDATE_COMMIT"', block)

    def test_release_trust_jobs_install_pinned_backend(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        install = "python -m pip install --no-index --find-links maintenance/vendor/wheels --require-hashes -r maintenance/requirements-trust.txt"
        self.assertEqual(3, source.count("Install pinned trust dependencies from vendored wheels"))
        self.assertEqual(3, source.count(install))

    def test_release_reuses_lfs_cache_and_materializes_on_miss(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn(
            "actions/cache@0057852bfaa89a56745cba8c7296529d2fc39830",
            source,
        )
        self.assertIn("path: .git/lfs/objects", source)
        self.assertIn(
            "key: x86qw-lfs-v3-${{ inputs.candidate_commit }}",
            source,
        )
        self.assertIn("enableCrossOsArchive: true", source)
        self.assertIn("cache-hit", source)
        self.assertIn("git lfs checkout", source)
        self.assertIn("materialize_lfs.py", source)
        self.assertIn("git lfs fsck --pointers", source)
        self.assertNotIn("git lfs pull", source)

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

        self.assertEqual(12, len(blocks))
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
        self.assertIn("promote-1.0", source)
        self.assertIn("inputs.mode != 'rehearsal'", source)
        self.assertIn("environment: release", source)
        self.assertIn("release-evidence.json", source)
        self.assertIn("M3", source)

    def test_release_exposes_a_protected_rc_promotion_path(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("- promote-rc", source)
        self.assertIn("inputs.mode != 'rehearsal'", source)
        self.assertIn("promote-rc) [[ \"$CANDIDATE_VERSION\" =~ ^1\\.0\\.0-rc\\.[0-9]+$ ]]", source)
        self.assertIn("Protected release approval boundary", source)

    def test_signing_workflow_fetches_sha_before_verifying_candidate(self):
        source = (ROOT / ".github/workflows/sign-native-evidence.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("ref: main", source)
        self.assertNotIn("ref: ${{ inputs.candidate_commit }}", source)
        self.assertIn('git fetch --no-tags origin "$CANDIDATE_COMMIT"', source)
        self.assertIn('git checkout --detach "$CANDIDATE_COMMIT"', source)
        self.assertIn('test "$(git rev-parse HEAD)" = "$CANDIDATE_COMMIT"', source)
        self.assertIn("Checkout immutable candidate commit", source)

    def test_promotion_rechecks_live_p0_p1_blockers_before_m3_gate(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("release-blockers:", source)
        self.assertIn("maintenance/tools/check_release_blockers.py", source)
        self.assertIn("issues: read", source)
        self.assertLess(source.index("release-blockers:"), source.index("promotion-gate:"))
        gate = source[source.index("promotion-gate:"):]
        self.assertIn("release-blockers", gate.split("verify-release-mirrors:", 1)[0])

    def test_promotion_attaches_authorized_m3_evidence_without_rebuild(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("native_evidence_run_id", source)
        self.assertIn("native_evidence_artifact_id", source)
        self.assertIn("attach-native-evidence:", source)
        self.assertIn("actions: read", source)
        self.assertIn("maintenance/tools/attach_release_evidence.py", source)
        self.assertIn("run-id:", source)
        self.assertIn("M3_TRUST_ROOT_B64", source)
        self.assertIn("maintenance/trust/m3-root.json", source)
        self.assertIn("M3 root secret diverges from the versioned public root", source)
        self.assertNotIn("--trust-root m3/root.json", source)
        self.assertIn("needs.attach-native-evidence.outputs.artifact-id", source)

    def test_promotion_materializes_and_verifies_durable_evidence_receipt(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("native_evidence_artifact_name:", source)
        self.assertIn("maintenance/tools/release_receipt.py", source)
        self.assertIn("evidence-root.json", source)
        self.assertIn("release-receipt.json", source)
        self.assertIn("release-evidence.json", source)
        self.assertIn("release_receipt.py verify", source)
        self.assertIn("release-receipt-coordinates.json", source)

    def test_final_receipt_binds_public_acceptance_handoff(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        receipt_step = source.split("      - name: Create durable evidence root and release receipt", 1)[1]
        receipt_step = receipt_step.split("      - name: Upload evidence-bound candidate without overwrite", 1)[0]
        self.assertIn("public_acceptance", receipt_step)
        self.assertIn("ACCEPTANCE_COMMIT", receipt_step)
        self.assertIn("ACCEPTANCE_RUN_ID", receipt_step)
        self.assertIn("ACCEPTANCE_ARTIFACT_ID", receipt_step)
        self.assertIn("ACCEPTANCE_ARTIFACT_NAME", receipt_step)
        self.assertIn("ACCEPTANCE_VERSION", receipt_step)
        self.assertIn("inputs.mode == 'promote-1.0'", receipt_step)

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
        self.assertIn('RELEASE_COMMIT: ${{ github.sha }}', metadata_step)
        self.assertIn('--commit "$RELEASE_COMMIT"', metadata_step)
        self.assertIn('--head-branch main', metadata_step)
        self.assertNotIn('--commit "$CANDIDATE_COMMIT"', metadata_step)

    def test_metadata_last_assembles_the_rendered_candidate_site(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("--site-source candidate/site/public", source)
        self.assertNotIn("--output release-work/site-preview", source)
        self.assertNotIn("--site-source site/public", source)

    def test_metadata_last_verifies_the_public_product_projection(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn(
            "maintenance/tools/verify_public_product.py --candidate candidate",
            source,
        )
        self.assertLess(
            source.index("verify_public_product.py"),
            source.index("Record metadata-last post-publish result"),
        )

    def test_public_acceptance_runs_the_full_disposable_m3_lifecycle(self):
        path = ROOT / ".github/workflows/public-acceptance.yml"
        source = path.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", source)
        self.assertIn("runs-on: [self-hosted, macOS, arm64, M3]", source)
        self.assertIn("public_install_smoke.py", source)
        self.assertIn("--full-lifecycle", source)
        self.assertIn(
            "python -m pip install --no-index --find-links maintenance/vendor/wheels "
            "--require-hashes -r maintenance/requirements-trust.txt",
            source,
        )
        self.assertIn("--online-only", (ROOT / "maintenance/tools/public_install_smoke.py").read_text(encoding="utf-8"))
        self.assertIn("overwrite: false", source)
        self.assertIn("retention-days: 90", source)

    def test_final_promotion_requires_a_proven_public_rc_acceptance(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("public_acceptance_run_id:", source)
        self.assertIn("public_acceptance_artifact_id:", source)
        self.assertIn("public_acceptance_artifact_name:", source)
        self.assertIn("public_acceptance_version:", source)
        self.assertIn("verify-public-acceptance:", source)
        gate = source.split("  verify-public-acceptance:\n", 1)[1]
        self.assertIn("verify_external_handoff.py", gate)
        self.assertIn(".github/workflows/public-acceptance.yml", gate)
        self.assertIn("verify_public_acceptance.py", gate)
        self.assertIn("needs.verify-public-acceptance.result", source)


if __name__ == "__main__":
    unittest.main()
