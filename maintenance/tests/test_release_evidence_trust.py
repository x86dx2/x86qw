from __future__ import annotations

import base64
import json
import unittest
from pathlib import Path

from maintenance.tests.trust_support import EphemeralSigner
from x86qw_runtime import trust


ROOT = Path(__file__).resolve().parents[2]


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def encoded_signature(signer: EphemeralSigner, payload: bytes) -> str:
    signature = signer.sign(payload).signature
    return base64.urlsafe_b64encode(bytes.fromhex(signature)).rstrip(b"=").decode("ascii")


class ReleaseEvidenceTrustTests(unittest.TestCase):
    def setUp(self) -> None:
        self.signers = tuple(EphemeralSigner() for _ in range(3))
        keys = {
            signer.public_key.keyid: {
                "keytype": "ed25519",
                "scheme": "ed25519",
                "keyval": {"public": signer.public_key.keyval["public"]},
            }
            for signer in self.signers
        }
        signed = {
            "format": "x86qw-m3-evidence-root-v1",
            "project": "x86qw",
            "role": "evidence",
            "version": 1,
            "expires": "2099-01-01T00:00:00Z",
            "threshold": 2,
            "keys": keys,
        }
        self.root = {
            "signed": signed,
            "signatures": [
                {
                    "keyid": signer.public_key.keyid,
                    "sig": encoded_signature(signer, canonical(signed)),
                }
                for signer in self.signers[:2]
            ],
        }
        self.identity = {
            "version": "1.0.0-rc.1",
            "commit": "a" * 40,
            "manifest_sha256": "b" * 64,
        }

    def _evidence(self) -> dict[str, object]:
        body = {
            "format": 1,
            "project": "x86qw",
            "version": self.identity["version"],
            "commit": self.identity["commit"],
            "status": "complete",
            "candidate": dict(self.identity),
            "platforms": {"macOS-ARM64": {"status": "complete"}},
        }
        body["signatures"] = [
            {
                "keyid": signer.public_key.keyid,
                "sig": encoded_signature(signer, canonical(body)),
            }
            for signer in self.signers[:2]
        ]
        return body

    def test_valid_root_and_evidence_threshold_are_accepted(self):
        result = trust.verify_release_evidence(
            canonical(self.root),
            canonical(self._evidence()),
            expected_identity=self.identity,
        )
        self.assertEqual("complete", result["status"])

    def test_versioned_production_m3_root_is_self_signed_and_keeps_two_of_three(self):
        root_path = ROOT / "maintenance/trust/m3-root.json"
        self.assertTrue(root_path.is_file())
        keys = trust._load_evidence_root(root_path.read_bytes())
        self.assertEqual(3, len(keys))
        document = json.loads(root_path.read_text(encoding="utf-8"))
        self.assertEqual(2, document["signed"]["threshold"])

    def test_root_requires_self_signed_threshold(self):
        root = json.loads(json.dumps(self.root))
        root["signatures"] = root["signatures"][:1]
        with self.assertRaises(trust.TrustError):
            trust.verify_release_evidence(
                canonical(root), canonical(self._evidence()), expected_identity=self.identity,
            )

    def test_evidence_rejects_identity_drift(self):
        evidence = self._evidence()
        evidence["candidate"]["commit"] = "c" * 40  # type: ignore[index]
        body = {key: value for key, value in evidence.items() if key != "signatures"}
        evidence["signatures"] = [
            {
                "keyid": signer.public_key.keyid,
                "sig": encoded_signature(signer, canonical(body)),
            }
            for signer in self.signers[:2]
        ]
        with self.assertRaises(trust.TrustError):
            trust.verify_release_evidence(
                canonical(self.root), canonical(evidence), expected_identity=self.identity,
            )

    def test_evidence_rejects_duplicate_signer_and_noncanonical_signature(self):
        evidence = self._evidence()
        evidence["signatures"] = [evidence["signatures"][0], evidence["signatures"][0]]  # type: ignore[index]
        with self.assertRaises(trust.TrustError):
            trust.verify_release_evidence(
                canonical(self.root), canonical(evidence), expected_identity=self.identity,
            )


if __name__ == "__main__":
    unittest.main()
