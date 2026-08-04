from __future__ import annotations

import base64
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
from unittest import mock

from maintenance.tools import build_installer_bundle
from x86qw_runtime.io import atomic as atomic_io
from maintenance.tools.build_installer_bundle import (
    ARCHIVE_BASE64_ASSIGNMENTS,
    ARCHIVE_SOURCE,
    POWERSHELL_BOOTSTRAP,
    PUBLIC_POWERSHELL_BOOTSTRAP,
    PUBLIC_SHELL_BOOTSTRAP,
    SHELL_BOOTSTRAP,
    embedded_archive_source,
    update_public_bootstrap,
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
    def test_embedded_archive_assignment_accepts_lf_and_crlf(self):
        payload = b"canonical archive helper\n"
        encoded = base64.b64encode(payload).decode("ascii")
        for newline in (b"\n", b"\r\n"):
            with self.subTest(newline=newline), tempfile.TemporaryDirectory() as temporary:
                source = Path(temporary) / "bootstrap.sh"
                source.write_bytes(
                    f'ARCHIVE_HELPER_BASE64="{encoded}"'.encode("ascii") + newline
                )
                self.assertEqual(
                    payload,
                    embedded_archive_source(source, "ARCHIVE_HELPER_BASE64"),
                )

    def test_bootstrap_update_preserves_crlf(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "bootstrap.ps1"
            source.write_bytes(b'VALUE="old"\r\nOTHER="kept"\r\n')
            if os.name != "nt":
                source.chmod(0o750)
            update_public_bootstrap(source, {"VALUE": "new"})
            self.assertEqual(
                b'VALUE="new"\r\nOTHER="kept"\r\n',
                source.read_bytes(),
            )
            if os.name != "nt":
                self.assertEqual(0o750, stat.S_IMODE(source.stat().st_mode))

    def test_bootstrap_update_does_not_follow_a_concurrent_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "bootstrap.sh"
            victim = root / "personal.sh"
            source.write_bytes(b'VALUE="old"\n')
            victim.write_bytes(b'VALUE="personal"\n')
            real_read = build_installer_bundle.read_regular_file
            swapped = False

            def read_then_swap(path: Path, **kwargs: object) -> bytes:
                nonlocal swapped
                payload = real_read(path, **kwargs)
                if not swapped and path == source:
                    swapped = True
                    source.unlink()
                    try:
                        source.symlink_to(victim)
                    except OSError as error:
                        self.skipTest(f"symlink indisponível: {error}")
                return payload

            with mock.patch.object(
                build_installer_bundle,
                "read_regular_file",
                side_effect=read_then_swap,
            ), self.assertRaisesRegex(ValueError, "changed while reading"):
                update_public_bootstrap(source, {"VALUE": "new"})

            self.assertTrue(source.is_symlink())
            self.assertEqual(b'VALUE="personal"\n', victim.read_bytes())

    def test_bootstrap_atomic_replace_does_not_follow_a_late_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "bootstrap.sh"
            victim = root / "personal.sh"
            source.write_bytes(b'VALUE="old"\n')
            victim.write_bytes(b'VALUE="personal"\n')
            real_replace = build_installer_bundle.os.replace

            def plant_then_replace(staged: Path, destination: Path) -> None:
                Path(destination).unlink()
                try:
                    Path(destination).symlink_to(victim)
                except OSError as error:
                    self.skipTest(f"symlink indisponível: {error}")
                real_replace(staged, destination)

            with mock.patch.object(
                build_installer_bundle.os,
                "replace",
                side_effect=plant_then_replace,
            ):
                update_public_bootstrap(source, {"VALUE": "new"})

            self.assertFalse(source.is_symlink())
            self.assertEqual(b'VALUE="new"\n', source.read_bytes())
            self.assertEqual(b'VALUE="personal"\n', victim.read_bytes())

    def test_bootstrap_update_preserves_original_when_staging_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "bootstrap.sh"
            original = b'VALUE="old"\n'
            source.write_bytes(original)
            with mock.patch(
                "maintenance.tools.build_artifacts.StagedArtifact.seal",
                side_effect=OSError("simulated disk failure"),
            ), self.assertRaisesRegex(OSError, "simulated disk failure"):
                update_public_bootstrap(source, {"VALUE": "new"})
            self.assertEqual(original, source.read_bytes())
            self.assertEqual([], list(source.parent.glob(f".{source.name}.*")))

    def test_bootstrap_publication_uses_the_runtime_directory_barrier(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "bootstrap.sh"
            source.write_bytes(b'VALUE="old"\n')
            calls: list[Path] = []
            real_barrier = atomic_io.sync_directory

            def record(path: Path) -> None:
                calls.append(Path(path))
                real_barrier(path)

            with mock.patch.object(
                atomic_io,
                "sync_directory",
                side_effect=record,
            ):
                update_public_bootstrap(source, {"VALUE": "new"})

            self.assertEqual([source.parent, source.parent], calls)
            self.assertEqual(b'VALUE="new"\n', source.read_bytes())

    def test_bootstrap_runtime_barrier_failure_preserves_previous_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "bootstrap.sh"
            original = b'VALUE="old"\n'
            source.write_bytes(original)

            with mock.patch.object(
                atomic_io,
                "sync_directory",
                side_effect=OSError("simulated directory fsync failure"),
            ), self.assertRaisesRegex(OSError, "simulated directory fsync failure"):
                update_public_bootstrap(source, {"VALUE": "new"})

            self.assertEqual(original, source.read_bytes())
            self.assertEqual([], list(source.parent.glob(f".{source.name}.*")))

    def test_archive_helper_and_bootstraps_are_pinned_to_lf(self):
        paths = (
            "x86qw_runtime/io/archive.py",
            "dist/installer/bin/install.sh",
            "dist/installer/bin/install.ps1",
            "site/public/install.sh",
            "site/public/install.ps1",
        )
        result = subprocess.run(
            ["git", "check-attr", "text", "eol", "--", *paths],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        for path in paths:
            with self.subTest(path=path):
                self.assertIn(f"{path}: text: set\n", result.stdout)
                self.assertIn(f"{path}: eol: lf\n", result.stdout)

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
    def test_unix_bootstrap_scopes_archive_temporary_files_to_private_workdir(self):
        version = "9.9.5"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_temporary = root / "archive-tmp.txt"
            installer_temporary = root / "installer-tmp.txt"
            bundle = root / f"x86qw-installer-{version}.zip"
            application = (
                "import os, pathlib\n"
                "pathlib.Path(os.environ['X86QW_TEST_INSTALLER_TMP']).write_text("
                "os.environ.get('TMPDIR', ''), encoding='utf-8')\n"
            )
            self._write_installer_bundle(bundle, version, application)
            bootstrap, binaries = self._prepare_unix_bootstrap(root, version, bundle)
            python = binaries / "python3"
            python.unlink()
            python.write_text(
                "#!/bin/sh\n"
                "case \"$*\" in\n"
                "  *x86qw_runtime.io.archive*) "
                "printf '%s' \"${TMPDIR-}\" > \"$X86QW_TEST_ARCHIVE_TMP\" ;;\n"
                "esac\n"
                "exec \"$X86QW_TEST_REAL_PYTHON\" \"$@\"\n",
                encoding="utf-8",
            )
            python.chmod(0o755)
            completed = subprocess.run(
                [str(bootstrap)],
                env={
                    "PATH": os.fspath(binaries),
                    "TMPDIR": os.fspath(root),
                    "X86QW_TEST_ARCHIVE_TMP": os.fspath(archive_temporary),
                    "X86QW_TEST_INSTALLER_TMP": os.fspath(installer_temporary),
                    "X86QW_TEST_REAL_PYTHON": sys.executable,
                    "X86QW_TEST_BUNDLE": os.fspath(bundle),
                    "PYTHONIOENCODING": "utf-8",
                },
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            helper_temp = Path(archive_temporary.read_text(encoding="utf-8"))
            self.assertEqual(root, helper_temp.parent)
            self.assertTrue(helper_temp.name.startswith("x86qw-installer."))
            self.assertFalse(helper_temp.exists())
            self.assertEqual(
                os.fspath(root),
                installer_temporary.read_text(encoding="utf-8"),
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
