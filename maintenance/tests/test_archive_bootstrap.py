from __future__ import annotations

import base64
import errno
import hashlib
import io
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

if os.name != "nt":
    import fcntl
    import pty
    import termios

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
CURRENT_VERSION = (ROOT / "dist/installer/VERSION").read_text(encoding="utf-8").strip()
CURRENT_BUNDLE = (
    ROOT / "dist/installer/packages" / CURRENT_VERSION
    / f"x86qw-installer-{CURRENT_VERSION}.zip"
)
CURRENT_SHA256 = hashlib.sha256(CURRENT_BUNDLE.read_bytes()).hexdigest()
HISTORICAL_071_VERSION = "0.7.1"
HISTORICAL_071_BUNDLE = (
    ROOT / "dist/installer/packages" / HISTORICAL_071_VERSION
    / f"x86qw-installer-{HISTORICAL_071_VERSION}.zip"
)
HISTORICAL_071_SHA256 = "a0946ffcc8a4e1181dbc55ea08caf54691b18b12e901d12069eb2064b38c0d80"


class ArchiveBootstrapTests(unittest.TestCase):
    def test_powershell_enables_utf8_before_rendering_unicode_interface(self):
        source = POWERSHELL_BOOTSTRAP.read_text(encoding="utf-8")

        self.assertLess(
            source.index("[Console]::OutputEncoding = $Utf8Encoding"),
            source.index("Preparando interface do instalador..."),
        )

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

    @unittest.skipIf(os.name == "nt", "bootstrap Unix e exercitado nos runners POSIX")
    def test_truncated_unix_bootstrap_prefixes_are_inert(self):
        source = SHELL_BOOTSTRAP.read_bytes()
        self.assertEqual(source, PUBLIC_SHELL_BOOTSTRAP.read_bytes())
        self.assertTrue(source.endswith(b'\nx86qw_install_main "$@"\n'))
        self.assertIn(b"x86qw_install_main() {", source)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sentinel = root / "side-effect"
            binaries = root / "bin"
            binaries.mkdir()
            for name in ("curl", "mktemp", "python3", "rm"):
                path = binaries / name
                path.write_text(
                    "#!/bin/sh\n"
                    f"printf '%s\\n' '{name}' >>\"$X86QW_TEST_SENTINEL\"\n"
                    "exit 0\n",
                    encoding="utf-8",
                )
                path.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = str(binaries)
            environment["TMPDIR"] = str(root)
            environment["X86QW_TEST_SENTINEL"] = str(sentinel)
            for percent in (25, 50, 75, 99):
                with self.subTest(percent=percent):
                    if sentinel.exists():
                        sentinel.unlink()
                    prefix = source[: max(1, len(source) * percent // 100)]
                    result = subprocess.run(
                        ["/bin/bash"],
                        input=prefix,
                        capture_output=True,
                        check=False,
                        env=environment,
                        timeout=10,
                    )
                    self.assertNotEqual(0, result.returncode)
                    self.assertFalse(sentinel.exists())

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
        self.assertEqual(
            HISTORICAL_071_SHA256,
            hashlib.sha256(HISTORICAL_071_BUNDLE.read_bytes()).hexdigest(),
        )
        prefix = f"x86qw-installer-{HISTORICAL_071_VERSION}"
        required = f"x86qw-installer-{HISTORICAL_071_VERSION}/x86qw.pyz"
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "extracted"
            completed = subprocess.run(
                [
                    sys.executable, "-m", "x86qw_runtime.io.archive",
                    str(HISTORICAL_071_BUNDLE), str(destination),
                    "--bundle-version", HISTORICAL_071_VERSION,
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
        self.assertEqual(
            HISTORICAL_071_SHA256,
            hashlib.sha256(HISTORICAL_071_BUNDLE.read_bytes()).hexdigest(),
        )

    @unittest.skipIf(os.name == "nt", "bootstrap Unix e exercitado nos runners POSIX")
    def test_unix_bootstrap_opens_with_three_stage_installer_interface(self):
        version = "9.9.4"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / f"x86qw-installer-{version}.zip"
            self._write_installer_bundle(bundle, version, "raise SystemExit(0)\n")
            bundle_size = bundle.stat().st_size
            bootstrap, binaries = self._prepare_unix_bootstrap(root, version, bundle)
            completed = subprocess.run(
                [str(bootstrap), "--help"],
                env={
                    "PATH": os.fspath(binaries),
                    "TMPDIR": os.fspath(root),
                    "COLUMNS": "100",
                    "X86QW_TEST_BUNDLE": os.fspath(bundle),
                    "PYTHONIOENCODING": "utf-8",
                },
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("\033[38;2;136;146;176mPreparando interface do instalador...", completed.stdout)
        self.assertIn(
            "\033[38;2;255;77;77m\033[1m"
            "                             ⢀⣤⣶⣶⣿⣿⣶⣶⣤⡀",
            completed.stdout,
        )
        self.assertNotIn("Q U A K E W O R L D", completed.stdout)
        self.assertIn(
            "\033[38;2;90;100;128m"
            "                                  qw.x86.com.br | instalador 9.9.4",
            completed.stdout,
        )
        self.assertLess(
            completed.stdout.index("⢀⣤⣶⣶⣿⣿⣶⣶⣤⡀"),
            completed.stdout.index("Preparando interface do instalador..."),
        )
        self.assertNotIn("[X] Instalador x86QW", completed.stdout)
        self.assertNotIn("Cinco jogos. Um menu. Uma partida.", completed.stdout)
        self.assertIn("Plano de instalação", completed.stdout)
        self.assertIn("Sistema:", completed.stdout)
        self.assertIn("Método de instalação:", completed.stdout)
        self.assertIn(f"Versão solicitada:\033[0m {version}", completed.stdout)
        self.assertIn("[1/3] Preparando ambiente", completed.stdout)
        self.assertIn("[2/3] Instalando x86QW", completed.stdout)
        self.assertIn("[3/3] Finalizando configuração", completed.stdout)
        self.assertLess(
            completed.stdout.index("qw.x86.com.br | instalador"),
            completed.stdout.index("baixando instalador"),
        )
        self.assertIn(
            f"Instalador x86QW {version}  Baixado  {bundle_size}B/{bundle_size}B",
            completed.stdout,
        )
        self.assertIn("\033[38;2;0;229;204m✓\033[0m Instalador extraído e verificado", completed.stdout)
        self.assertIn("\033[38;2;90;100;128m·\033[0m Iniciando configuração", completed.stdout)

    @unittest.skipIf(os.name == "nt", "bootstrap Unix e exercitado nos runners POSIX")
    def test_unix_bootstrap_hides_native_curl_meter_like_powershell(self):
        version = "9.9.1"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / f"x86qw-installer-{version}.zip"
            self._write_installer_bundle(bundle, version, "raise SystemExit(0)\n")
            bootstrap, binaries = self._prepare_unix_bootstrap(root, version, bundle)
            curl = binaries / "curl"
            curl.write_text(
                "#!/bin/sh\n"
                "silent=0\n"
                "for argument in \"$@\"; do\n"
                "  [ \"$argument\" = \"--silent\" ] && silent=1\n"
                "done\n"
                "[ \"$silent\" = 1 ] || printf '%s\\n' 'CURL_NATIVE_PROGRESS' >&2\n"
                "exec /bin/cat \"$X86QW_TEST_BUNDLE\"\n",
                encoding="utf-8",
            )
            curl.chmod(0o755)
            completed = subprocess.run(
                [str(bootstrap), "--help"],
                env={
                    "PATH": os.fspath(binaries),
                    "TMPDIR": os.fspath(root),
                    "COLUMNS": "100",
                    "X86QW_TEST_BUNDLE": os.fspath(bundle),
                    "PYTHONIOENCODING": "utf-8",
                },
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertNotIn("CURL_NATIVE_PROGRESS", completed.stderr)
        self.assertIn(f"x86QW: baixando instalador {version}...", completed.stdout)
        self.assertNotIn("(tentativa", completed.stdout)

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
    def test_powershell_bootstrap_opens_with_three_stage_installer_interface(self):
        version = "9.9.3"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / f"x86qw-installer-{version}.zip"
            self._write_installer_bundle(bundle, version, "raise SystemExit(0)\n")
            bundle_size = bundle.stat().st_size
            bootstrap, runner = self._prepare_powershell_bootstrap(root, version, bundle)
            completed = subprocess.run(
                [
                    self._powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(runner), str(bootstrap), "--help",
                ],
                env={
                    **os.environ,
                    "COLUMNS": "100",
                    "X86QW_TEST_BUNDLE": os.fspath(bundle),
                    "PYTHONIOENCODING": "utf-8",
                },
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("Preparando interface do instalador...", completed.stdout)
        self.assertIn("                             ⢀⣤⣶⣶⣿⣿⣶⣶⣤⡀", completed.stdout)
        self.assertNotIn("Q U A K E W O R L D", completed.stdout)
        self.assertIn(
            "                                  qw.x86.com.br | instalador 9.9.3",
            completed.stdout,
        )
        self.assertNotIn("[X] Instalador x86QW", completed.stdout)
        self.assertNotIn("Cinco jogos. Um menu. Uma partida.", completed.stdout)
        self.assertIn("Plano de instalação", completed.stdout)
        expected_os = (
            "windows" if os.name == "nt"
            else "macos" if sys.platform == "darwin"
            else "linux"
        )
        self.assertIn(f"Sistema detectado: {expected_os}", completed.stdout)
        self.assertIn(f"Sistema: {expected_os}", completed.stdout)
        self.assertIn("Método de instalação:", completed.stdout)
        self.assertIn(f"Versão solicitada: {version}", completed.stdout)
        self.assertIn("[1/3] Preparando ambiente", completed.stdout)
        self.assertIn("[2/3] Instalando x86QW", completed.stdout)
        self.assertIn("[3/3] Finalizando configuração", completed.stdout)
        self.assertLess(
            completed.stdout.index("qw.x86.com.br | instalador"),
            completed.stdout.index("baixando instalador"),
        )
        self.assertIn(
            f"Instalador x86QW {version}  Baixado  {bundle_size}B/{bundle_size}B",
            completed.stdout,
        )
        self.assertIn("✓ Instalador extraído e verificado", completed.stdout)
        self.assertIn("· Iniciando configuração", completed.stdout)

    @unittest.skipIf(os.name == "nt", "paridade POSIX requer pseudo-terminal")
    @unittest.skipUnless(
        shutil.which("pwsh") or shutil.which("powershell"),
        "PowerShell indisponivel neste runner",
    )
    def test_powershell_bootstrap_matches_shell_host_and_key_value_palette(self):
        version = "9.9.2"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / f"x86qw-installer-{version}.zip"
            self._write_installer_bundle(bundle, version, "raise SystemExit(0)\n")
            bootstrap, runner = self._prepare_powershell_bootstrap(
                root, version, bundle, force_color=True,
            )
            returncode, output = self._run_with_terminal(
                [
                    self._powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(runner), str(bootstrap), "--help",
                ],
                {
                    **os.environ,
                    "COLUMNS": "100",
                    "TERM": "xterm-256color",
                    "X86QW_TEST_BUNDLE": os.fspath(bundle),
                    "PYTHONIOENCODING": "utf-8",
                },
            )

        expected_os = "macos" if sys.platform == "darwin" else "linux"
        self.assertEqual(0, returncode, output)
        self.assertIn(
            f"\033[38;2;0;229;204m✓\033[0m Sistema detectado: {expected_os}",
            output,
        )
        self.assertIn(
            f"\033[38;2;90;100;128mSistema:\033[0m {expected_os}",
            output,
        )
        self.assertIn(
            "\033[38;2;90;100;128mMétodo de instalação:\033[0m pacote verificado",
            output,
        )

    @unittest.skipIf(os.name == "nt", "geometria PTY POSIX requer ioctl")
    @unittest.skipUnless(
        shutil.which("pwsh") or shutil.which("powershell"),
        "PowerShell indisponivel neste runner",
    )
    def test_powershell_banner_uses_live_terminal_width_instead_of_stale_columns(self):
        version = "9.9.2"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / f"x86qw-installer-{version}.zip"
            self._write_installer_bundle(bundle, version, "raise SystemExit(0)\n")
            bootstrap, runner = self._prepare_powershell_bootstrap(root, version, bundle)
            returncode, output = self._run_with_terminal(
                [
                    self._powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(runner), str(bootstrap), "--help",
                ],
                {
                    **os.environ,
                    "COLUMNS": "80",
                    "TERM": "xterm-256color",
                    "X86QW_TEST_BUNDLE": os.fspath(bundle),
                    "PYTHONIOENCODING": "utf-8",
                },
                columns=132,
            )

        plain = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", output)
        plain = plain.replace("\x1b=", "").replace("\x1b>", "").replace("\r", "")
        first_logo_line = next(line for line in plain.splitlines() if "⣀⣤⣶⣶" in line)
        expected_padding = ((132 - 78) // 2) + 18
        self.assertEqual(0, returncode, output)
        self.assertEqual(expected_padding, len(first_logo_line) - len(first_logo_line.lstrip()))

    @unittest.skipIf(os.name == "nt", "geometria PTY POSIX requer ioctl")
    @unittest.skipUnless(
        shutil.which("pwsh") or shutil.which("powershell"),
        "PowerShell indisponivel neste runner",
    )
    def test_powershell_banner_preserves_every_logo_column(self):
        version = "9.9.2"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / f"x86qw-installer-{version}.zip"
            self._write_installer_bundle(bundle, version, "raise SystemExit(0)\n")
            bootstrap, runner = self._prepare_powershell_bootstrap(root, version, bundle)
            _returncode, output = self._run_with_terminal(
                [
                    self._powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(runner), str(bootstrap), "--help",
                ],
                {
                    **os.environ,
                    "COLUMNS": "80",
                    "TERM": "xterm-256color",
                    "X86QW_TEST_BUNDLE": os.fspath(bundle),
                    "PYTHONIOENCODING": "utf-8",
                },
                columns=132,
            )

        plain = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", output)
        lines = plain.replace("\x1b=", "").replace("\x1b>", "").replace("\r", "").splitlines()
        start = next(index for index, line in enumerate(lines) if "⢀⣤⣶⣶" in line)
        end = next(index for index, line in enumerate(lines[start:], start) if "⠘⠿⠿⠿⠿⠆" in line)
        outer_padding = (132 - 78) // 2
        logo = [line[outer_padding:] for line in lines[start:end + 1]]
        geometry = tuple(
            (len(line) - len(line.lstrip()), len(line)) for line in logo
        )

        self.assertEqual(
            (
                (18, 78), (17, 78), (4, 77), (5, 76), (6, 76),
                (5, 75), (4, 74), (2, 73), (2, 73), (48, 54),
            ),
            geometry,
        )

    @unittest.skipIf(os.name == "nt", "geometria PTY POSIX requer ioctl")
    def test_unix_banner_uses_live_terminal_width_instead_of_stale_columns(self):
        version = "9.9.2"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / f"x86qw-installer-{version}.zip"
            self._write_installer_bundle(bundle, version, "raise SystemExit(0)\n")
            bootstrap, binaries = self._prepare_unix_bootstrap(root, version, bundle)
            returncode, output = self._run_with_terminal(
                [str(bootstrap), "--help"],
                {
                    **os.environ,
                    "PATH": os.fspath(binaries),
                    "COLUMNS": "80",
                    "TERM": "xterm-256color",
                    "TMPDIR": os.fspath(root),
                    "X86QW_TEST_BUNDLE": os.fspath(bundle),
                    "PYTHONIOENCODING": "utf-8",
                },
                columns=132,
            )

        plain = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", output).replace("\r", "")
        first_logo_line = next(line for line in plain.splitlines() if "⢀⣤⣶⣶" in line)
        expected_padding = ((132 - 78) // 2) + 18
        self.assertEqual(0, returncode, output)
        self.assertEqual(expected_padding, len(first_logo_line) - len(first_logo_line.lstrip()))

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
            self.assertNotIn("Write-Error:", completed.stderr)
            self.assertNotIn("Line |", completed.stderr)
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
            archive.writestr("_x86qw/LICENSE", (ROOT / "LICENSE").read_bytes())
            archive.writestr("_x86qw/NOTICE", (ROOT / "NOTICE").read_bytes())
        with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(f"{prefix}/installer.json", encoded_identity)
            archive.writestr(f"{prefix}/x86qw.pyz", zipapp.getvalue())
            archive.writestr(f"{prefix}/VERSION", version + "\n")
            archive.writestr(f"{prefix}/LICENSE", (ROOT / "LICENSE").read_bytes())
            archive.writestr(f"{prefix}/NOTICE", (ROOT / "NOTICE").read_bytes())
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
            f'INSTALLER_VERSION="{CURRENT_VERSION}"',
            f'INSTALLER_VERSION="{version}"',
            1,
        ).replace(
            f'INSTALLER_SHA256="{CURRENT_SHA256}"',
            f'INSTALLER_SHA256="{hashlib.sha256(bundle.read_bytes()).hexdigest()}"',
            1,
        ).replace(
            f'INSTALLER_SIZE="{CURRENT_BUNDLE.stat().st_size}"',
            f'INSTALLER_SIZE="{bundle.stat().st_size}"',
            1,
        )
        bootstrap = root / "install.sh"
        bootstrap.write_text(source, encoding="utf-8")
        bootstrap.chmod(0o755)
        binaries = root / "bin"
        binaries.mkdir()
        (binaries / "python3").symlink_to(sys.executable)
        for command in ("mktemp", "rm", "stty"):
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

    @staticmethod
    def _run_with_terminal(
        arguments: list[str], environment: dict[str, str], *, columns: int | None = None,
    ) -> tuple[int, str]:
        master, slave = pty.openpty()
        if columns is not None:
            fcntl.ioctl(
                slave,
                termios.TIOCSWINSZ,
                struct.pack("HHHH", 24, columns, 0, 0),
            )

        def attach_terminal() -> None:
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

        try:
            completed = subprocess.Popen(
                arguments,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=slave,
                stderr=slave,
                close_fds=True,
                preexec_fn=attach_terminal,
            )
        finally:
            os.close(slave)

        chunks: list[bytes] = []
        try:
            while True:
                try:
                    chunk = os.read(master, 65536)
                except OSError as error:
                    if error.errno == errno.EIO:
                        break
                    raise
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            os.close(master)

        return completed.wait(timeout=30), b"".join(chunks).decode("utf-8", errors="replace")

    def _prepare_powershell_bootstrap(
        self, root: Path, version: str, bundle: Path, *, force_color: bool = False,
    ) -> tuple[Path, Path]:
        escaped_python = sys.executable.replace("'", "''")
        source = POWERSHELL_BOOTSTRAP.read_text(encoding="utf-8")
        source = source.replace(
            f'$InstallerVersion = "{CURRENT_VERSION}"',
            f'$InstallerVersion = "{version}"',
            1,
        ).replace(
            f'$InstallerSha256 = "{CURRENT_SHA256}"',
            f'$InstallerSha256 = "{hashlib.sha256(bundle.read_bytes()).hexdigest()}"',
            1,
        ).replace(
            f'$InstallerSize = "{CURRENT_BUNDLE.stat().st_size}"',
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
        if force_color:
            source = source.replace(
                "$UseColor = $Host.UI.SupportsVirtualTerminal -and "
                "-not [Console]::IsOutputRedirected",
                "$UseColor = $true",
                1,
            )
        bootstrap = root / "install.ps1"
        bootstrap.write_text(source, encoding="utf-8")
        runner = root / "run-bootstrap.ps1"
        force_ansi = (
            "if (Get-Variable -Name PSStyle -ErrorAction SilentlyContinue) {\n"
            "  $PSStyle.OutputRendering = 'Ansi'\n"
            "}\n"
            if force_color else ""
        )
        runner.write_text(
            "param(\n"
            "  [string]$Bootstrap,\n"
            "  [Parameter(ValueFromRemainingArguments=$true)][string[]]$Forward\n"
            ")\n"
            + force_ansi
            + "& $Bootstrap @Forward\n"
            + "exit $global:LASTEXITCODE\n",
            encoding="utf-8",
        )
        return bootstrap, runner


if __name__ == "__main__":
    unittest.main()
