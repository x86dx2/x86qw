from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "maintenance/tools/verify_tuf_release_source.py"


def write_timestamp(path: Path, *, version: int, expires: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "signatures": [{"keyid": "timestamp-key", "sig": expires}],
                "signed": {
                    "_type": "timestamp",
                    "expires": expires,
                    "meta": {
                        "7.snapshot.json": {
                            "hashes": {"sha256": "a" * 64},
                            "length": 123,
                            "version": 7,
                        }
                    },
                    "spec_version": "1.0.31",
                    "version": version,
                },
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
        encoding="utf-8",
    )


class VerifyTufReleaseSourceTests(unittest.TestCase):
    def run_gate(self, source: Path, projection: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--source-repository",
                str(source),
                "--release-projection",
                str(projection),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

    def run_promotion_gate(
        self,
        source: Path,
        projection: Path,
        public: Path,
        renewed: Path,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--source-repository",
                str(source),
                "--release-projection",
                str(projection),
                "--public-repository",
                str(public),
                "--renewed-repository",
                str(renewed),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_rejects_same_timestamp_version_with_different_signed_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = workspace / "source"
            projection = workspace / "projection"
            write_timestamp(
                source / "metadata/timestamp.json",
                version=32,
                expires="2026-10-01T18:56:35Z",
            )
            write_timestamp(
                projection / "metadata/timestamp.json",
                version=32,
                expires="2026-10-01T19:34:52Z",
            )

            completed = self.run_gate(source, projection)

            self.assertNotEqual(0, completed.returncode)
            self.assertIn("equivocação", completed.stderr)
            self.assertIn("versão 32", completed.stderr)

    def test_accepts_the_exact_timestamp_bound_to_the_release_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = workspace / "source"
            projection = workspace / "projection"
            timestamp = source / "metadata/timestamp.json"
            write_timestamp(
                timestamp,
                version=33,
                expires="2026-10-02T00:00:00Z",
            )
            projected = projection / "metadata/timestamp.json"
            projected.parent.mkdir(parents=True, exist_ok=True)
            projected.write_bytes(timestamp.read_bytes())

            completed = self.run_gate(source, projection)

            self.assertEqual(0, completed.returncode, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual("bound-release-source", result["status"])
            self.assertEqual(33, result["timestamp_version"])

    def test_rejects_public_equivocation_before_deployment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = workspace / "source"
            projection = workspace / "projection"
            public = workspace / "public"
            renewed = workspace / "renewed"
            write_timestamp(
                source / "metadata/timestamp.json",
                version=32,
                expires="2026-10-01T18:56:35Z",
            )
            projected = projection / "metadata/timestamp.json"
            projected.parent.mkdir(parents=True, exist_ok=True)
            projected.write_bytes((source / "metadata/timestamp.json").read_bytes())
            write_timestamp(
                public / "metadata/timestamp.json",
                version=32,
                expires="2026-10-01T19:34:52Z",
            )
            write_timestamp(
                renewed / "metadata/timestamp.json",
                version=33,
                expires="2026-10-02T19:34:52Z",
            )

            completed = self.run_promotion_gate(source, projection, public, renewed)

            self.assertNotEqual(0, completed.returncode)
            self.assertIn("produção", completed.stderr)
            self.assertIn("equivocação", completed.stderr)

    def test_accepts_one_monotonic_source_and_one_renewal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = workspace / "source"
            projection = workspace / "projection"
            public = workspace / "public"
            renewed = workspace / "renewed"
            write_timestamp(
                public / "metadata/timestamp.json",
                version=32,
                expires="2026-10-01T19:34:52Z",
            )
            write_timestamp(
                source / "metadata/timestamp.json",
                version=33,
                expires="2026-10-02T19:34:52Z",
            )
            projected = projection / "metadata/timestamp.json"
            projected.parent.mkdir(parents=True, exist_ok=True)
            projected.write_bytes((source / "metadata/timestamp.json").read_bytes())
            write_timestamp(
                renewed / "metadata/timestamp.json",
                version=34,
                expires="2026-10-03T19:34:52Z",
            )

            completed = self.run_promotion_gate(source, projection, public, renewed)

            self.assertEqual(0, completed.returncode, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual("safe-monotonic-promotion", result["status"])
            self.assertEqual(32, result["public_timestamp_version"])
            self.assertEqual(33, result["source_timestamp_version"])
            self.assertEqual(34, result["renewed_timestamp_version"])

    def test_accepts_redeploy_when_public_already_matches_the_exact_renewal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = workspace / "source"
            projection = workspace / "projection"
            public = workspace / "public"
            renewed = workspace / "renewed"
            write_timestamp(
                source / "metadata/timestamp.json",
                version=39,
                expires="2026-10-02T20:25:22Z",
            )
            projected = projection / "metadata/timestamp.json"
            projected.parent.mkdir(parents=True, exist_ok=True)
            projected.write_bytes((source / "metadata/timestamp.json").read_bytes())
            write_timestamp(
                renewed / "metadata/timestamp.json",
                version=40,
                expires="2026-10-02T21:06:36Z",
            )
            public_timestamp = public / "metadata/timestamp.json"
            public_timestamp.parent.mkdir(parents=True, exist_ok=True)
            public_timestamp.write_bytes(
                (renewed / "metadata/timestamp.json").read_bytes()
            )

            completed = self.run_promotion_gate(source, projection, public, renewed)

            self.assertEqual(0, completed.returncode, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual("safe-converged-redeployment", result["status"])
            self.assertEqual(40, result["public_timestamp_version"])
            self.assertEqual(39, result["source_timestamp_version"])
            self.assertEqual(40, result["renewed_timestamp_version"])

    def test_rejects_redeploy_when_public_diverges_from_same_version_renewal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = workspace / "source"
            projection = workspace / "projection"
            public = workspace / "public"
            renewed = workspace / "renewed"
            write_timestamp(
                source / "metadata/timestamp.json",
                version=39,
                expires="2026-10-02T20:25:22Z",
            )
            projected = projection / "metadata/timestamp.json"
            projected.parent.mkdir(parents=True, exist_ok=True)
            projected.write_bytes((source / "metadata/timestamp.json").read_bytes())
            write_timestamp(
                public / "metadata/timestamp.json",
                version=40,
                expires="2026-10-02T21:06:35Z",
            )
            write_timestamp(
                renewed / "metadata/timestamp.json",
                version=40,
                expires="2026-10-02T21:06:36Z",
            )

            completed = self.run_promotion_gate(source, projection, public, renewed)

            self.assertNotEqual(0, completed.returncode)
            self.assertIn("equivocação", completed.stderr)
            self.assertIn("renovação", completed.stderr)


if __name__ == "__main__":
    unittest.main()
