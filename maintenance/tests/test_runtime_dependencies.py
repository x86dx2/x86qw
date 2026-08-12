from __future__ import annotations

import hashlib
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
            self.assertIn("securesystemslib/_internal/__init__.py", names)
            self.assertIn("urllib3/__init__.py", names)
            self.assertIn("_x86qw/runtime-dependencies.json", names)
            self.assertIn("_x86qw/trust/root.json", names)
            self.assertEqual(
                (ROOT / "maintenance/trust/root.json").read_bytes(),
                archive.read("_x86qw/trust/root.json"),
            )
            self.assertFalse(any(name.endswith((".pem", ".key")) for name in names))
            lock = json.loads(archive.read("_x86qw/runtime-dependencies.json"))
        self.assertEqual(builder.runtime_dependency_projection(), lock)
        self.assertNotIn("source", lock["dependencies"][0])
        self.assertNotIn("package_prefixes", lock["dependencies"][0])

        wheel_path = (
            ROOT / "maintenance/vendor/wheels/securesystemslib-1.4.0-py3-none-any.whl"
        )
        with zipfile.ZipFile(wheel_path) as wheel:
            record = next(
                wheel.read(name).decode("utf-8")
                for name in wheel.namelist()
                if name.endswith(".dist-info/RECORD")
            )
        self.assertIn("securesystemslib/_internal/__init__.py,,0", record.splitlines())
        lock_entry = next(
            dependency
            for dependency in lock["dependencies"]
            if dependency["name"] == "securesystemslib"
        )
        self.assertEqual(
            "add-empty-package-marker:securesystemslib/_internal/__init__.py",
            lock_entry["transformation"],
        )

        with tempfile.TemporaryDirectory() as temporary:
            application = Path(temporary) / "x86qw.pyz"
            application.write_bytes(payload)
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import sys; sys.path.insert(0, sys.argv[1]); "
                    "from tuf.ngclient import Updater; "
                    "from securesystemslib.dsse import Envelope; "
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

    def test_builder_rejects_a_valid_root_with_unpinned_bytes(self) -> None:
        original_read = builder.read_regular_file
        root_path = ROOT / "maintenance/trust/root.json"
        changed_root = root_path.read_bytes().rstrip() + b" \n"

        def substitute(path: Path, *args: object, **kwargs: object) -> bytes:
            if path == root_path:
                return changed_root
            return original_read(path, *args, **kwargs)

        with mock.patch.object(builder, "read_regular_file", side_effect=substitute):
            with self.assertRaisesRegex(ValueError, "root TUF|SHA-256|pin"):
                builder.zipapp_bytes("9.9.9")

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

    def test_builder_rejects_colliding_projected_license_names(self) -> None:
        wheel_stream = io.BytesIO()
        with zipfile.ZipFile(wheel_stream, "w") as archive:
            archive.writestr("demo/__init__.py", b"__version__ = '1.0.0'\n")
            archive.writestr(
                "demo-1.0.0.dist-info/licenses/LICENSE", b"first license\n",
            )
            archive.writestr(
                "demo-1.0.0.dist-info/licenses/docs/LICENSE", b"second license\n",
            )
        wheel = wheel_stream.getvalue()
        lock = {
            "format": 1,
            "project": "x86qw",
            "dependencies": [{
                "name": "demo",
                "version": "1.0.0",
                "filename": "demo-1.0.0-py3-none-any.whl",
                "sha256": hashlib.sha256(wheel).hexdigest(),
                "upstream_sha256": hashlib.sha256(wheel).hexdigest(),
                "transformation": "none",
                "license": "MIT",
                "source": "https://pypi.org/project/demo/",
                "package_prefixes": ["demo/"],
            }],
        }
        with mock.patch.object(builder, "runtime_dependency_lock", return_value=lock), \
             mock.patch.object(builder, "read_regular_file", return_value=wheel):
            with self.assertRaisesRegex(
                ValueError,
                r"membro runtime duplicado: _x86qw/licenses/dependencies/demo/LICENSE",
            ):
                builder.runtime_dependency_members()


if __name__ == "__main__":
    unittest.main()
