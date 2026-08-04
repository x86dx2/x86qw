from __future__ import annotations

import importlib
import importlib.util
import io
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from maintenance.tools.build_installer_bundle import zipapp_bytes


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "dist/installer/bin/python_runtime.py"
SPEC = importlib.util.spec_from_file_location("x86qw_python_runtime", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
python_runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(python_runtime)


class PythonRuntimeContractTests(unittest.TestCase):
    def test_legacy_module_reexports_canonical_runtime_contract(self):
        canonical = importlib.import_module("x86qw_runtime.platform.python_runtime")

        for name in (
            "UnsupportedPythonError", "version_is_supported",
            "require_supported_runtime", "validated_executable", "render_launcher",
        ):
            with self.subTest(name=name):
                self.assertIs(getattr(python_runtime, name), getattr(canonical, name))

    def test_zipapp_contains_canonical_python_runtime_and_facade(self):
        with zipfile.ZipFile(io.BytesIO(zipapp_bytes("9.9.9"))) as application:
            names = set(application.namelist())
        self.assertIn("x86qw_runtime/platform/python_runtime.py", names)
        self.assertIn("python_runtime.py", names)

    def test_minimum_runtime_contract_accepts_310_and_313_but_rejects_39(self):
        self.assertFalse(python_runtime.version_is_supported((3, 9, 19)))
        self.assertTrue(python_runtime.version_is_supported((3, 10, 0)))
        self.assertTrue(python_runtime.version_is_supported((3, 13, 5)))
        with self.assertRaisesRegex(
            python_runtime.UnsupportedPythonError,
            "Python 3.10 ou mais recente",
        ):
            python_runtime.require_supported_runtime((3, 9, 19))

    def test_unix_launcher_persists_a_quoted_absolute_unicode_path(self):
        template = "#!/bin/sh\npersisted_python=@X86QW_PYTHON@\n"
        runtime = "/tmp/x86 QW/Pythón 3/bin/python3"
        rendered = python_runtime.render_launcher(
            "x86qw.sh", template, runtime,
        )
        self.assertEqual(
            f"#!/bin/sh\npersisted_python={shlex.quote(python_runtime.validated_executable(runtime))}\n",
            rendered,
        )
        self.assertNotIn(python_runtime.LAUNCHER_PLACEHOLDER, rendered)

    def test_windows_launcher_persists_spaces_unicode_and_escapes_percent(self):
        template = '@echo off\nset "X86QW_PYTHON=@X86QW_PYTHON@"\n'
        rendered = python_runtime.render_launcher(
            "x86qw.cmd", template, r"C:\Usuários\x86%qw\Python 3\python.exe",
        )
        self.assertEqual(
            '@echo off\nset "X86QW_PYTHON=C:\\Usuários\\x86%%qw\\Python 3\\python.exe"\n',
            rendered,
        )
        self.assertNotIn(python_runtime.LAUNCHER_PLACEHOLDER, rendered)

    def test_launcher_template_requires_exactly_one_placeholder(self):
        for template in ("sem marcador", "@X86QW_PYTHON@ @X86QW_PYTHON@"):
            with self.subTest(template=template):
                with self.assertRaisesRegex(ValueError, "exatamente um marcador"):
                    python_runtime.render_launcher("x86qw.sh", template, "/usr/bin/python3")

    def test_executable_path_rejects_control_characters(self):
        with self.assertRaisesRegex(ValueError, "caracteres de controle"):
            python_runtime.validated_executable("/tmp/python\nmalicioso")

    def test_four_wrapper_families_use_the_canonical_version_probe(self):
        wrappers = (
            ROOT / "dist/installer/bin/install.sh",
            ROOT / "dist/installer/bin/install.ps1",
            ROOT / "dist/installer/bin/x86qw.sh",
            ROOT / "dist/installer/bin/x86qw.cmd",
        )
        for wrapper in wrappers:
            with self.subTest(wrapper=wrapper):
                source = wrapper.read_text(encoding="utf-8")
                probe_lines = [
                    line
                    for line in source.splitlines()
                    if "import sys; raise SystemExit" in line
                ]
                self.assertTrue(probe_lines)
                for line in probe_lines:
                    self.assertIn(python_runtime.VERSION_PROBE, line)

        for name in ("install.sh", "install.ps1"):
            with self.subTest(public_copy=name):
                self.assertEqual(
                    (ROOT / "dist/installer/bin" / name).read_bytes(),
                    (ROOT / "site/public" / name).read_bytes(),
                )

    def test_manager_rejects_python_39_before_internal_imports(self):
        manager = ROOT / "dist/installer/bin/manager.py"
        source = manager.read_text(encoding="utf-8")
        self.assertLess(
            source.index("python_runtime.require_supported_runtime()"),
            source.index('importlib.import_module("x86qw_runtime.session_control")'),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "sitecustomize.py").write_text(
                "import sys\nsys.version_info = (3, 9, 0, 'final', 0)\n",
                encoding="utf-8",
            )
            environment = dict(
                os.environ,
                PYTHONPATH=os.fspath(root),
                PYTHONDONTWRITEBYTECODE="1",
            )
            completed = subprocess.run(
                [sys.executable, os.fspath(manager), "--help"],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(2, completed.returncode)
        self.assertEqual("", completed.stdout)
        self.assertIn("Python 3.10 ou mais recente", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)

    def test_zipapp_rejects_python_39_before_loading_manager(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            application = root / "x86qw.pyz"
            application.write_bytes(zipapp_bytes("9.9.9"))
            (root / "sitecustomize.py").write_text(
                "import sys\nsys.version_info = (3, 9, 0, 'final', 0)\n",
                encoding="utf-8",
            )
            completed = subprocess.run(
                [sys.executable, os.fspath(application), "--version"],
                cwd=ROOT,
                env=dict(
                    os.environ,
                    PYTHONPATH=os.fspath(root),
                    PYTHONDONTWRITEBYTECODE="1",
                ),
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(2, completed.returncode)
        self.assertEqual("", completed.stdout)
        self.assertIn("Python 3.10 ou mais recente", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)


if __name__ == "__main__":
    unittest.main()
