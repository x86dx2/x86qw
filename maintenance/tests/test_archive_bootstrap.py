from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from maintenance.tools.build_installer_bundle import (
    ARCHIVE_BASE64_ASSIGNMENTS,
    ARCHIVE_SOURCE,
    POWERSHELL_BOOTSTRAP,
    PUBLIC_POWERSHELL_BOOTSTRAP,
    PUBLIC_SHELL_BOOTSTRAP,
    SHELL_BOOTSTRAP,
    embedded_archive_source,
    validate_bootstrap_archive_source,
    zipapp_bytes,
)
ROOT = Path(__file__).resolve().parents[2]
PUBLISHED_VERSION = "0.7.1"
PUBLISHED_BUNDLE = (
    ROOT / "dist/installer/packages" / PUBLISHED_VERSION
    / f"x86qw-installer-{PUBLISHED_VERSION}.zip"
)
PUBLISHED_SHA256 = "a0946ffcc8a4e1181dbc55ea08caf54691b18b12e901d12069eb2064b38c0d80"


class ArchiveBootstrapTests(unittest.TestCase):
    def test_bootstraps_are_synchronized_with_the_canonical_archive_source(self):
        validate_bootstrap_archive_source()
        expected = ARCHIVE_SOURCE.read_bytes()
        for canonical, public in (
            (SHELL_BOOTSTRAP, PUBLIC_SHELL_BOOTSTRAP),
            (POWERSHELL_BOOTSTRAP, PUBLIC_POWERSHELL_BOOTSTRAP),
        ):
            with self.subTest(bootstrap=canonical):
                self.assertEqual(canonical.read_bytes(), public.read_bytes())
                self.assertEqual(
                    expected,
                    embedded_archive_source(
                        canonical, ARCHIVE_BASE64_ASSIGNMENTS[canonical],
                    ),
                )

    def test_bootstraps_do_not_delegate_archive_extraction(self):
        for path in (
            SHELL_BOOTSTRAP,
            PUBLIC_SHELL_BOOTSTRAP,
            POWERSHELL_BOOTSTRAP,
            PUBLIC_POWERSHELL_BOOTSTRAP,
        ):
            with self.subTest(bootstrap=path):
                source = path.read_text(encoding="utf-8")
                self.assertNotIn("unzip", source.casefold())
                self.assertNotIn("Expand-Archive", source)
                self.assertIn("x86qw_runtime.io.archive", source)
                self.assertIn("--bundle-version", source)
                self.assertIn("--required", source)
                self.assertEqual(2, source.count("--executable"))
                self.assertIn("x86qw.sh", source)
                self.assertIn("dist/installer/bin/manager.py", source)

    def test_zipapp_embeds_the_canonical_archive_package_byte_for_byte(self):
        with zipfile.ZipFile(io.BytesIO(zipapp_bytes("9.9.9"))) as archive:
            self.assertEqual(
                ARCHIVE_SOURCE.read_bytes(),
                archive.read("x86qw_runtime/io/archive.py"),
            )
            self.assertIn("x86qw_runtime/__init__.py", archive.namelist())
            self.assertIn("x86qw_runtime/io/__init__.py", archive.namelist())

    def test_real_published_071_bundle_remains_extractable_without_mutation(self):
        self.assertEqual(PUBLISHED_SHA256, hashlib.sha256(PUBLISHED_BUNDLE.read_bytes()).hexdigest())
        prefix = f"x86qw-installer-{PUBLISHED_VERSION}"
        required = f"x86qw-installer-{PUBLISHED_VERSION}/x86qw.pyz"
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "extracted"
            completed = subprocess.run(
                [
                    sys.executable, "-m", "x86qw_runtime.io.archive",
                    str(PUBLISHED_BUNDLE), str(destination),
                    "--bundle-version", PUBLISHED_VERSION,
                    "--required", required,
                    "--executable", f"{prefix}/x86qw.sh",
                    "--executable", f"{prefix}/dist/installer/bin/manager.py",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertTrue((destination / required).is_file())
            if os.name != "nt":
                self.assertEqual(
                    0o755,
                    stat.S_IMODE((destination / prefix / "x86qw.sh").stat().st_mode),
                )
                self.assertEqual(
                    0o755,
                    stat.S_IMODE((
                        destination / prefix / "dist/installer/bin/manager.py"
                    ).stat().st_mode),
                )
        self.assertEqual(PUBLISHED_SHA256, hashlib.sha256(PUBLISHED_BUNDLE.read_bytes()).hexdigest())

    @unittest.skipIf(os.name == "nt", "bootstrap Unix e exercitado nos runners POSIX")
    def test_unix_bootstrap_preserves_unicode_arguments_and_installer_exit(self):
        version = "9.9.9"
        prefix = f"x86qw-installer-{version}"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "ambiente com espacos e Unicode ç"
            root.mkdir()
            arguments = root / "argumentos.json"
            bundle = root / f"x86qw-installer-{version}.zip"
            application = (
                "import json, os, pathlib, sys\n"
                "pathlib.Path(os.environ['X86QW_TEST_ARGUMENTS']).write_text("
                "json.dumps(sys.argv[1:], ensure_ascii=False), encoding='utf-8')\n"
                "raise SystemExit(23)\n"
            )
            self._write_installer_bundle(bundle, version, application)
            bootstrap, binaries = self._prepare_unix_bootstrap(root, version, bundle)
            target = root / "Jogos" / "Usuarios com espaços" / "ação"
            completed = subprocess.run(
                [str(bootstrap), "--target", str(target), "valor com espaços", "çãõ"],
                env={
                    "PATH": os.fspath(binaries),
                    "TMPDIR": os.fspath(root),
                    "X86QW_TEST_ARGUMENTS": os.fspath(arguments),
                    "X86QW_TEST_BUNDLE": os.fspath(bundle),
                    "PYTHONIOENCODING": "utf-8",
                },
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(23, completed.returncode, completed.stderr)
            self.assertEqual(
                ["--online-only", "--target", str(target), "valor com espaços", "çãõ"],
                json.loads(arguments.read_text(encoding="utf-8")),
            )

    @unittest.skipIf(os.name == "nt", "bootstrap Unix e exercitado nos runners POSIX")
    def test_invalid_archive_never_executes_its_zipapp(self):
        version = "9.9.8"
        prefix = f"x86qw-installer-{version}"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sentinel = root / "zipapp-executed"
            bundle = root / f"x86qw-installer-{version}.zip"
            application = (
                "import os, pathlib\n"
                "pathlib.Path(os.environ['X86QW_TEST_SENTINEL']).write_text('executed')\n"
            )
            self._write_installer_bundle(
                bundle,
                version,
                application,
                extra_member=(
                    f"{prefix}/unexpected.txt",
                    b"must be rejected by the exact bundle contract",
                ),
            )
            bootstrap, binaries = self._prepare_unix_bootstrap(root, version, bundle)
            completed = subprocess.run(
                [str(bootstrap)],
                env={
                    "PATH": os.fspath(binaries),
                    "TMPDIR": os.fspath(root),
                    "X86QW_TEST_BUNDLE": os.fspath(bundle),
                    "X86QW_TEST_SENTINEL": os.fspath(sentinel),
                    "PYTHONIOENCODING": "utf-8",
                },
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(0, completed.returncode)
            self.assertFalse(sentinel.exists())

    @unittest.skipUnless(
        shutil.which("pwsh") or shutil.which("powershell"),
        "PowerShell indisponivel neste runner",
    )
    def test_powershell_bootstrap_preserves_unicode_long_arguments_and_exit_code(self):
        version = "9.9.7"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "ambiente com espacos e Unicode ç"
            root.mkdir()
            arguments = root / "argumentos.json"
            bundle = root / f"x86qw-installer-{version}.zip"
            application = (
                "import json, os, pathlib, sys\n"
                "pathlib.Path(os.environ['X86QW_TEST_ARGUMENTS']).write_text("
                "json.dumps(sys.argv[1:], ensure_ascii=False), encoding='utf-8')\n"
                "raise SystemExit(23)\n"
            )
            self._write_installer_bundle(bundle, version, application)
            bootstrap, runner = self._prepare_powershell_bootstrap(root, version, bundle)
            target = root / "Jogos" / "Usuarios com espaços" / "ação"
            forwarded = [
                "--target", str(target), "um", "dois", "três", "quatro", "cinco",
                "seis", "sete", "oito", "nove", "dez", "valor com espaços", "çãõ",
            ]
            completed = subprocess.run(
                [
                    self._powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(runner), str(bootstrap), *forwarded,
                ],
                env={
                    **os.environ,
                    "X86QW_TEST_ARGUMENTS": os.fspath(arguments),
                    "X86QW_TEST_BUNDLE": os.fspath(bundle),
                    "PYTHONIOENCODING": "utf-8",
                },
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(23, completed.returncode, completed.stderr)
            self.assertEqual(
                ["--online-only", *forwarded],
                json.loads(arguments.read_text(encoding="utf-8")),
            )

    @unittest.skipUnless(
        shutil.which("pwsh") or shutil.which("powershell"),
        "PowerShell indisponivel neste runner",
    )
    def test_powershell_bootstrap_never_executes_a_hostile_bundle(self):
        version = "9.9.6"
        prefix = f"x86qw-installer-{version}"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "hostil Unicode ç"
            root.mkdir()
            sentinel = root / "zipapp-executed"
            bundle = root / f"x86qw-installer-{version}.zip"
            application = (
                "import os, pathlib\n"
                "pathlib.Path(os.environ['X86QW_TEST_SENTINEL']).write_text("
                "'executed', encoding='utf-8')\n"
            )
            self._write_installer_bundle(
                bundle,
                version,
                application,
                extra_member=(
                    f"{prefix}/unexpected.txt",
                    b"must be rejected before the zipapp is executed",
                ),
            )
            bootstrap, runner = self._prepare_powershell_bootstrap(root, version, bundle)
            completed = subprocess.run(
                [
                    self._powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(runner), str(bootstrap), "--target", str(root / "jogo"),
                ],
                env={
                    **os.environ,
                    "X86QW_TEST_BUNDLE": os.fspath(bundle),
                    "X86QW_TEST_SENTINEL": os.fspath(sentinel),
                    "PYTHONIOENCODING": "utf-8",
                },
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertNotEqual(0, completed.returncode)
            self.assertFalse(sentinel.exists(), completed.stderr)

    def _write_installer_bundle(
        self,
        bundle: Path,
        version: str,
        application: str,
        *,
        extra_member: tuple[str, bytes] | None = None,
    ) -> None:
        prefix = f"x86qw-installer-{version}"
        identity = {"format": 1, "project": "x86qw", "version": version}
        encoded_identity = json.dumps(identity, sort_keys=True).encode("utf-8")
        zipapp = io.BytesIO()
        with zipfile.ZipFile(zipapp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("__main__.py", application)
            archive.writestr("_x86qw/installer.json", encoded_identity)
        with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(f"{prefix}/installer.json", encoded_identity)
            archive.writestr(f"{prefix}/x86qw.pyz", zipapp.getvalue())
            archive.writestr(f"{prefix}/VERSION", version + "\n")
            archive.writestr(f"{prefix}/x86qw.sh", "#!/bin/sh\n")
            archive.writestr(f"{prefix}/x86qw.cmd", "@echo off\r\n")
            archive.writestr(
                f"{prefix}/dist/installer/bin/manager.py",
                "#!/usr/bin/env python3\n",
            )
            archive.writestr(f"{prefix}/_x86qw/installer.json", encoded_identity)
            if extra_member is not None:
                archive.writestr(*extra_member)

    def _prepare_unix_bootstrap(
        self, root: Path, version: str, bundle: Path,
    ) -> tuple[Path, Path]:
        source = SHELL_BOOTSTRAP.read_text(encoding="utf-8")
        source = source.replace(
            f'INSTALLER_VERSION="{PUBLISHED_VERSION}"',
            f'INSTALLER_VERSION="{version}"',
            1,
        ).replace(
            f'INSTALLER_SHA256="{PUBLISHED_SHA256}"',
            f'INSTALLER_SHA256="{hashlib.sha256(bundle.read_bytes()).hexdigest()}"',
            1,
        ).replace(
            'INSTALLER_SIZE="157113"',
            f'INSTALLER_SIZE="{bundle.stat().st_size}"',
            1,
        )
        bootstrap = root / "install.sh"
        bootstrap.write_text(source, encoding="utf-8")
        bootstrap.chmod(0o755)
        binaries = root / "bin"
        binaries.mkdir()
        (binaries / "python3").symlink_to(sys.executable)
        for command in ("mktemp", "rm"):
            executable = shutil.which(command)
            self.assertIsNotNone(executable, command)
            (binaries / command).symlink_to(executable)
        curl = binaries / "curl"
        curl.write_text(
            "#!/bin/sh\nexec /bin/cat \"$X86QW_TEST_BUNDLE\"\n",
            encoding="utf-8",
        )
        curl.chmod(0o755)
        return bootstrap, binaries

    @staticmethod
    def _powershell() -> str:
        executable = shutil.which("pwsh") or shutil.which("powershell")
        if executable is None:
            raise AssertionError("PowerShell indisponivel")
        return executable

    def _prepare_powershell_bootstrap(
        self, root: Path, version: str, bundle: Path,
    ) -> tuple[Path, Path]:
        escaped_python = sys.executable.replace("'", "''")
        source = POWERSHELL_BOOTSTRAP.read_text(encoding="utf-8")
        source = source.replace(
            f'$InstallerVersion = "{PUBLISHED_VERSION}"',
            f'$InstallerVersion = "{version}"',
            1,
        ).replace(
            f'$InstallerSha256 = "{PUBLISHED_SHA256}"',
            f'$InstallerSha256 = "{hashlib.sha256(bundle.read_bytes()).hexdigest()}"',
            1,
        ).replace(
            '$InstallerSize = "157113"',
            f'$InstallerSize = "{bundle.stat().st_size}"',
            1,
        ).replace(
            '[pscustomobject]@{ Command = "py"; Arguments = @("-3") },',
            f"[pscustomobject]@{{ Command = '{escaped_python}'; Arguments = @() }},",
            1,
        )
        downloader_start = source.index("$DownloaderSource = @'\n")
        downloader_end = source.index("\n'@", downloader_start)
        controlled_downloader = (
            "$DownloaderSource = @'\n"
            "import os, shutil, sys\n"
            "shutil.copyfile(os.environ['X86QW_TEST_BUNDLE'], sys.argv[1])\n"
            "'@"
        )
        source = (
            source[:downloader_start]
            + controlled_downloader
            + source[downloader_end + len("\n'@"):]
        )
        bootstrap = root / "install.ps1"
        bootstrap.write_text(source, encoding="utf-8")
        runner = root / "run-bootstrap.ps1"
        runner.write_text(
            "param(\n"
            "  [string]$Bootstrap,\n"
            "  [Parameter(ValueFromRemainingArguments=$true)][string[]]$Forward\n"
            ")\n"
            "& $Bootstrap @Forward\n"
            "exit $global:LASTEXITCODE\n",
            encoding="utf-8",
        )
        return bootstrap, runner


if __name__ == "__main__":
    unittest.main()
