from __future__ import annotations

import json
import unittest

from maintenance.tools.validate_release_inputs import (
    GROUP_KEYS,
    ReleaseInputError,
    validate_release_inputs,
)


SHA = "a" * 40
DIGEST = "b" * 64


def _handoffs() -> dict[str, str]:
    values = {
        "candidate_handoff": {
            "run_id": "123",
            "artifact_id": "456",
            "artifact_name": f"candidate-{SHA}-1-1",
            "candidate_sha256": DIGEST,
        },
        "native_evidence_handoff": {
            "run_id": "123",
            "artifact_id": "457",
            "artifact_name": f"native-m3-signed-{SHA}-2-1",
        },
        "tuf_metadata_handoff": {
            "run_id": "123",
            "artifact_id": "458",
            "artifact_name": "tuf-metadata-main-1",
            "workflow": ".github/workflows/tuf-metadata-handoff.yml",
        },
        "public_acceptance_handoff": {
            "commit": SHA,
            "run_id": "124",
            "artifact_id": "459",
            "artifact_name": "public-acceptance-1.0.0-rc.1-1",
            "version": "1.0.0-rc.1",
            "receipt_sha256": DIGEST,
            "bundle_sha256": DIGEST,
            "catalog_sha256": DIGEST,
        },
        "tuf_operation_handoff": {
            "run_id": "125",
            "artifact_id": "460",
            "artifact_name": f"tuf-operation-{SHA}-3-1",
            "report_sha256": DIGEST,
            "operator": "owner",
            "custody_host": "m3-local",
            "sla_hours": "6",
        },
        "soak_handoff": {
            "commit": SHA,
            "version": "1.0.0-rc.1",
            "candidate_json_sha256": DIGEST,
            "bundle_sha256": DIGEST,
            "run_id": "126",
            "artifact_id": "461",
            "artifact_name": f"rc-soak-{SHA}-4-1",
            "report_sha256": DIGEST,
            "issue_number": "143",
        },
    }
    return {name: json.dumps(values[name], sort_keys=True) for name in GROUP_KEYS}


class ValidateReleaseInputsTests(unittest.TestCase):
    def test_rehearsal_accepts_empty_optional_handoffs(self):
        result = validate_release_inputs(
            mode="rehearsal",
            release_audience="owner-only",
            candidate_commit=SHA,
            candidate_version="1.0.0",
            public_acceptance_scope="single-user",
            handoffs={name: "{}" for name in GROUP_KEYS},
        )
        self.assertEqual("validated-release-inputs", result["status"])
        self.assertEqual([], result["required_handoffs"])

    def test_owner_only_final_requires_acceptance_but_not_external_operations(self):
        handoffs = _handoffs()
        handoffs["tuf_operation_handoff"] = "{}"
        handoffs["soak_handoff"] = "{}"
        result = validate_release_inputs(
            mode="promote-1.0",
            release_audience="owner-only",
            candidate_commit=SHA,
            candidate_version="1.0.0",
            public_acceptance_scope="single-user",
            handoffs=handoffs,
        )
        self.assertEqual(
            ["candidate_handoff", "native_evidence_handoff", "public_acceptance_handoff", "tuf_metadata_handoff"],
            result["required_handoffs"],
        )

    def test_external_final_requires_soak_and_tuf_operation(self):
        handoffs = _handoffs()
        handoffs["soak_handoff"] = "{}"
        with self.assertRaisesRegex(ReleaseInputError, "soak_handoff incompleto"):
            validate_release_inputs(
                mode="promote-1.0",
                release_audience="external-public",
                candidate_commit=SHA,
                candidate_version="1.0.0",
                public_acceptance_scope="external-users",
                handoffs=handoffs,
            )

    def test_unknown_and_duplicate_handoff_keys_are_rejected(self):
        handoffs = _handoffs()
        handoffs["candidate_handoff"] = json.dumps({"run_id": "1", "unexpected": "x"})
        with self.assertRaisesRegex(ReleaseInputError, "chaves desconhecidas"):
            validate_release_inputs(
                mode="promote-rc",
                release_audience="owner-only",
                candidate_commit=SHA,
                candidate_version="1.0.0-rc.1",
                public_acceptance_scope="single-user",
                handoffs=handoffs,
            )

        handoffs = _handoffs()
        handoffs["candidate_handoff"] = '{"run_id":"1","run_id":"2"}'
        with self.assertRaisesRegex(ReleaseInputError, "chave duplicada"):
            validate_release_inputs(
                mode="promote-rc",
                release_audience="owner-only",
                candidate_commit=SHA,
                candidate_version="1.0.0-rc.1",
                public_acceptance_scope="single-user",
                handoffs=handoffs,
            )


if __name__ == "__main__":
    unittest.main()
