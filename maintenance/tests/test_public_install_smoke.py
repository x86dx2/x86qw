from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from maintenance.tools import public_install_smoke as smoke


def package(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "package": "x86qw-installer",
        "version": "1.0.0",
        "current": True,
        "filename": "x86qw-installer-1.0.0.zip",
        "size": 123,
        "sha256": "a" * 64,
        "urls": ["https://downloads.example.invalid/x86qw-installer-1.0.0.zip"],
    }
    value.update(overrides)
    return value


class PublicInstallSmokeTests(unittest.TestCase):
    def test_catalog_requires_exactly_one_current_candidate(self):
        catalog = {"project": "x86qw", "packages": [package()]}
        result = smoke._catalog_package(catalog, "1.0.0")
        self.assertEqual(123, result["size"])
        self.assertEqual("a" * 64, result["sha256"])

        for packages in ([package(), package()], [package(current=False)], []):
            with self.subTest(packages=packages):
                with self.assertRaises(smoke.PublicInstallSmokeError):
                    smoke._catalog_package({"project": "x86qw", "packages": packages}, "1.0.0")

    def test_catalog_rejects_http_mirror_and_invalid_digest(self):
        with self.assertRaises(smoke.PublicInstallSmokeError):
            smoke._catalog_package(
                {"project": "x86qw", "packages": [package(urls=["http://example.invalid/x.zip"])]},
                "1.0.0",
            )
        with self.assertRaises(smoke.PublicInstallSmokeError):
            smoke._catalog_package(
                {"project": "x86qw", "packages": [package(sha256="not-a-digest")]},
                "1.0.0",
            )

    def test_catalog_rejects_untrusted_shape_and_credentials(self):
        with self.assertRaises(smoke.PublicInstallSmokeError):
            smoke._catalog_package({"project": "other", "packages": [package()]}, "1.0.0")
        with self.assertRaises(smoke.PublicInstallSmokeError):
            smoke._catalog_package(
                {"project": "x86qw", "packages": [package(
                    urls=["https://user:password@example.invalid/x.zip"],
                )]},
                "1.0.0",
            )

    def test_run_smoke_requires_production_trust_endpoint(self):
        with self.assertRaises(smoke.PublicInstallSmokeError):
            smoke.run_smoke(
                version="1.0.0",
                platform="linux",
                channel="stable",
                release="latest",
                profile="essential",
                catalog_url="https://x86qw.example.invalid/catalog.json",
                trust_metadata_url="http://localhost/trust",
            )

    def test_parser_requires_explicit_trust_metadata_url(self):
        with self.assertRaises(SystemExit) as raised:
            smoke._parser().parse_args([
                "--version", "1.0.0", "--platform", "linux",
                "--channel", "stable", "--release", "latest",
                "--profile", "essential",
            ])
        self.assertEqual(2, raised.exception.code)

    def test_run_smoke_uses_exact_bundle_and_json_verification(self):
        candidate = package()
        runs: list[list[str]] = []

        def fake_run(command, **kwargs):
            runs.append(list(command))
            if "version" in command:
                output = json.dumps({
                    "command": "version",
                    "data": {"project": "x86qw", "version": "1.0.0"},
                    "ok": True,
                })
            elif "verify" in command:
                output = json.dumps({"project": "x86qw", "ok": True})
            else:
                output = "instalação concluída\n"
            return mock.Mock(returncode=0, stdout=output)

        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(smoke, "_download_catalog", return_value={
                "project": "x86qw", "packages": [candidate],
            }))
            stack.enter_context(mock.patch.object(smoke, "download_mirrors"))
            stack.enter_context(mock.patch.object(smoke, "validate_installer_bundle", return_value=mock.sentinel.plan))
            stack.enter_context(mock.patch.object(smoke, "extract_archive"))
            stack.enter_context(mock.patch.object(smoke.subprocess, "run", side_effect=fake_run))
            # The extracted application and receipt are represented by the
            # mock filesystem; this keeps the test independent of a published
            # artifact while still checking the command contract.
            real_is_file = Path.is_file
            real_read_text = Path.read_text

            def fake_is_file(path: Path) -> bool:
                if path.name == "x86qw.pyz" or path.name == "receipt":
                    return True
                return real_is_file(path)

            def fake_read_text(path: Path, *args, **kwargs) -> str:
                if path.name == "receipt":
                    return json.dumps({"version": "1.0.0"})
                return real_read_text(path, *args, **kwargs)

            stack.enter_context(mock.patch.object(Path, "is_file", fake_is_file))
            stack.enter_context(mock.patch.object(Path, "read_text", fake_read_text))
            with contextlib.redirect_stdout(io.StringIO()):
                result = smoke.run_smoke(
                    version="1.0.0",
                    platform="linux",
                    channel="stable",
                    release="latest",
                    profile="essential",
                    catalog_url="https://x86qw.example.invalid/catalog.json",
                    trust_metadata_url="https://trust.example.invalid/x86qw",
                )

        self.assertTrue(result["verified"])
        self.assertEqual(3, len(runs))
        self.assertIn("--non-interactive", runs[0])
        self.assertIn("--json", runs[1])
        self.assertIn("--json", runs[2])
        self.assertIn("verify", runs[2])


if __name__ == "__main__":
    unittest.main()
