from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import x86qw_runtime.trust as trust
from x86qw_runtime.trust import (
    DigestError,
    ExpiryError,
    FreezeError,
    FormatError,
    RollbackError,
    SignatureError,
    ThresholdError,
    TrustError,
    TrustedVersions,
    canonical_json_bytes,
    deserialize_trusted_versions,
    key_id,
    parse_json_bytes,
    serialize_trusted_versions,
    verify_release_metadata,
    verify_release_evidence,
)


ROOT = Path(__file__).resolve().parents[2]
TRUST_FIXTURES = ROOT / "maintenance/tests/fixtures/trust"


def fixture(name: str) -> bytes:
    if name == "catalog.json":
        return (ROOT / "site/public/api/v1/catalog.json").read_bytes()
    return (TRUST_FIXTURES / name).read_bytes()


def envelope(name: str) -> dict[str, object]:
    return parse_json_bytes(fixture(name))


def _ephemeral_rsa_key() -> tuple[Path, dict[str, object], tempfile.TemporaryDirectory[str]]:
    if shutil.which("openssl") is None:
        raise unittest.SkipTest("openssl não disponível para vetor criptográfico positivo")
    directory = tempfile.TemporaryDirectory(prefix="x86qw-trust-")
    private_key = Path(directory.name) / "key.pem"
    try:
        subprocess.run(
            [
                "openssl", "genpkey", "-algorithm", "RSA",
                "-pkeyopt", "rsa_keygen_bits:3072", "-out", str(private_key),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        text = subprocess.run(
            ["openssl", "rsa", "-in", str(private_key), "-pubout", "-text", "-noout"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        directory.cleanup()
        raise unittest.SkipTest(f"openssl não conseguiu gerar vetor RSA: {error}")
    match = re.search(r"modulus:\s*(.*?)\npublicExponent:", text, flags=re.S)
    if match is None:
        directory.cleanup()
        raise unittest.SkipTest("saída do openssl sem modulus RSA legível")
    modulus = re.sub(r"[^0-9a-f]", "", match.group(1).lower())
    public_key = {
        "keytype": "rsa",
        "scheme": "rsassa-pss-sha256",
        "keyval": {"public": {"e": 65537, "n": modulus.lstrip("0")}},
    }
    return private_key, public_key, directory


def _openssl_sign(private_key: Path, payload: bytes) -> str:
    with tempfile.TemporaryDirectory(prefix="x86qw-sign-") as directory:
        source = Path(directory) / "body.json"
        signature = Path(directory) / "body.sig"
        source.write_bytes(payload)
        subprocess.run(
            [
                "openssl", "dgst", "-sha256", "-sign", str(private_key),
                "-sigopt", "rsa_padding_mode:pss",
                "-sigopt", "rsa_pss_saltlen:32",
                "-sigopt", "rsa_mgf1_md:sha256",
                "-out", str(signature), str(source),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return base64.urlsafe_b64encode(signature.read_bytes()).decode("ascii").rstrip("=")


def _signed_evidence_fixture(
    private_key: Path,
    public_key: dict[str, object],
    *,
    evidence_signers: list[tuple[Path, dict[str, object]]] | None = None,
    signature_field: str = "signature",
) -> tuple[bytes, bytes, dict[str, str]]:
    evidence_signers = evidence_signers or [(private_key, public_key)]
    identifier = key_id(public_key)
    evidence_keyids = [key_id(public) for _, public in evidence_signers]
    roles = {
        label: {"keyids": [identifier], "threshold": 1}
        for label in ("root", "current", "snapshot", "evidence")
    }
    roles["evidence"] = {
        "keyids": evidence_keyids,
        "threshold": len(evidence_keyids),
    }
    keys = {key_id(public): public for _, public in evidence_signers}
    keys[identifier] = public_key
    root_signed = {
        "_type": "root",
        "consistent_snapshot": True,
        "expires": "2027-08-05T00:00:00Z",
        "keys": keys,
        "roles": roles,
        "spec_version": "1.0",
        "version": 1,
    }
    root = {
        "signed": root_signed,
        "signatures": [{
            "keyid": identifier,
            "sig": _openssl_sign(private_key, canonical_json_bytes(root_signed)),
        }],
    }
    identity = {
        "version": "1.0.0-rc.1",
        "commit": "a" * 40,
        "manifest_sha256": "b" * 64,
    }
    body = {
        "format": 1,
        "project": "x86qw",
        "version": identity["version"],
        "commit": identity["commit"],
        "status": "complete",
        "candidate": identity,
        "platforms": {
            "macos-arm64": {
                "status": "complete",
                "recorded_at": "2026-08-04T12:00:00Z",
            },
        },
    }
    signatures = [
        {
            "keyid": key_id(public),
            "sig": _openssl_sign(private, canonical_json_bytes(body)),
        }
        for private, public in evidence_signers
    ]
    evidence = {
        **body,
        signature_field: signatures if signature_field == "signatures" else signatures[0],
    }
    return canonical_json_bytes(root), canonical_json_bytes(evidence), identity


def _trusted_catalog_fixture(*, current_digest: str | None = None) -> TrustedVersions:
    """Build a complete persisted checkpoint for the public 0.7.3 chain."""

    root = envelope("root.json")
    current = envelope("current.json")
    snapshot = envelope("snapshot.json")
    return TrustedVersions(
        root=1,
        snapshot=1,
        current=1,
        root_digest=hashlib.sha256(canonical_json_bytes(root["signed"])).hexdigest(),
        snapshot_digest=hashlib.sha256(canonical_json_bytes(snapshot["signed"])).hexdigest(),
        current_digest=(
            current_digest
            or hashlib.sha256(canonical_json_bytes(current["signed"])).hexdigest()
        ),
        root_metadata=fixture("root.json"),
    )


class TrustMetadataTests(unittest.TestCase):
    def test_release_evidence_positive_signature_is_verified_cryptographically(self) -> None:
        private_key, public_key, lifetime = _ephemeral_rsa_key()
        self.addCleanup(lifetime.cleanup)
        root, evidence, identity = _signed_evidence_fixture(private_key, public_key)
        with mock.patch.object(trust, "ROOT_KEY_ID", key_id(public_key)), mock.patch.object(
            trust, "ROOT_PUBLIC_KEY", public_key,
        ):
            result = verify_release_evidence(
                root,
                evidence,
                expected_identity=identity,
                now=datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
            )
        self.assertEqual(identity, result["candidate"])
        self.assertEqual(identity["version"], result.trusted.evidence_version)
        self.assertEqual(result.evidence_digest, result.trusted.evidence_digest)
        self.assertEqual(1, result.trusted.root)
        self.assertIsNotNone(result.trusted.root_metadata)
        with self.assertRaises(TypeError):
            result.document["status"] = "failed"  # type: ignore[index]
        round_trip = deserialize_trusted_versions(serialize_trusted_versions(result.trusted))
        self.assertEqual(result.trusted.evidence_tuple(), round_trip.evidence_tuple())
        self.assertEqual(result.trusted, round_trip)

    def test_release_evidence_threshold_accepts_two_distinct_signers(self) -> None:
        private_key, public_key, lifetime = _ephemeral_rsa_key()
        self.addCleanup(lifetime.cleanup)
        second_private, second_public, second_lifetime = _ephemeral_rsa_key()
        self.addCleanup(second_lifetime.cleanup)
        root, evidence, identity = _signed_evidence_fixture(
            private_key,
            public_key,
            evidence_signers=[(private_key, public_key), (second_private, second_public)],
            signature_field="signatures",
        )
        with mock.patch.object(trust, "ROOT_KEY_ID", key_id(public_key)), mock.patch.object(
            trust, "ROOT_PUBLIC_KEY", public_key,
        ):
            result = verify_release_evidence(
                root,
                evidence,
                expected_identity=identity,
                now=datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
            )
        self.assertEqual(identity, result["candidate"])
        self.assertEqual(identity["version"], result.trusted.evidence_version)

    def test_release_evidence_threshold_rejects_singular_and_duplicate_signatures(self) -> None:
        private_key, public_key, lifetime = _ephemeral_rsa_key()
        self.addCleanup(lifetime.cleanup)
        second_private, second_public, second_lifetime = _ephemeral_rsa_key()
        self.addCleanup(second_lifetime.cleanup)
        root, singular, identity = _signed_evidence_fixture(
            private_key,
            public_key,
            evidence_signers=[(private_key, public_key), (second_private, second_public)],
        )
        with mock.patch.object(trust, "ROOT_KEY_ID", key_id(public_key)), mock.patch.object(
            trust, "ROOT_PUBLIC_KEY", public_key,
        ), self.assertRaises(ThresholdError):
            verify_release_evidence(
                root,
                singular,
                expected_identity=identity,
                now=datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
            )

        root, evidence, identity = _signed_evidence_fixture(
            private_key,
            public_key,
            evidence_signers=[(private_key, public_key), (second_private, second_public)],
            signature_field="signatures",
        )
        parsed = parse_json_bytes(evidence)
        parsed["signatures"].append(dict(parsed["signatures"][0]))
        with mock.patch.object(trust, "ROOT_KEY_ID", key_id(public_key)), mock.patch.object(
            trust, "ROOT_PUBLIC_KEY", public_key,
        ), self.assertRaises(SignatureError):
            verify_release_evidence(
                root,
                canonical_json_bytes(parsed),
                expected_identity=identity,
                now=datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
            )

    def test_evidence_checkpoint_supports_contiguous_root_rotation(self) -> None:
        old_private, old_public, old_lifetime = _ephemeral_rsa_key()
        self.addCleanup(old_lifetime.cleanup)
        new_private, new_public, new_lifetime = _ephemeral_rsa_key()
        self.addCleanup(new_lifetime.cleanup)
        root, evidence, identity = _signed_evidence_fixture(old_private, old_public)
        old_id = key_id(old_public)
        new_id = key_id(new_public)
        rotated_signed = {
            "_type": "root",
            "consistent_snapshot": True,
            "expires": "2027-08-06T00:00:00Z",
            "keys": {old_id: old_public, new_id: new_public},
            "roles": {
                "current": {"keyids": [new_id], "threshold": 1},
                "evidence": {"keyids": [new_id], "threshold": 1},
                "root": {"keyids": [old_id, new_id], "threshold": 1},
                "snapshot": {"keyids": [new_id], "threshold": 1},
            },
            "spec_version": "1.0",
            "version": 2,
        }
        rotated_root = canonical_json_bytes({
            "signed": rotated_signed,
            "signatures": [
                {"keyid": old_id, "sig": _openssl_sign(old_private, canonical_json_bytes(rotated_signed))},
                {"keyid": new_id, "sig": _openssl_sign(new_private, canonical_json_bytes(rotated_signed))},
            ],
        })
        parsed_evidence = parse_json_bytes(evidence)
        body = dict(parsed_evidence)
        body.pop("signature")
        parsed_evidence["signature"] = {
            "keyid": new_id,
            "sig": _openssl_sign(new_private, canonical_json_bytes(body)),
        }
        rotated_evidence = canonical_json_bytes(parsed_evidence)
        with mock.patch.object(trust, "ROOT_KEY_ID", old_id), mock.patch.object(
            trust, "ROOT_PUBLIC_KEY", old_public,
        ):
            first = verify_release_evidence(
                root,
                evidence,
                expected_identity=identity,
                now=datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
            )
            second = verify_release_evidence(
                rotated_root,
                rotated_evidence,
                expected_identity=identity,
                trusted=first.trusted,
                now=datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
            )
        self.assertEqual(2, second.trusted.root)
        self.assertEqual(2, parse_json_bytes(serialize_trusted_versions(second.trusted))["root"])
        self.assertEqual(first.trusted.evidence_tuple(), second.trusted.evidence_tuple())

    def test_initial_root_requires_a_valid_signature_from_the_pinned_key(self) -> None:
        private_key, public_key, lifetime = _ephemeral_rsa_key()
        self.addCleanup(lifetime.cleanup)
        other_private, other_public, other_lifetime = _ephemeral_rsa_key()
        self.addCleanup(other_lifetime.cleanup)
        root, evidence, identity = _signed_evidence_fixture(private_key, public_key)
        parsed_root = parse_json_bytes(root)
        other_id = key_id(other_public)
        root_signed = parsed_root["signed"]
        root_signed["keys"][other_id] = other_public
        root_signed["roles"]["root"] = {
            "keyids": [key_id(public_key), other_id],
            "threshold": 1,
        }
        parsed_root["signatures"] = [{
            "keyid": other_id,
            "sig": _openssl_sign(other_private, canonical_json_bytes(root_signed)),
        }]
        with mock.patch.object(trust, "ROOT_KEY_ID", key_id(public_key)), mock.patch.object(
            trust, "ROOT_PUBLIC_KEY", public_key,
        ), self.assertRaises(ThresholdError):
            verify_release_evidence(
                canonical_json_bytes(parsed_root),
                evidence,
                expected_identity=identity,
                now=datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
            )

    def test_release_evidence_requires_and_closes_candidate_identity(self) -> None:
        private_key, public_key, lifetime = _ephemeral_rsa_key()
        self.addCleanup(lifetime.cleanup)
        root, evidence, identity = _signed_evidence_fixture(private_key, public_key)
        with self.assertRaises(TypeError):
            verify_release_evidence(
                root,
                evidence,
                now=datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
            )

        parsed = parse_json_bytes(evidence)
        parsed["candidate"] = dict(parsed["candidate"])
        parsed["candidate"]["commit"] = "c" * 40
        with mock.patch.object(trust, "ROOT_KEY_ID", key_id(public_key)), mock.patch.object(
            trust, "ROOT_PUBLIC_KEY", public_key,
        ), self.assertRaises(DigestError):
            verify_release_evidence(
                root,
                canonical_json_bytes(parsed),
                expected_identity={**identity, "commit": "c" * 40},
                now=datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
            )

        parsed = parse_json_bytes(evidence)
        parsed["candidate"] = dict(parsed["candidate"])
        parsed["candidate"]["manifest_sha256"] = "not-a-digest"
        with mock.patch.object(trust, "ROOT_KEY_ID", key_id(public_key)), mock.patch.object(
            trust, "ROOT_PUBLIC_KEY", public_key,
        ), self.assertRaises(FormatError):
            verify_release_evidence(
                root,
                canonical_json_bytes(parsed),
                expected_identity=identity,
                now=datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
            )

    def test_release_evidence_rejects_unredacted_secret_fields_in_platform_report(self) -> None:
        private_key, public_key, lifetime = _ephemeral_rsa_key()
        self.addCleanup(lifetime.cleanup)
        root, evidence, identity = _signed_evidence_fixture(private_key, public_key)

        def resign(report_update: dict[str, object]) -> bytes:
            parsed = parse_json_bytes(evidence)
            platforms = dict(parsed["platforms"])
            report = dict(platforms["macos-arm64"])
            report.update(report_update)
            platforms["macos-arm64"] = report
            parsed["platforms"] = platforms
            body = dict(parsed)
            signature = dict(body.pop("signature"))
            signature["sig"] = _openssl_sign(private_key, canonical_json_bytes(body))
            parsed["signature"] = signature
            return canonical_json_bytes(parsed)

        with mock.patch.object(trust, "ROOT_KEY_ID", key_id(public_key)), mock.patch.object(
            trust, "ROOT_PUBLIC_KEY", public_key,
        ):
            for update in (
                {"secrets": "present"},
                {"private_key": "-----BEGIN PRIVATE KEY-----"},
            ):
                with self.subTest(update=update), self.assertRaises(FormatError):
                    verify_release_evidence(
                        root,
                        resign(update),
                        expected_identity=identity,
                        now=datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
                    )

    def test_release_evidence_rejects_rollback_and_same_version_equivocation(self) -> None:
        private_key, public_key, lifetime = _ephemeral_rsa_key()
        self.addCleanup(lifetime.cleanup)
        root, evidence, identity = _signed_evidence_fixture(private_key, public_key)
        digest = hashlib.sha256(
            canonical_json_bytes({key: value for key, value in parse_json_bytes(evidence).items() if key != "signature"})
        ).hexdigest()
        with mock.patch.object(trust, "ROOT_KEY_ID", key_id(public_key)), mock.patch.object(
            trust, "ROOT_PUBLIC_KEY", public_key,
        ):
            with self.assertRaises(RollbackError):
                verify_release_evidence(
                    root,
                    evidence,
                    expected_identity=identity,
                    trusted=TrustedVersions(
                        evidence_version="2.0.0",
                        evidence_digest="0" * 64,
                    ),
                    now=datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
                )
            with self.assertRaises(RollbackError):
                verify_release_evidence(
                    root,
                    evidence,
                    expected_identity=identity,
                    trusted=TrustedVersions(
                        evidence_version=identity["version"],
                        evidence_digest="0" * 64,
                    ),
                    now=datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
                )
            accepted = verify_release_evidence(
                root,
                evidence,
                expected_identity=identity,
                trusted=TrustedVersions(
                    evidence_version=identity["version"],
                    evidence_digest=digest,
                ),
                now=datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
            )
        self.assertEqual(identity, accepted["candidate"])

        # SemVer precedence must permit the normal prerelease -> stable
        # transition while retaining the old generation for rollback checks.
        parsed = parse_json_bytes(evidence)
        parsed["version"] = "1.0.0"
        stable_identity = dict(identity)
        stable_identity["version"] = "1.0.0"
        parsed["candidate"] = stable_identity
        stable_body = dict(parsed)
        stable_body.pop("signature")
        parsed["signature"] = {
            "keyid": key_id(public_key),
            "sig": _openssl_sign(private_key, canonical_json_bytes(stable_body)),
        }
        with mock.patch.object(trust, "ROOT_KEY_ID", key_id(public_key)), mock.patch.object(
            trust, "ROOT_PUBLIC_KEY", public_key,
        ):
            stable = verify_release_evidence(
                root,
                canonical_json_bytes(parsed),
                expected_identity=stable_identity,
                trusted=TrustedVersions(
                    evidence_version=identity["version"],
                    evidence_digest=digest,
                ),
                now=datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
            )
        self.assertEqual("1.0.0", stable.trusted.evidence_version)

    def test_trusted_state_format_two_round_trips_and_imports_legacy_format_one(self) -> None:
        with self.assertRaises(ValueError):
            TrustedVersions(evidence_version="1.0.0-01", evidence_digest="b" * 64)
        state = TrustedVersions(
            evidence_version="1.0.0-rc.1",
            evidence_digest="b" * 64,
        )
        encoded = serialize_trusted_versions(state)
        parsed = parse_json_bytes(encoded)
        self.assertEqual(2, parsed["format"])
        self.assertEqual(state, deserialize_trusted_versions(encoded))
        self.assertEqual(
            state,
            deserialize_trusted_versions(fixture("trusted-state-v2-evidence.json")),
        )
        legacy = dict(parsed)
        legacy["format"] = 1
        legacy.pop("evidence_version")
        legacy.pop("evidence_digest")
        imported = deserialize_trusted_versions(canonical_json_bytes(legacy))
        self.assertIsNone(imported.evidence_version)
        self.assertIsNone(imported.evidence_digest)
        self.assertEqual(
            TrustedVersions(),
            deserialize_trusted_versions(fixture("trusted-state-v1.json")),
        )
        with self.assertRaises(FormatError):
            deserialize_trusted_versions(fixture("trusted-state-v2-incomplete.json"))

    def test_catalog_verification_preserves_trusted_evidence_generation(self) -> None:
        trusted = TrustedVersions(
            evidence_version="0.7.3",
            evidence_digest="b" * 64,
        )
        result = verify_release_metadata(
            fixture("root.json"),
            fixture("current.json"),
            fixture("snapshot.json"),
            fixture("catalog.json"),
            trusted=trusted,
            now=datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(trusted.evidence_tuple(), result.versions.evidence_tuple())
        self.assertEqual(trusted.evidence_tuple(), deserialize_trusted_versions(
            serialize_trusted_versions(result.versions),
        ).evidence_tuple())

    def test_complete_catalog_checkpoint_round_trips_root_and_role_digests(self) -> None:
        trusted = _trusted_catalog_fixture()
        restored = deserialize_trusted_versions(serialize_trusted_versions(trusted))
        self.assertEqual(trusted, restored)

    def test_release_evidence_rejects_incomplete_persisted_root_state(self) -> None:
        # A truncated persisted root cannot be represented as a trusted
        # checkpoint at all; rejecting it at construction prevents callers
        # from accidentally using an incomplete state in either verifier.
        with self.assertRaises(ValueError):
            TrustedVersions(root=1)
        with self.assertRaises(ValueError):
            TrustedVersions(root=0, snapshot=1, snapshot_digest="a" * 64)

    def test_release_evidence_signature_is_verified_against_distinct_evidence_role(self) -> None:
        identity = {
            "version": "1.0.0-rc.1",
            "commit": "a" * 40,
            "manifest_sha256": "b" * 64,
        }
        evidence = {
            "format": 1,
            "project": "x86qw",
            "version": identity["version"],
            "commit": identity["commit"],
            "status": "complete",
            "candidate": identity,
            "platforms": {"macos-arm64": {"recorded_at": "2026-08-04T12:00:00Z"}},
            "signature": {"keyid": "e7d419d9b6e7da0b813f717bab2ad9094b389e7bf29a452d1cacdb0624c7acdd", "sig": "a"},
        }
        with self.assertRaises(SignatureError):
            verify_release_evidence(
                fixture("root.json"),
                canonical_json_bytes(evidence),
                expected_identity=identity,
                now=datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
            )

    def test_release_evidence_without_signature_is_rejected(self) -> None:
        identity = {
            "version": "1.0.0-rc.1",
            "commit": "a" * 40,
            "manifest_sha256": "b" * 64,
        }
        evidence = {
            "format": 1,
            "project": "x86qw",
            "version": identity["version"],
            "commit": identity["commit"],
            "status": "complete",
            "candidate": identity,
            "platforms": {"macos-arm64": {"recorded_at": "2026-08-04T12:00:00Z"}},
            "signature": None,
        }
        with self.assertRaises(SignatureError):
            verify_release_evidence(
                fixture("root.json"),
                canonical_json_bytes(evidence),
                expected_identity=identity,
                now=datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
            )

    def test_release_evidence_rejects_stale_and_future_platform_timestamps(self) -> None:
        identity = {
            "version": "1.0.0-rc.1",
            "commit": "a" * 40,
            "manifest_sha256": "b" * 64,
        }
        for recorded_at in ("2026-07-20T12:00:00Z", "2026-08-04T12:10:01Z"):
            evidence = {
                "format": 1,
                "project": "x86qw",
                "version": identity["version"],
                "commit": identity["commit"],
                "status": "complete",
                "candidate": identity,
                "platforms": {"macos-arm64": {"recorded_at": recorded_at}},
                "signature": {
                    "keyid": "e7d419d9b6e7da0b813f717bab2ad9094b389e7bf29a452d1cacdb0624c7acdd",
                    "sig": "a",
                },
            }
            with self.subTest(recorded_at=recorded_at), self.assertRaises(ExpiryError):
                verify_release_evidence(
                    fixture("root.json"),
                    canonical_json_bytes(evidence),
                    expected_identity=identity,
                    now=datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
                )

    def test_release_evidence_freshness_boundary_clock_and_timestamp_shape(self) -> None:
        private_key, public_key, lifetime = _ephemeral_rsa_key()
        self.addCleanup(lifetime.cleanup)
        root, evidence, identity = _signed_evidence_fixture(private_key, public_key)

        def resign(recorded_at: str) -> bytes:
            parsed = parse_json_bytes(evidence)
            platforms = dict(parsed["platforms"])
            report = dict(platforms["macos-arm64"])
            report["recorded_at"] = recorded_at
            platforms["macos-arm64"] = report
            parsed["platforms"] = platforms
            body = dict(parsed)
            signature = dict(body.pop("signature"))
            signature["sig"] = _openssl_sign(private_key, canonical_json_bytes(body))
            parsed["signature"] = signature
            return canonical_json_bytes(parsed)

        now = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)
        with mock.patch.object(trust, "ROOT_KEY_ID", key_id(public_key)), mock.patch.object(
            trust, "ROOT_PUBLIC_KEY", public_key,
        ):
            with self.assertRaises(ExpiryError):
                verify_release_evidence(
                    root,
                    resign("2026-07-28T12:00:00Z"),
                    expected_identity=identity,
                    now=now,
                )
            accepted = verify_release_evidence(
                root,
                resign("2026-07-28T12:00:01Z"),
                expected_identity=identity,
                now=now,
            )
            self.assertEqual(identity, accepted["candidate"])
            with self.assertRaises(ExpiryError):
                verify_release_evidence(
                    root,
                    evidence,
                    expected_identity=identity,
                    now=datetime(2026, 8, 4, 12),
                )
            with self.assertRaises(ExpiryError):
                verify_release_evidence(
                    root,
                    resign("2026-08-04T12:05:01Z"),
                    expected_identity=identity,
                    now=now,
                )
            # A missing timestamp is a format error, not an implicit
            # ``recorded_at=now`` fallback.
            missing = parse_json_bytes(evidence)
            missing["platforms"] = {"macos-arm64": {}}
            with self.assertRaises(FormatError):
                verify_release_evidence(
                    root,
                    canonical_json_bytes(missing),
                    expected_identity=identity,
                    now=now,
                )

        duplicate = evidence.replace(
            b'"recorded_at":"2026-08-04T12:00:00Z"',
            b'"recorded_at":"2026-08-04T12:00:00Z","recorded_at":"2026-08-04T12:00:00Z"',
        )
        with self.assertRaises(FormatError):
            verify_release_evidence(
                root,
                duplicate,
                expected_identity=identity,
                now=now,
            )

    def test_canonical_json_rejects_duplicate_keys_and_noncanonical_numbers(self) -> None:
        with self.assertRaises(TrustError):
            parse_json_bytes(b'{"signed":1,"signed":2}')
        with self.assertRaises(TrustError):
            parse_json_bytes(b'{"value":1.0}')
        with self.assertRaises(TrustError):
            parse_json_bytes(b'{"value":-0}')
        with self.assertRaises(TrustError):
            canonical_json_bytes({1: "not a JSON object key"})

    def test_catalog_text_may_use_escaped_line_breaks_but_not_other_controls(self) -> None:
        parsed = parse_json_bytes(b'{"release_notes":"linha 1\\nlinha 2\\tok"}')
        self.assertEqual("linha 1\nlinha 2\tok", parsed["release_notes"])
        with self.assertRaises(TrustError):
            parse_json_bytes(b'{"value":"bad\\u0000"}')
        self.assertEqual(
            b'{"a":1,"z":"ok"}',
            canonical_json_bytes({"z": "ok", "a": 1}),
        )

    def test_pinned_catalog_chain_verifies_with_independent_rsa_pss_signatures(self) -> None:
        result = verify_release_metadata(
            fixture("root.json"),
            fixture("current.json"),
            fixture("snapshot.json"),
            fixture("catalog.json"),
            now=datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
        )
        self.assertEqual((1, 1, 1), result.versions.as_tuple())
        self.assertEqual("site/public/api/v1/catalog.json", result.catalog.path)
        self.assertEqual(
            result.catalog.sha256,
            hashlib.sha256(fixture("catalog.json")).hexdigest(),
        )

    def test_canonical_inventory_chain_matches_the_public_fixture_chain(self) -> None:
        result = verify_release_metadata(
            (ROOT / "maintenance/inventory/trust/root.json").read_bytes(),
            (ROOT / "maintenance/inventory/trust/current.json").read_bytes(),
            (ROOT / "maintenance/inventory/trust/snapshot.json").read_bytes(),
            fixture("catalog.json"),
            now=datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
        )
        self.assertEqual("0.7.3", result.release)

    def test_pinned_root_key_is_not_derived_from_catalog_hash(self) -> None:
        root = envelope("root.json")
        key = root["signed"]["keys"][root["signed"]["roles"]["root"]["keyids"][0]]
        self.assertEqual(key_id(key), root["signed"]["roles"]["root"]["keyids"][0])
        self.assertNotEqual(
            key_id(key), hashlib.sha256(fixture("catalog.json")).hexdigest(),
        )

    def test_root_public_schema_rejects_private_key_material(self) -> None:
        root = envelope("root.json")
        key_id_value = root["signed"]["roles"]["root"]["keyids"][0]
        root["signed"]["keys"][key_id_value]["keyval"]["private"] = "PRIVATE"
        with self.assertRaises(FormatError):
            verify_release_metadata(
                canonical_json_bytes(root),
                fixture("current.json"),
                fixture("snapshot.json"),
                fixture("catalog.json"),
                now=datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
            )

    def test_signature_tampering_and_threshold_fail_closed(self) -> None:
        current = envelope("current.json")
        current["signed"]["snapshot"]["version"] = 99
        with self.assertRaises(SignatureError):
            verify_release_metadata(
                fixture("root.json"),
                canonical_json_bytes(current),
                fixture("snapshot.json"),
                fixture("catalog.json"),
                now=datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
            )

        root = envelope("root.json")
        root["signatures"] = []
        with self.assertRaises(SignatureError):
            verify_release_metadata(
                canonical_json_bytes(root),
                fixture("current.json"),
                fixture("snapshot.json"),
                fixture("catalog.json"),
                now=datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
            )

    def test_rollback_and_freeze_are_rejected_against_trusted_versions(self) -> None:
        # Keep a complete state so this assertion exercises same-version
        # equivocation rather than relying on a malformed checkpoint.
        trusted = _trusted_catalog_fixture(current_digest="0" * 64)
        with self.assertRaises(RollbackError):
            verify_release_metadata(
                fixture("root.json"),
                fixture("current.json"),
                fixture("snapshot.json"),
                fixture("catalog.json"),
                now=datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
                trusted=trusted,
            )

        with self.assertRaises(FreezeError):
            verify_release_metadata(
                fixture("root.json"),
                fixture("current.json"),
                fixture("snapshot.json"),
                fixture("catalog.json"),
                now=datetime(2026, 8, 20, 12, tzinfo=timezone.utc),
            )

    def test_catalog_digest_and_length_are_authenticated_by_snapshot(self) -> None:
        catalog = fixture("catalog.json") + b"\n"
        with self.assertRaises(TrustError):
            verify_release_metadata(
                fixture("root.json"),
                fixture("current.json"),
                fixture("snapshot.json"),
                catalog,
                now=datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
            )

    def test_snapshot_target_binds_received_bytes_not_just_parsed_json(self) -> None:
        # The role signature covers ``signed`` canonically, so envelope
        # whitespace is not itself a signature failure.  ``current.snapshot``
        # must nevertheless bind the exact bytes downloaded from the mirror;
        # otherwise a rewritten envelope could bypass the signed length/digest
        # checkpoint while retaining the same parsed object.
        snapshot = b" \n" + fixture("snapshot.json")
        with self.assertRaises(DigestError):
            verify_release_metadata(
                fixture("root.json"),
                fixture("current.json"),
                snapshot,
                fixture("catalog.json"),
                now=datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
            )

    def test_signature_encoding_is_canonical_base64url(self) -> None:
        current = envelope("current.json")
        signature = current["signatures"][0]["sig"]
        self.assertNotIn("=", signature)
        self.assertEqual(
            signature,
            base64.urlsafe_b64encode(
                base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
            ).decode("ascii").rstrip("="),
        )

    def test_root_threshold_two_of_three_accepts_two_independent_signers(self) -> None:
        result = verify_release_metadata(
            fixture("root-threshold.json"),
            fixture("current.json"),
            fixture("snapshot.json"),
            fixture("catalog.json"),
            now=datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
        )
        self.assertEqual((1, 1, 1), result.versions.as_tuple())

    def test_root_rotation_requires_old_and_new_thresholds_and_rejects_revoked_keys(self) -> None:
        old_root = parse_json_bytes(fixture("root.json"))
        trusted = TrustedVersions(
            root=1,
            snapshot=1,
            current=1,
            root_digest=hashlib.sha256(
                canonical_json_bytes(old_root["signed"])
            ).hexdigest(),
            snapshot_digest=hashlib.sha256(
                canonical_json_bytes(envelope("snapshot.json")["signed"])
            ).hexdigest(),
            current_digest=hashlib.sha256(
                canonical_json_bytes(envelope("current.json")["signed"])
            ).hexdigest(),
            root_metadata=fixture("root.json"),
        )
        result = verify_release_metadata(
            fixture("root-v2.json"),
            fixture("current-v2.json"),
            fixture("snapshot-v2.json"),
            fixture("catalog.json"),
            now=datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
            trusted=trusted,
        )
        self.assertEqual((2, 2, 2), result.versions.as_tuple())
        with self.assertRaises(SignatureError):
            verify_release_metadata(
                fixture("root-v2.json"),
                fixture("current.json"),
                fixture("snapshot-v2.json"),
                fixture("catalog.json"),
                now=datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
                trusted=trusted,
            )


if __name__ == "__main__":
    unittest.main()
