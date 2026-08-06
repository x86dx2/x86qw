from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from maintenance.tools import build_installer_bundle as builder


ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / "maintenance/inventory/runtime-dependencies.json"


class RuntimeDependencyTests(unittest.TestCase):
    def test_public_zipapp_contains_and_imports_pinned_tuf_dependencies(self) -> None:
        payload = builder.zipapp_bytes("9.9.9")
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = set(archive.namelist())
            self.assertIn("tuf/ngclient/updater.py", names)
            self.assertIn("securesystemslib/signer/_key.py", names)
            self.assertIn("urllib3/__init__.py", names)
            self.assertIn("_x86qw/runtime-dependencies.json", names)
            self.assertNotIn("_x86qw/trust/root.json", names)
            lock = json.loads(archive.read("_x86qw/runtime-dependencies.json"))
        self.assertEqual(json.loads(LOCK.read_text(encoding="utf-8")), lock)

        with tempfile.TemporaryDirectory() as temporary:
            application = Path(temporary) / "x86qw.pyz"
            application.write_bytes(payload)
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import sys; sys.path.insert(0, sys.argv[1]); "
                    "import tuf, securesystemslib, urllib3; "
                    "print(tuf.__version__, securesystemslib.__version__, urllib3.__version__)",
                    str(application),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("7.0.0 1.4.0 2.7.0", completed.stdout.strip())

    def test_builder_rejects_a_wheel_that_differs_from_its_lock(self) -> None:
        if not hasattr(builder, "runtime_dependency_members"):
            self.fail("runtime dependency verifier is missing")
        read = builder.read_regular_file

        def tamper(path: Path, *args: object, **kwargs: object) -> bytes:
            payload = read(path, *args, **kwargs)
            if path.name == "tuf-7.0.0-py3-none-any.whl":
                return payload + b"tampered"
            return payload

        with mock.patch.object(builder, "read_regular_file", side_effect=tamper):
            with self.assertRaisesRegex(ValueError, "SHA-256|hash|diverge"):
                builder.runtime_dependency_members()


if __name__ == "__main__":
    unittest.main()
