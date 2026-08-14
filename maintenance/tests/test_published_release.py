from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "maintenance/tools/verify_published_release.py"
SPEC = importlib.util.spec_from_file_location("verify_published_release", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PublishedReleaseTests(unittest.TestCase):
    def test_public_identity_accepts_semver_release_candidates(self):
        MODULE._validate_identity("example/project", "1.0.0-rc.1", "a" * 40)

    def test_public_release_metadata_requires_durable_native_evidence(self):
        self.assertIn("release-evidence.json", MODULE.PUBLIC_METADATA_NAMES)
        self.assertIn("evidence-root.json", MODULE.PUBLIC_METADATA_NAMES)
        self.assertIn("release-receipt.json", MODULE.PUBLIC_METADATA_NAMES)
        self.assertEqual(8, len(MODULE.PUBLIC_METADATA_NAMES))

    def metadata_payloads(self) -> dict[str, bytes]:
        return {
            name: f"x86QW public {name}\n".encode("utf-8")
            for name in MODULE.PUBLIC_METADATA_NAMES
        }

    def manifest(self) -> dict[str, object]:
        metadata = {
            name: {
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            for name, payload in self.metadata_payloads().items()
            if name in MODULE.BOUND_METADATA_NAMES
        }
        return {
            "format": 2,
            "project": "x86qw",
            "version": "1.0.0",
            "commit": "a" * 40,
            "metadata": metadata,
            "artifacts": {
                "installer/x86qw-installer-1.0.0.zip": {
                    "size": 123,
                    "sha256": "b" * 64,
                },
            },
        }

    def fetcher(self, payloads: dict[str, object]):
        def fetch(url: str, timeout: float) -> dict[str, object]:
            del timeout
            value = payloads[url]
            if isinstance(value, Exception):
                raise value
            return value  # type: ignore[return-value]

        return fetch

    def ref(self, *, commit: str = "a" * 40, object_type: str = "commit") -> dict[str, object]:
        return {
            "ref": "refs/tags/x86qw-installer-1.0.0",
            "object": {"sha": commit, "type": object_type},
        }

    def release(self, *, assets: list[dict[str, object]] | None = None, draft: bool = False, prerelease: bool = False) -> dict[str, object]:
        metadata_assets = []
        for offset, (name, payload) in enumerate(self.metadata_payloads().items(), start=10):
            metadata_assets.append({
                "id": offset,
                "name": name,
                "state": "uploaded",
                "size": len(payload),
                "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
            })
        return {
            "tag_name": "x86qw-installer-1.0.0",
            "draft": draft,
            "prerelease": prerelease,
            "assets": assets if assets is not None else [{
                "id": 7,
                "name": "x86qw-installer-1.0.0.zip",
                "state": "uploaded",
                "size": 123,
                "digest": "sha256:" + "b" * 64,
            }, *metadata_assets],
        }

    def durable_receipt(self) -> dict[str, object]:
        return {
            "public_acceptance": {
                "commit": "c" * 40,
                "run_id": "31752738003",
                "artifact_id": "9004",
                "artifact_name": "public-acceptance-1.0.0-rc.2-31752738003-1",
                "version": "1.0.0-rc.2",
                "receipt_sha256": "d" * 64,
                "bundle_sha256": "e" * 64,
                "catalog_sha256": "f" * 64,
            },
        }

    def classify(
        self,
        payloads: dict[str, object],
        *,
        manifest: dict[str, object] | None = None,
        durable_receipt: dict[str, object] | None = None,
        **kwargs: object,
    ) -> str:
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary)
            for name, payload in self.metadata_payloads().items():
                (candidate / name).write_bytes(payload)
            fetch_json = kwargs.pop("fetch_json", None) or self.fetcher(payloads)
            with mock.patch.object(
                MODULE,
                "validate_durable_assets",
                return_value=self.durable_receipt() if durable_receipt is None else durable_receipt,
            ):
                return MODULE.classify_published_release(
                    candidate,
                    trust_root=Path(temporary) / "root.json",
                    repository="example/project",
                    version="1.0.0",
                    commit="a" * 40,
                    verify=lambda *_args, **_kwargs: manifest or self.manifest(),
                    fetch_json=fetch_json,
                    **kwargs,
                )

    def test_final_release_requires_public_acceptance_handoff_in_durable_receipt(self):
        payloads = {
            "https://api.github.com/repos/example/project/git/ref/tags/x86qw-installer-1.0.0": self.ref(),
            "https://api.github.com/repos/example/project/releases/tags/x86qw-installer-1.0.0": self.release(),
        }
        with self.assertRaisesRegex(MODULE.PublishedReleaseError, "aceitação pública"):
            self.classify(payloads, durable_receipt={})

    def test_final_release_rejects_malformed_public_acceptance_digest(self):
        payloads = {
            "https://api.github.com/repos/example/project/git/ref/tags/x86qw-installer-1.0.0": self.ref(),
            "https://api.github.com/repos/example/project/releases/tags/x86qw-installer-1.0.0": self.release(),
        }
        durable = self.durable_receipt()
        durable["public_acceptance"]["bundle_sha256"] = "not-a-digest"  # type: ignore[index]
        with self.assertRaisesRegex(MODULE.PublishedReleaseError, "coordenadas inválidas"):
            self.classify(payloads, durable_receipt=durable)

    def test_both_absent_is_absent(self):
        missing = MODULE.PublishedReleaseNotFound()
        payloads = {
            "https://api.github.com/repos/example/project/git/ref/tags/x86qw-installer-1.0.0": missing,
            "https://api.github.com/repos/example/project/releases/tags/x86qw-installer-1.0.0": missing,
        }
        self.assertEqual("absent", self.classify(payloads))

    def test_exact_tag_and_release_are_exact(self):
        payloads = {
            "https://api.github.com/repos/example/project/git/ref/tags/x86qw-installer-1.0.0": self.ref(),
            "https://api.github.com/repos/example/project/releases/tags/x86qw-installer-1.0.0": self.release(),
        }
        self.assertEqual("exact", self.classify(payloads))

    def test_zip_only_release_is_rejected(self):
        payloads = {
            "https://api.github.com/repos/example/project/git/ref/tags/x86qw-installer-1.0.0": self.ref(),
            "https://api.github.com/repos/example/project/releases/tags/x86qw-installer-1.0.0": self.release(
                assets=[self.release()["assets"][0]],
            ),
        }
        with self.assertRaises(MODULE.PublishedReleaseError):
            self.classify(payloads)

    def test_only_one_public_object_is_inconclusive(self):
        missing = MODULE.PublishedReleaseNotFound()
        payloads = {
            "https://api.github.com/repos/example/project/git/ref/tags/x86qw-installer-1.0.0": self.ref(),
            "https://api.github.com/repos/example/project/releases/tags/x86qw-installer-1.0.0": missing,
        }
        with self.assertRaisesRegex(MODULE.PublishedReleaseError, "assimétrico"):
            self.classify(payloads)

    def test_wrong_commit_and_annotated_tag_are_rejected(self):
        payloads = {
            "https://api.github.com/repos/example/project/git/ref/tags/x86qw-installer-1.0.0": self.ref(commit="c" * 40),
            "https://api.github.com/repos/example/project/releases/tags/x86qw-installer-1.0.0": self.release(),
        }
        with self.assertRaises(MODULE.PublishedReleaseError):
            self.classify(payloads)
        payloads[
            "https://api.github.com/repos/example/project/git/ref/tags/x86qw-installer-1.0.0"
        ] = self.ref(object_type="tag")
        with self.assertRaises(MODULE.PublishedReleaseError):
            self.classify(payloads)

    def test_draft_prerelease_and_asset_drift_are_rejected(self):
        for kwargs in ({"draft": True}, {"prerelease": True}):
            payloads = {
                "https://api.github.com/repos/example/project/git/ref/tags/x86qw-installer-1.0.0": self.ref(),
                "https://api.github.com/repos/example/project/releases/tags/x86qw-installer-1.0.0": self.release(**kwargs),
            }
            with self.subTest(kwargs=kwargs), self.assertRaises(MODULE.PublishedReleaseError):
                self.classify(payloads)
        drifted = self.release(assets=[{
            "id": 7,
            "name": "x86qw-installer-1.0.0.zip",
            "state": "uploaded",
            "size": 124,
            "digest": "sha256:" + "b" * 64,
        }])
        payloads = {
            "https://api.github.com/repos/example/project/git/ref/tags/x86qw-installer-1.0.0": self.ref(),
            "https://api.github.com/repos/example/project/releases/tags/x86qw-installer-1.0.0": drifted,
        }
        with self.assertRaises(MODULE.PublishedReleaseError):
            self.classify(payloads)

    def test_extra_missing_duplicate_and_bad_digest_assets_are_rejected(self):
        base = self.release()["assets"]
        cases = [
            [],
            [*base, {"id": 8, "name": "extra.zip", "state": "uploaded", "size": 1, "digest": "sha256:" + "c" * 64}],
            [*base, dict(base[0])],  # type: ignore[index]
            [{**base[0], "digest": "sha256:bad"}],  # type: ignore[index]
        ]
        for assets in cases:
            payloads = {
                "https://api.github.com/repos/example/project/git/ref/tags/x86qw-installer-1.0.0": self.ref(),
                "https://api.github.com/repos/example/project/releases/tags/x86qw-installer-1.0.0": self.release(assets=assets),
            }
            with self.subTest(assets=assets), self.assertRaises(MODULE.PublishedReleaseError):
                self.classify(payloads)

    def test_duplicate_candidate_zip_basenames_are_rejected(self):
        manifest = self.manifest()
        manifest["artifacts"] = {
            **manifest["artifacts"],  # type: ignore[dict-item]
            "other/x86qw-installer-1.0.0.zip": {"size": 1, "sha256": "c" * 64},
        }
        missing = MODULE.PublishedReleaseNotFound()
        payloads = {
            "https://api.github.com/repos/example/project/git/ref/tags/x86qw-installer-1.0.0": missing,
            "https://api.github.com/repos/example/project/releases/tags/x86qw-installer-1.0.0": missing,
        }
        with self.assertRaisesRegex(MODULE.PublishedReleaseError, "basename"):
            self.classify(payloads, manifest=manifest)

    def test_uppercase_zip_artifacts_are_included_in_public_set(self):
        manifest = self.manifest()
        manifest["artifacts"] = {
            "installer/x86qw-installer-1.0.0.ZIP": {
                "size": 123,
                "sha256": "b" * 64,
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary)
            for name, payload in self.metadata_payloads().items():
                (candidate / name).write_bytes(payload)
            assets = MODULE._candidate_assets(manifest, candidate)
            self.assertEqual(
                {"x86qw-installer-1.0.0.ZIP": {"size": 123, "digest": "sha256:" + "b" * 64}},
                {name: assets[name] for name in assets if name.endswith(".ZIP")},
            )

    def test_non_404_transport_error_and_invalid_schema_fail_closed(self):
        failure = RuntimeError("server error")
        payloads = {
            "https://api.github.com/repos/example/project/git/ref/tags/x86qw-installer-1.0.0": failure,
            "https://api.github.com/repos/example/project/releases/tags/x86qw-installer-1.0.0": self.release(),
        }
        with self.assertRaises(MODULE.PublishedReleaseError):
            self.classify(payloads)
        payloads["https://api.github.com/repos/example/project/git/ref/tags/x86qw-installer-1.0.0"] = {"object": {}}
        with self.assertRaises(MODULE.PublishedReleaseError):
            self.classify(payloads)

    def test_cli_prints_only_state_and_requires_allow_absent(self):
        with mock.patch.object(MODULE, "classify_published_release", return_value="absent"):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(1, MODULE.main([
                    "--repository", "example/project", "--version", "1.0.0",
                    "--commit", "a" * 40, "--candidate", "/tmp/candidate",
                    "--trust-root", "/tmp/root.json",
                ]))
            self.assertEqual("absent\n", stdout.getvalue())
            self.assertEqual("", stderr.getvalue())
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(0, MODULE.main([
                    "--repository", "example/project", "--version", "1.0.0",
                    "--commit", "a" * 40, "--candidate", "/tmp/candidate",
                    "--trust-root", "/tmp/root.json", "--allow-absent",
                ]))
            self.assertEqual("absent\n", stdout.getvalue())
            self.assertEqual("", stderr.getvalue())

    def test_external_json_rejects_duplicate_keys(self):
        response = mock.Mock(data=b'{"tag_name":"one","tag_name":"two"}')
        with mock.patch.object(MODULE, "download", return_value=response):
            with self.assertRaisesRegex(MODULE.PublishedReleaseError, "chaves duplicadas"):
                MODULE._fetch_json_with_downloader(
                    "https://api.github.com/example", 5.0, token="token"
                )

    def test_api_root_uses_the_shared_https_contract(self):
        missing = MODULE.PublishedReleaseNotFound()
        payloads = {
            "https://api.github.com/repos/example/project/git/ref/tags/x86qw-installer-1.0.0": missing,
            "https://api.github.com/repos/example/project/releases/tags/x86qw-installer-1.0.0": missing,
        }
        for api_root in ("http://api.github.com", "https://", "https://user:pass@api.github.com", "https://api.github.com/#fragment"):
            with self.subTest(api_root=api_root), self.assertRaises(MODULE.PublishedReleaseError):
                self.classify(payloads, api_root=api_root)

    def test_shared_deadline_is_never_extended_for_later_query(self):
        missing = MODULE.PublishedReleaseNotFound()
        payloads = {
            "https://api.github.com/repos/example/project/git/ref/tags/x86qw-installer-1.0.0": missing,
            "https://api.github.com/repos/example/project/releases/tags/x86qw-installer-1.0.0": missing,
        }
        timeouts: list[float] = []

        def fetcher(url: str, timeout: float) -> dict[str, object]:
            timeouts.append(timeout)
            value = payloads[url]
            raise value

        with mock.patch.object(MODULE.time, "monotonic", side_effect=[0.0, 0.1, 0.9]):
            self.assertEqual(
                "absent",
                self.classify(payloads, fetch_json=fetcher, deadline_seconds=1.0),
            )
        self.assertEqual(2, len(timeouts))
        self.assertAlmostEqual(0.9, timeouts[0])
        self.assertAlmostEqual(0.1, timeouts[1])


if __name__ == "__main__":
    unittest.main()
