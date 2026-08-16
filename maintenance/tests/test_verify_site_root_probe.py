from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from maintenance.tools import verify_site_root_probe


ROOT = Path(__file__).resolve().parents[2]


class VerifySiteRootProbeTests(unittest.TestCase):
    def test_assembled_html_without_owner_only_fails_closed(self) -> None:
        with self.assertRaises(verify_site_root_probe.RootProbeError) as raised:
            verify_site_root_probe.verify_root_probe(
                assembled_html=b"<html>x86QW</html>",
                live_status=403,
                live_body=b"error code: 1010",
            )
        self.assertIn("assembled", str(raised.exception))

    def test_live_200_without_owner_only_fails_even_if_assembled_is_honest(self) -> None:
        with self.assertRaises(verify_site_root_probe.RootProbeError) as raised:
            verify_site_root_probe.verify_root_probe(
                assembled_html=b"<p>owner-only</p>",
                live_status=200,
                live_body=b"<html>external-public welcome</html>",
            )
        self.assertIn("live", str(raised.exception))

    def test_cloudflare_403_is_recorded_when_assembled_html_names_owner_only(self) -> None:
        result = verify_site_root_probe.verify_root_probe(
            assembled_html="<p>Instalacao owner-only</p>".encode("ascii"),
            live_status=403,
            live_body=b"error code: 1010",
        )
        self.assertEqual("verified-assembled", result["status"])
        self.assertEqual(403, result["live_status"])
        self.assertEqual("cloudflare_challenge", result["live_root"])

    def test_live_200_with_owner_only_is_fully_verified(self) -> None:
        result = verify_site_root_probe.verify_root_probe(
            assembled_html=b"<p>owner-only</p>",
            live_status=200,
            live_body=b"<p>A release final e owner-only</p>",
        )
        self.assertEqual("verified", result["status"])
        self.assertEqual(200, result["live_status"])
        self.assertEqual("owner-only", result["live_root"])

    def test_cli_reads_assembled_index_and_does_not_treat_403_as_audience_ok(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            assembled = Path(temporary) / "index.html"
            assembled.write_text("<p>owner-only</p>", encoding="utf-8")
            report = Path(temporary) / "probe.json"
            fetch = mock.Mock(return_value=(403, b"blocked"))
            exit_code = verify_site_root_probe.main(
                [
                    "--assembled",
                    str(assembled),
                    "--live-url",
                    "https://x86qw.example/?",
                    "--report",
                    str(report),
                ],
                fetch=fetch,
                stdout=io.StringIO(),
            )
            self.assertEqual(0, exit_code)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual("cloudflare_challenge", payload["live_root"])
            fetch.assert_called_once()

    def test_projection_repair_verifies_assembled_html_before_the_live_root(self) -> None:
        source = (ROOT / ".github/workflows/site-projection-repair.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("verify_site_root_probe.py", source)
        self.assertIn("release-work/site/public/index.html", source)
        self.assertNotIn("| grep -F \"owner-only\"", source)


if __name__ == "__main__":
    unittest.main()
