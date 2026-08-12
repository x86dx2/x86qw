from __future__ import annotations

import unittest
import tempfile
import json
from pathlib import Path
from unittest import mock

from maintenance.tools import verify_external_handoff
from maintenance.tools.release_candidate import CandidateError


NATIVE_WORKFLOW = ".github/workflows/native-m3.yml"


class ExternalHandoffTests(unittest.TestCase):
    def test_run_and_artifacts_are_bound_to_repository_and_commit(self):
        responses = [
            {
                "status": "completed",
                "conclusion": "success",
                "head_sha": "a" * 40,
                "repository": {"full_name": "x86dx2/x86qw"},
                "head_repository": {"full_name": "x86dx2/x86qw"},
                "path": NATIVE_WORKFLOW,
                "event": "workflow_dispatch",
                "head_branch": "main",
            },
            {"artifacts": [{
                "name": "handoff-Linux-X64",
                "id": 1001,
                "digest": "sha256:" + "a" * 64,
                "created_at": "2026-08-04T12:00:00Z",
                "updated_at": "2026-08-04T12:00:00Z",
                "expired": False,
            }]},
        ]
        with mock.patch.object(verify_external_handoff, "_get_json", side_effect=responses):
            result = verify_external_handoff.verify_external_run(
                repository="x86dx2/x86qw",
                run_id="123",
                commit="a" * 40,
                artifacts=("handoff-Linux-X64",),
                token="token",
                workflow=NATIVE_WORKFLOW,
                head_branch="main",
            )
        self.assertEqual("success", result["conclusion"])

    def test_wrong_commit_or_missing_artifact_fails_closed(self):
        with mock.patch.object(
            verify_external_handoff,
            "_get_json",
            return_value={
                "status": "completed", "conclusion": "success", "head_sha": "b" * 40,
                "repository": {"full_name": "x86dx2/x86qw"},
                "head_repository": {"full_name": "x86dx2/x86qw"},
                "path": NATIVE_WORKFLOW,
                "event": "workflow_dispatch",
                "head_branch": "main",
            },
        ):
            with self.assertRaises(CandidateError):
                verify_external_handoff.verify_external_run(
                    repository="x86dx2/x86qw",
                    run_id="123",
                    commit="a" * 40,
                    artifacts=("handoff-Linux-X64",),
                    token="token",
                    workflow=NATIVE_WORKFLOW,
                    head_branch="main",
                )

    def test_expected_artifact_id_must_match_the_verified_artifact_name(self):
        responses = [
            {
                "status": "completed", "conclusion": "success", "head_sha": "a" * 40,
                "repository": {"full_name": "x86dx2/x86qw"},
                "head_repository": {"full_name": "x86dx2/x86qw"},
                "path": NATIVE_WORKFLOW,
                "event": "workflow_dispatch", "head_branch": "main",
            },
            {"artifacts": [{
                "name": "handoff-Linux-X64", "id": 1001,
                "digest": "sha256:" + "a" * 64,
                "created_at": "2026-08-04T12:00:00Z",
                "updated_at": "2026-08-04T12:00:00Z", "expired": False,
            }]},
        ]
        with mock.patch.object(verify_external_handoff, "_get_json", side_effect=responses):
            with self.assertRaisesRegex(CandidateError, "ID do artefato externo diverge"):
                verify_external_handoff.verify_external_run(
                    repository="x86dx2/x86qw",
                    run_id="123",
                    commit="a" * 40,
                    artifacts=("handoff-Linux-X64",),
                    artifact_ids={"handoff-Linux-X64": 1002},
                    token="token",
                    workflow=NATIVE_WORKFLOW,
                    head_branch="main",
                )

    def test_artifact_names_are_path_safe_before_any_api_request(self):
        with self.assertRaisesRegex(CandidateError, "nome do artefato externo inválido"):
            verify_external_handoff.verify_external_run(
                repository="x86dx2/x86qw",
                run_id="123",
                commit="a" * 40,
                artifacts=("../handoff",),
                token="token",
                workflow=NATIVE_WORKFLOW,
                head_branch="main",
            )

    def test_cli_can_persist_exact_verified_artifact_identity(self):
        responses = [
            {
                "status": "completed", "conclusion": "success", "head_sha": "a" * 40,
                "repository": {"full_name": "x86dx2/x86qw"},
                "head_repository": {"full_name": "x86dx2/x86qw"},
                "path": NATIVE_WORKFLOW,
                "event": "workflow_dispatch", "head_branch": "main",
            },
            {"artifacts": [{
                "name": "handoff-Linux-X64", "id": 1001,
                "digest": "sha256:" + "a" * 64,
                "created_at": "2026-08-04T12:00:00Z",
                "updated_at": "2026-08-04T12:00:00Z", "expired": False,
            }]},
        ]
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            verify_external_handoff, "_get_json", side_effect=responses,
        ), mock.patch.dict(
            verify_external_handoff.os.environ,
            {"GITHUB_TOKEN": "token", "GITHUB_REPOSITORY": "x86dx2/x86qw"},
            clear=False,
        ):
            output = Path(temporary) / "verified.json"
            self.assertEqual(0, verify_external_handoff.main([
                "--run-id", "123", "--commit", "a" * 40,
                "--artifact", "handoff-Linux-X64",
                "--workflow", NATIVE_WORKFLOW,
                "--head-branch", "main", "--output", str(output),
            ]))
            self.assertEqual(1001, json.loads(output.read_text())["handoff-Linux-X64"]["id"])

        responses = [
            {
                "status": "completed", "conclusion": "success", "head_sha": "a" * 40,
                "repository": {"full_name": "x86dx2/x86qw"},
                "head_repository": {"full_name": "x86dx2/x86qw"},
                "path": NATIVE_WORKFLOW,
                "event": "workflow_dispatch",
                "head_branch": "main",
            },
            {"artifacts": []},
        ]
        with mock.patch.object(verify_external_handoff, "_get_json", side_effect=responses):
            with self.assertRaises(CandidateError):
                verify_external_handoff.verify_external_run(
                    repository="x86dx2/x86qw",
                    run_id="123",
                    commit="a" * 40,
                    artifacts=("handoff-Linux-X64",),
                    token="token",
                    workflow=NATIVE_WORKFLOW,
                    head_branch="main",
                )


if __name__ == "__main__":
    unittest.main()
