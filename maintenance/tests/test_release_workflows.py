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
    def test_release_rehearsal_disables_optional_other_os_preview(self):
        validate = (ROOT / ".github/workflows/validate.yml").read_text(encoding="utf-8")
        release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("run_preview:", validate)
        self.assertIn("if: inputs.run_preview == true", validate)
        self.assertIn(
            "  validate:\n    uses: ./.github/workflows/validate.yml\n    with:\n      run_preview: false",
            release,
        )

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
            "maintenance/tools/monitor_public_tuf.py",
            "maintenance/tools/verify_tuf_operation_report.py",
            "maintenance/tools/tuf_operation_drill.py",
            "maintenance/tools/tuf_timestamp_renewal.py",
            "maintenance/tools/verify_tuf_timestamp_renewal.py",
            "maintenance/tools/verify_tuf_timestamp_publication.py",
            "maintenance/tools/tuf_snapshot_renewal.py",
            "maintenance/tools/verify_tuf_snapshot_renewal.py",
            "maintenance/tools/verify_tuf_snapshot_publication.py",
            "maintenance/tools/build_soak_report.py",
            "maintenance/tools/verify_soak_report.py",
            "maintenance/tools/validate_release_inputs.py",
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
            ROOT / ".github/workflows/rc-soak.yml",
            ROOT / ".github/workflows/tuf-operation-drill.yml",
            ROOT / ".github/workflows/tuf-timestamp-renewal.yml",
            ROOT / ".github/workflows/tuf-timestamp-publish.yml",
            ROOT / ".github/workflows/tuf-snapshot-renewal.yml",
            ROOT / ".github/workflows/tuf-snapshot-publish.yml",
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

    def test_patch_mirror_publication_is_protected_and_fail_closed(self):
        source = (ROOT / ".github/workflows/patch-mirror.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("workflow_dispatch:", source)
        self.assertIn("environment: release", source)
        self.assertIn("actions: read", source)
        self.assertIn("contents: read", source)
        self.assertIn("verify_external_handoff.py", source)
        self.assertIn("--workflow .github/workflows/release.yml", source)
        self.assertIn("release_candidate.py verify candidate", source)
        self.assertIn("publish_gitlab_candidate.py", source)
        self.assertIn("--publish", source)
        self.assertIn("verify_release_mirrors.py", source)
        self.assertIn("GITLAB_TOKEN: ${{ secrets.GITLAB_TOKEN }}", source)
        self.assertIn("< dist/installer/VERSION", source)
        self.assertNotIn("< VERSION", source)
        self.assertIn("Fast-forward public GitLab source mirror", source)
        self.assertIn("git merge-base --is-ancestor", source)
        self.assertIn("LocalMediaDir=", source)
        self.assertIn("git remote add public-gitlab", source)
        self.assertIn('git lfs push --object-id public-gitlab "$expected_sha"', source)
        self.assertNotIn("git lfs fetch", source)
        self.assertNotIn("git lfs clean", source)
        self.assertIn("sha256sum", source)
        self.assertIn("stat -c %s", source)
        self.assertNotIn("cmp --silent", source)
        self.assertIn("git credential approve", source)
        self.assertIn("git credential reject", source)
        self.assertIn('"$RELEASE_CODE_COMMIT:refs/heads/main"', source)
        self.assertNotIn("oauth2:${GITLAB_TOKEN}", source)
        self.assertNotIn("curl ", source)
        self.assertNotIn("--clobber", source)

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

    def test_timestamp_renewal_is_protected_and_never_publishes(self):
        source = (ROOT / ".github/workflows/tuf-timestamp-renewal.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("workflow_dispatch:", source)
        self.assertIn("environment: release", source)
        self.assertIn("actions: read", source)
        self.assertIn("TUF_TIMESTAMP_KEY_B64", source)
        self.assertIn("tuf_timestamp_renewal.py", source)
        self.assertIn('default: "168"', source)
        self.assertIn("verify_external_handoff.py", source)
        self.assertIn("tuf-metadata-handoff.yml", source)
        self.assertIn("release_code_commit:", source)
        self.assertIn("ref: ${{ inputs.release_code_commit }}", source)
        self.assertIn("Validate the pinned renewal-code checkout", source)
        self.assertNotIn("ref: main", source)
        self.assertIn("overwrite: false", source)
        self.assertIn("published=false", source)
        self.assertIn("tuf-timestamp-renewal-${{ inputs.candidate_commit }}-${{ github.run_id }}-${{ github.run_attempt }}", source)
        self.assertNotIn("publish_tuf_metadata.py", source)
        self.assertNotIn("CLOUDFLARE_API_TOKEN", source)

    def test_timestamp_publication_is_protected_and_binds_renewal_before_deploy(self):
        source = (ROOT / ".github/workflows/tuf-timestamp-publish.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("workflow_dispatch:", source)
        self.assertIn("environment: release", source)
        self.assertIn("actions: read", source)
        self.assertIn("verify_external_handoff.py", source)
        self.assertIn("tuf-timestamp-renewal.yml", source)
        self.assertIn("verify_tuf_timestamp_renewal.py", source)
        self.assertIn('"changed_files": ["metadata/timestamp.json"]', source)
        self.assertIn("publish_tuf_metadata.py", source)
        self.assertIn("assemble_site_release.py", source)
        self.assertIn("CLOUDFLARE_API_TOKEN", source)
        self.assertIn("verify_public_tuf.py", source)
        self.assertIn("verify_tuf_timestamp_publication.py", source)
        self.assertIn("Verify immutable timestamp publication receipt", source)
        self.assertIn("overwrite: false", source)
        self.assertIn(
            "tuf-timestamp-publication-${{ inputs.candidate_commit }}-${{ github.run_id }}-${{ github.run_attempt }}",
            source,
        )
        self.assertNotIn("generate_trust_metadata.py", source)

    def test_snapshot_renewal_is_protected_and_never_publishes(self):
        source = (ROOT / ".github/workflows/tuf-snapshot-renewal.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("workflow_dispatch:", source)
        self.assertIn("environment: release", source)
        self.assertIn("actions: read", source)
        self.assertIn("TUF_SNAPSHOT_KEY_B64", source)
        self.assertIn("TUF_TIMESTAMP_KEY_B64", source)
        self.assertIn("tuf_snapshot_renewal.py", source)
        self.assertIn('default: "90"', source)
        self.assertIn('default: "30"', source)
        self.assertIn("verify_external_handoff.py", source)
        self.assertIn("tuf-metadata-handoff.yml", source)
        self.assertIn("release_code_commit:", source)
        self.assertIn("ref: ${{ inputs.release_code_commit }}", source)
        self.assertIn("Validate the pinned renewal-code checkout", source)
        self.assertNotIn("ref: main", source)
        self.assertIn("overwrite: false", source)
        self.assertIn("published=false", source)
        self.assertIn("tuf-snapshot-renewal-${{ inputs.candidate_commit }}-${{ github.run_id }}-${{ github.run_attempt }}", source)
        self.assertNotIn("publish_tuf_metadata.py", source)
        self.assertNotIn("CLOUDFLARE_API_TOKEN", source)

    def test_snapshot_publication_is_protected_and_binds_renewal_before_deploy(self):
        source = (ROOT / ".github/workflows/tuf-snapshot-publish.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("workflow_dispatch:", source)
        self.assertIn("environment: release", source)
        self.assertIn("actions: read", source)
        self.assertIn("verify_external_handoff.py", source)
        self.assertIn("tuf-snapshot-renewal.yml", source)
        self.assertIn("verify_tuf_snapshot_renewal.py", source)
        self.assertIn("publish_tuf_metadata.py", source)
        self.assertIn("assemble_site_release.py", source)
        self.assertIn("CLOUDFLARE_API_TOKEN", source)
        self.assertIn("verify_public_tuf.py", source)
        self.assertIn("verify_tuf_snapshot_publication.py", source)
        self.assertIn("Verify immutable snapshot publication receipt", source)
        self.assertIn("overwrite: false", source)
        self.assertIn(
            "tuf-snapshot-publication-${{ inputs.candidate_commit }}-${{ github.run_id }}-${{ github.run_attempt }}",
            source,
        )
        self.assertNotIn("generate_trust_metadata.py", source)
        self.assertNotIn("verify_tuf_timestamp_renewal.py", source)
        self.assertNotIn('"changed_files": ["metadata/timestamp.json"]', source)

    def test_public_acceptance_runs_inside_the_protected_release_environment(self):
        source = (ROOT / ".github/workflows/public-acceptance.yml").read_text(
            encoding="utf-8"
        )
        job = source.split("  accept:\n", 1)[1].split("\n    steps:\n", 1)[0]
        self.assertIn("    environment: release\n", job)
        self.assertIn("    permissions:\n      contents: read", job)
        self.assertIn("runs-on: [self-hosted, macOS, arm64, M3]", job)

    def test_release_declares_owner_only_mode_and_defers_external_gates(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("release_audience:", source)
        self.assertIn("owner-only", source)
        self.assertIn("external-public", source)
        self.assertIn("inputs.release_audience == 'external-public'", source)
        self.assertIn("acceptance_scope", source)

    def test_release_dispatch_stays_within_github_input_limit(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        dispatch = source.split("    inputs:\n", 1)[1].split("\n\nconcurrency:", 1)[0]
        names = re.findall(r"^      ([a-z0-9_-]+):$", dispatch, re.MULTILINE)
        self.assertLessEqual(len(names), 25)
        for group in (
            "candidate_handoff",
            "native_evidence_handoff",
            "tuf_metadata_handoff",
            "public_acceptance_handoff",
            "tuf_operation_handoff",
            "soak_handoff",
        ):
            self.assertIn(group, names)
        for legacy in (
            "candidate_run_id",
            "candidate_artifact_id",
            "native_evidence_run_id",
            "public_acceptance_run_id",
            "tuf_operation_run_id",
            "soak_run_id",
        ):
            self.assertNotIn(legacy, names)

    def test_release_gates_require_success_of_all_mandatory_upstream_jobs(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        attach = source.split("  attach-native-evidence:\n", 1)[1].split(
            "\n    needs:", 1
        )[0]
        promotion = source.split("  promotion-gate:\n", 1)[1].split(
            "\n    needs:", 1
        )[0]
        for block, required in (
            (attach, ("build-once", "portable-verify", "approval", "release-blockers")),
            (
                promotion,
                (
                    "build-once",
                    "approval",
                    "release-blockers",
                    "attach-native-evidence",
                ),
            ),
        ):
            for job_name in required:
                self.assertIn(
                    f"needs.{job_name}.result == 'success'",
                    block,
                    job_name,
                )

    def test_final_promotion_rejects_reusing_soaked_candidate_bytes(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        gate = source.split("  promotion-gate:\n", 1)[1].split(
            "\n  publish-assets:\n", 1
        )[0]
        self.assertIn("Require final candidate bytes differ from soaked RC", gate)
        self.assertIn('test "$actual_candidate" != "$SOAK_CANDIDATE_JSON_SHA256"', gate)
        self.assertIn('test "$actual_bundle" != "$SOAK_BUNDLE_SHA256"', gate)

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

    def test_publication_jobs_require_a_successful_promotion_gate(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        expected = {
            "publish-assets": (
                "if: ${{ always() && (inputs.mode == 'promote-rc' || "
                "inputs.mode == 'promote-1.0') && needs.promotion-gate.result == 'success' }}"
            ),
            "publish-gitlab": (
                "if: ${{ always() && (inputs.mode == 'promote-rc' || "
                "inputs.mode == 'promote-1.0') && needs.promotion-gate.result == 'success' "
                "&& needs.publish-assets.result == 'success' }}"
            ),
            "verify-release-mirrors": (
                "if: ${{ always() && (inputs.mode == 'promote-rc' || "
                "inputs.mode == 'promote-1.0') && needs.promotion-gate.result == 'success' "
                "&& needs.publish-assets.result == 'success' "
                "&& needs.publish-gitlab.result == 'success' }}"
            ),
            "metadata-last": (
                "if: ${{ always() && (inputs.mode == 'promote-rc' || "
                "inputs.mode == 'promote-1.0') && needs.promotion-gate.result == 'success' "
                "&& needs.verify-release-mirrors.result == 'success' }}"
            ),
        }
        for job_name, condition in expected.items():
            start = source.index(f"  {job_name}:\n")
            following = re.search(r"\n  [A-Za-z0-9_-]+:\n", source[start + 1:])
            end = start + 1 + following.start() if following else len(source)
            block = source[start:end]
            self.assertIn(condition, block, job_name)

    def test_build_once_fetches_history_for_public_migration_fixtures(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        build = source.split("  build-once:\n", 1)[1].split("\n  portable-verify:\n", 1)[0]
        self.assertIn("fetch-depth: 0", build)
        self.assertIn("fetch-tags: true", build)

    def test_promotion_reuses_a_verified_prebuilt_candidate_without_rebuild(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        build = source.split("  build-once:\n", 1)[1].split("\n  portable-verify:\n", 1)[0]
        self.assertIn("candidate_handoff:", source)
        self.assertIn("fromJSON(inputs.candidate_handoff)", build)
        self.assertIn("Validate prebuilt candidate provenance", build)
        self.assertIn("maintenance/tools/verify_external_handoff.py", build)
        self.assertIn("Download immutable candidate artifact", build)
        self.assertIn("candidate_sha256", build)
        self.assertIn("if: ${{ inputs.mode == 'rehearsal' }}", build)
        self.assertIn("if: ${{ inputs.mode != 'rehearsal' }}", build)

    def test_release_jobs_fetch_candidate_sha_after_main_checkout(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertEqual(6, source.count("name: Checkout immutable candidate commit"))
        self.assertEqual(13, source.count("ref: main"))
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
        self.assertEqual(4, source.count("Install pinned trust dependencies from vendored wheels"))
        self.assertEqual(4, source.count(install))

    def test_release_reuses_lfs_cache_and_materializes_on_miss(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn(
            "actions/cache@cdf6c1fa76f9f475f3d7449005a359c84ca0f306",
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

        self.assertEqual(14, len(blocks))
        for block in blocks:
            self.assertIn("artifact-ids:", block)
            self.assertRegex(block, r"(?m)^\s+merge-multiple:\s*true\s*$")
            self.assertIn("path:", block)

    def test_candidate_artifacts_preserve_hidden_files(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        lines = source.splitlines()
        starts = [
            index
            for index, line in enumerate(lines)
            if "uses: actions/upload-artifact@" in line
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

        self.assertEqual(3, len(blocks))
        for block in blocks:
            self.assertRegex(
                block,
                r"(?m)^\s+path:\s+(?:release-work/candidate|candidate-with-m3|promoted)\s*$",
            )
            self.assertRegex(
                block,
                r"(?m)^\s+include-hidden-files:\s*true\s*$",
            )

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
        self.assertIn("native_evidence_handoff:", source)
        self.assertIn("fromJSON(inputs.native_evidence_handoff)", source)
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
        self.assertIn("native_evidence_handoff:", source)
        self.assertIn("tuf_operation_handoff:", source)
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

    def test_final_receipt_binds_tuf_operation_handoff(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        receipt_step = source.split("      - name: Create durable evidence root and release receipt", 1)[1]
        receipt_step = receipt_step.split("      - name: Upload evidence-bound candidate without overwrite", 1)[0]
        self.assertIn("tuf_operation", receipt_step)
        self.assertIn("TUF_OPERATION_RUN_ID", receipt_step)
        self.assertIn("TUF_OPERATION_ARTIFACT_ID", receipt_step)
        self.assertIn("TUF_OPERATION_ARTIFACT_NAME", receipt_step)
        self.assertIn("TUF_OPERATION_REPORT_SHA256", receipt_step)
        self.assertIn("TUF_OPERATION_OPERATOR", receipt_step)
        self.assertIn("TUF_OPERATION_CUSTODY_HOST", receipt_step)
        self.assertIn("TUF_OPERATION_SLA_HOURS", receipt_step)

    def test_final_promotion_requires_tuf_operation_handoff(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("tuf_operation_handoff:", source)
        self.assertIn("fromJSON(inputs.tuf_operation_handoff)", source)
        block = source.split("  attach-native-evidence:\n", 1)[1]
        block = block.split("  promotion-gate:\n", 1)[0]
        self.assertIn("tuf-operation-drill.yml", block)
        self.assertIn("verify_tuf_operation_report.py", block)
        self.assertIn("TUF_OPERATION_REPORT_SHA256", block)

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
        self.assertIn("tuf_metadata_handoff:", source)
        self.assertIn("fromJSON(inputs.tuf_metadata_handoff)", source)
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

    def test_metadata_last_retries_public_propagation_before_failing(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        metadata = source.split("  metadata-last:\n", 1)[1]
        verification = metadata.split(
            "      - name: Verify public TUF after deployment", 1
        )[1].split("      - name: Record metadata-last post-publish result", 1)[0]
        self.assertIn("for attempt in 1 2 3 4 5", verification)
        self.assertIn("sleep", verification)
        self.assertIn("attempt ${attempt}/5", verification)

    def test_public_acceptance_runs_the_full_disposable_m3_lifecycle(self):
        path = ROOT / ".github/workflows/public-acceptance.yml"
        source = path.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", source)
        self.assertIn("runs-on: [self-hosted, macOS, arm64, M3]", source)
        self.assertIn("public_install_smoke.py", source)
        self.assertIn("--full-lifecycle", source)
        self.assertIn("Record exact public bytes for the final handoff", source)
        self.assertIn("receipt_sha256=", source)
        self.assertIn("bundle_sha256=", source)
        self.assertIn("catalog_sha256=", source)
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
        self.assertIn("public_acceptance_handoff:", source)
        self.assertIn("fromJSON(inputs.public_acceptance_handoff)", source)
        self.assertIn("verify-public-acceptance:", source)
        gate = source.split("  verify-public-acceptance:\n", 1)[1]
        self.assertIn("verify_external_handoff.py", gate)
        self.assertIn(".github/workflows/public-acceptance.yml", gate)
        self.assertIn("verify_public_acceptance.py", gate)
        self.assertIn("--expected-bundle-sha256", gate)
        self.assertIn("--expected-catalog-sha256", gate)
        self.assertIn("needs.verify-public-acceptance.result", source)

    def test_final_promotion_requires_a_proven_completed_rc_soak(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("soak_handoff:", source)
        self.assertIn("fromJSON(inputs.soak_handoff)", source)
        self.assertIn("verify-soak:", source)
        soak = source.split("  verify-soak:\n", 1)[1].split("\n  attach-native-evidence:\n", 1)[0]
        self.assertIn("verify_external_handoff.py", soak)
        self.assertIn("verify_soak_report.py", soak)
        self.assertIn("rc-soak.yml", soak)
        self.assertIn("actions: read", soak)
        attach = source.split("  attach-native-evidence:\n", 1)[1].split("\n  promotion-gate:\n", 1)[0]
        self.assertIn("verify-soak", attach)
        self.assertIn("needs.verify-soak.result", source)

    def test_final_promotion_requires_a_healthy_live_public_tuf_lease(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("verify-public-tuf:", source)
        tuf = source.split("  verify-public-tuf:\n", 1)[1].split("\n  attach-native-evidence:\n", 1)[0]
        self.assertIn("monitor_public_tuf.py", tuf)
        self.assertIn("--warning-hours 6", tuf)
        self.assertIn("maintenance/trust/root.json", tuf)
        attach = source.split("  attach-native-evidence:\n", 1)[1].split("\n  promotion-gate:\n", 1)[0]
        self.assertIn("verify-public-tuf", attach)
        self.assertIn("needs.verify-public-tuf.result", source)

    def test_rc_soak_workflow_is_protected_and_uploads_immutable_report(self):
        path = ROOT / ".github/workflows/rc-soak.yml"
        source = path.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", source)
        self.assertIn("environment: release", source)
        self.assertIn("issues: read", source)
        self.assertIn("ref: ${{ inputs.candidate_commit }}", source)
        self.assertIn("maintenance/tools/build_soak_report.py", source)
        self.assertIn("verify_soak_report.py", source)
        self.assertIn("hardware:", source)
        self.assertIn("observation_evidence_b64:", source)
        builder = (ROOT / "maintenance/tools/build_soak_report.py").read_text(encoding="utf-8")
        self.assertIn("FORMAT = 2", builder)
        self.assertIn('PLATFORM = "macos-arm64"', builder)
        self.assertIn('"evidence": evidence_by_date[date]', builder)
        self.assertIn("actions/upload-artifact@", source)
        self.assertIn("overwrite: false", source)
        self.assertIn("retention-days: 90", source)
        self.assertIn("rc-soak-${{ inputs.candidate_commit }}-${{ github.run_id }}-${{ github.run_attempt }}", source)
        self.assertIn("id: upload", source)
        self.assertIn("steps.upload.outputs.artifact-id", source)
        self.assertIn("soak_artifact_id=", source)

    def test_tuf_operation_handoff_is_protected_and_candidate_bound(self):
        path = ROOT / ".github/workflows/tuf-operation-drill.yml"
        source = path.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", source)
        self.assertIn("environment: release", source)
        self.assertIn("operation_report_b64", source)
        self.assertIn("verify_tuf_operation_report.py", source)
        self.assertIn("verify_external_handoff.py", source)
        self.assertIn("overwrite: false", source)
        self.assertIn("retention-days: 90", source)
        self.assertIn("tuf-operation-${{ inputs.candidate_commit }}-${{ github.run_id }}-${{ github.run_attempt }}", source)

    def test_tuf_operation_handoff_records_registered_artifact_coordinates(self):
        path = ROOT / ".github/workflows/tuf-operation-drill.yml"
        source = path.read_text(encoding="utf-8")
        self.assertIn("id: upload", source)
        self.assertIn("steps.upload.outputs.artifact-id", source)
        self.assertIn("steps.upload.outputs.artifact-digest", source)
        self.assertIn("operation_artifact_id=", source)
        self.assertIn("operation_artifact_digest=", source)

    def test_native_evidence_waits_for_public_acceptance_before_final_receipt(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        block = source.split("  attach-native-evidence:\n", 1)[1]
        block = block.split("  promotion-gate:\n", 1)[0]
        self.assertIn("verify-public-acceptance", block)
        self.assertIn("ACCEPTANCE_RECEIPT_SHA256", block)
        self.assertIn("ACCEPTANCE_BUNDLE_SHA256", block)
        self.assertIn("ACCEPTANCE_CATALOG_SHA256", block)


if __name__ == "__main__":
    unittest.main()
