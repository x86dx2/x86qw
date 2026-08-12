from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class NativeWorkflowTests(unittest.TestCase):
    def test_m3_workflow_runs_the_exact_candidate_without_personal_installation(self):
        workflow = ROOT / ".github/workflows/native-m3.yml"
        self.assertTrue(workflow.is_file())
        source = workflow.read_text(encoding="utf-8")

        for fragment in (
            "workflow_dispatch:",
            "candidate_artifact_id:",
            "candidate_run_id:",
            "candidate_commit:",
            "candidate_sha256:",
            "candidate_artifact_name:",
            "runs-on: [self-hosted, macOS, arm64, M3]",
            "artifact-ids: ${{ inputs.candidate_artifact_id }}",
            "run-id: ${{ inputs.candidate_run_id }}",
            "github-token: ${{ github.token }}",
            "maintenance/tools/verify_external_handoff.py",
            '--artifact-id "$EXPECTED_ARTIFACT_ID"',
            "maintenance.tools.native_plan_adapter",
            "maintenance.tools.native_macos_harness run",
            "maintenance/tools/native_release_smoke.py",
            "maintenance/tools/native_release_evidence.py",
            "maintenance.tools.native_handoff_evidence aggregate",
            "maintenance.tools.assemble_release_evidence prepare",
            "records/macOS-ARM64.json",
            "release-evidence-body.json",
            "native-evidence-pending",
            "native-evidence-input",
            "retention-days: 90",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, source)

        self.assertNotIn("quake-world", source)
        self.assertNotIn("maintenance.tools.native_m3_harness run", source)
        self.assertNotIn("path: ${{ runner.temp }}/native-m3\n", source)

    def test_m3_workflow_uses_pinned_actions_and_keeps_pending_evidence_unsigned(self):
        source = (ROOT / ".github/workflows/native-m3.yml").read_text(encoding="utf-8")
        for action in (
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093",
            "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
        ):
            self.assertIn(action, source)
        self.assertNotIn("actions/setup-python@", source)
        self.assertIn("signed: false", source)
        self.assertIn("promotable: false", source)
        self.assertNotIn("M3_TRUST_ROOT_B64", source)
        self.assertNotIn("path: ${{ runner.temp }}/native-m3\n", source)

    def test_m3_workflow_uses_runner_owned_python_without_fixed_home(self):
        source = (ROOT / ".github/workflows/native-m3.yml").read_text(encoding="utf-8")
        for fragment in (
            "name: Prepare isolated Python on self-hosted M3",
            'python3_bin="$(command -v python3)"',
            '"$python3_bin" -m venv "$RUNNER_TEMP/x86qw-python"',
            "sys.version_info >= (3, 13)",
            'echo "$RUNNER_TEMP/x86qw-python/bin" >> "$GITHUB_PATH"',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, source)
        self.assertNotIn("AGENT_TOOLSDIRECTORY", source)

    def test_m3_workflow_removes_large_candidate_workspace_after_handoff(self):
        source = (ROOT / ".github/workflows/native-m3.yml").read_text(encoding="utf-8")
        self.assertIn("name: Remove native M3 temporary workspace", source)
        self.assertIn("if: always()", source)
        for path in (
            '"$GITHUB_WORKSPACE/candidate"',
            '"$RUNNER_TEMP/x86qw-python"',
            '"$RUNNER_TEMP/candidate-artifact.json"',
            '"$RUNNER_TEMP/native-plan.json"',
            '"$RUNNER_TEMP/native-m3"',
            '"$RUNNER_TEMP/native-evidence-input"',
        ):
            with self.subTest(path=path):
                self.assertIn(path, source)


if __name__ == "__main__":
    unittest.main()
