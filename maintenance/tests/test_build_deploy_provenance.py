from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from maintenance.tools.build_deploy_provenance import (
    DeployProvenanceError,
    build_deploy_provenance,
    write_deploy_provenance,
)


ROOT = Path(__file__).resolve().parents[2]


class BuildDeployProvenanceTests(unittest.TestCase):
    def test_version_and_health_omit_installer_history_and_stay_owner_only(self) -> None:
        documents = build_deploy_provenance(
            commit="a" * 40,
            validate_run_id=1,
            deploy_run_id=2,
            catalog_sha256="b" * 64,
        )
        self.assertEqual("owner-only", documents["version"]["release_audience"])
        self.assertFalse(documents["version"]["external_public"])
        self.assertEqual("ok", documents["health"]["status"])
        self.assertEqual(documents["version"]["commit"], documents["health"]["commit"])

    def test_writes_the_three_public_paths(self) -> None:
        documents = build_deploy_provenance(
            commit="a" * 40,
            validate_run_id=11,
            deploy_run_id=22,
            catalog_sha256="c" * 64,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_deploy_provenance(root, documents)
            version = json.loads((root / "version").read_text(encoding="utf-8"))
            api_version = json.loads((root / "api/v1/version").read_text(encoding="utf-8"))
            health = json.loads((root / "api/v1/health").read_text(encoding="utf-8"))
            self.assertEqual(documents["version"], version)
            self.assertEqual(version, api_version)
            self.assertEqual(documents["health"], health)

    def test_rejects_unsafe_coordinates(self) -> None:
        with self.assertRaises(DeployProvenanceError):
            build_deploy_provenance(
                commit="not-a-sha",
                validate_run_id=1,
                deploy_run_id=2,
                catalog_sha256="b" * 64,
            )


if __name__ == "__main__":
    unittest.main()
