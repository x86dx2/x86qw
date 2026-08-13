from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class EvidenceSigningWorkflowTests(unittest.TestCase):
    def test_protected_workflow_assembles_only_authorized_public_signatures(self):
        workflow = ROOT / ".github/workflows/sign-native-evidence.yml"
        self.assertTrue(workflow.is_file())
        source = workflow.read_text(encoding="utf-8")
        for fragment in (
            "workflow_dispatch:",
            "candidate_artifact_name:",
            "native_input_artifact_id:",
            "native_input_run_id:",
            "signatures_b64:",
            "environment: release",
            "maintenance/tools/verify_external_handoff.py",
            '--artifact-id "$CANDIDATE_ARTIFACT_ID"',
            "--artifact-id \"$NATIVE_ARTIFACT_ID\"",
            "maintenance.tools.assemble_release_evidence assemble",
            "--trust-root",
            "maintenance/trust/m3-root.json",
            "maintenance/tools/attach_release_evidence.py",
            "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
            "overwrite: false",
            "ADR 0007",
            "solo maintainer waiver",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, source)
        self.assertNotIn("external custodian", source.casefold())
        self.assertNotIn("private_key", source)
        self.assertNotIn("M3_PRIVATE", source)
        self.assertNotIn("openssl", source)

    def test_native_provenance_step_exports_the_native_artifact_id(self):
        source = (ROOT / ".github/workflows/sign-native-evidence.yml").read_text(encoding="utf-8")
        step = source.split("      - name: Verify the native run and artifact provenance", 1)[1]
        step = step.split("      - uses: actions/download-artifact@", 1)[0]
        self.assertIn("NATIVE_ARTIFACT_ID: ${{ inputs.native_input_artifact_id }}", step)


if __name__ == "__main__":
    unittest.main()
