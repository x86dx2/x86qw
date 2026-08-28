from __future__ import annotations

import ast
import json
import hashlib
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from pathlib import Path

from maintenance.tools import launcher_contract


ROOT = Path(__file__).resolve().parents[2]
CAPABILITIES = ROOT / "maintenance/inventory/capabilities.json"
PRODUCT = ROOT / "site/public/api/v1/product.json"
CURRENT_VERSION = (ROOT / "dist/installer/VERSION").read_text(encoding="utf-8").strip()
CURRENT_BUNDLE = (
    ROOT / "dist/installer/packages" / CURRENT_VERSION
    / f"x86qw-installer-{CURRENT_VERSION}.zip"
)
CURRENT_BUNDLE_SIZE = CURRENT_BUNDLE.stat().st_size
WINDOWS_POWERSHELL = shutil.which("powershell.exe") if os.name == "nt" else None
POWERSHELL_RUNTIMES = tuple(dict.fromkeys(
    runtime
    for runtime in (shutil.which("pwsh"), WINDOWS_POWERSHELL)
    if runtime is not None
))
POWERSHELL = POWERSHELL_RUNTIMES[0] if POWERSHELL_RUNTIMES else None


class LauncherContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._windows_shim_temporary = None
        cls.windows_python_shim = None
        if os.name != "nt":
            return

        cls._windows_shim_temporary = tempfile.TemporaryDirectory()
        shim_root = Path(cls._windows_shim_temporary.name)
        source = shim_root / "python-shim.cs"
        source.write_text(
            r'''using System;
using System.IO;
using System.Reflection;
using System.Text;

public static class X86QWPythonShim
{
    private static readonly Encoding Utf8 = new UTF8Encoding(false);

    private static string Identity()
    {
        return Path.GetFileNameWithoutExtension(Assembly.GetExecutingAssembly().Location);
    }

    private static bool IsProbe(string[] arguments)
    {
        foreach (string argument in arguments)
        {
            if (argument == "-c")
            {
                return true;
            }
        }
        return false;
    }

    private static bool IsIncompatible(string identity)
    {
        string configured = Environment.GetEnvironmentVariable("X86QW_INCOMPATIBLE_SHIMS");
        if (String.IsNullOrEmpty(configured))
        {
            return false;
        }
        foreach (string candidate in configured.Split(';'))
        {
            if (String.Equals(candidate.Trim(), identity, StringComparison.OrdinalIgnoreCase))
            {
                return true;
            }
        }
        return false;
    }

    private static void LogInvocation(string identity, string phase)
    {
        string path = Environment.GetEnvironmentVariable("X86QW_PYTHON_LOG");
        if (!String.IsNullOrEmpty(path))
        {
            File.AppendAllText(path, identity + "|" + phase + Environment.NewLine, Utf8);
        }
    }

    private static string JsonQuote(string value)
    {
        StringBuilder result = new StringBuilder();
        result.Append('"');
        foreach (char character in value)
        {
            switch (character)
            {
                case '"': result.Append("\\\""); break;
                case '\\': result.Append("\\\\"); break;
                case '\b': result.Append("\\b"); break;
                case '\f': result.Append("\\f"); break;
                case '\n': result.Append("\\n"); break;
                case '\r': result.Append("\\r"); break;
                case '\t': result.Append("\\t"); break;
                default:
                    if (character < 0x20)
                    {
                        result.Append("\\u");
                        result.Append(((int)character).ToString("x4"));
                    }
                    else
                    {
                        result.Append(character);
                    }
                    break;
            }
        }
        result.Append('"');
        return result.ToString();
    }

    private static void WriteForwardedArguments(string[] arguments)
    {
        string output = Environment.GetEnvironmentVariable("X86QW_STUB_OUTPUT");
        if (String.IsNullOrEmpty(output))
        {
            return;
        }

        int runtimeArgument = 0;
        if (arguments.Length > 0 && arguments[0] == "-3")
        {
            runtimeArgument = 1;
        }
        int firstForwarded = runtimeArgument + 1;
        StringBuilder json = new StringBuilder("[");
        for (int index = firstForwarded; index < arguments.Length; index++)
        {
            if (index > firstForwarded)
            {
                json.Append(',');
            }
            json.Append(JsonQuote(arguments[index]));
        }
        json.Append(']');
        File.WriteAllText(output, json.ToString(), Utf8);
    }

    public static int Main(string[] arguments)
    {
        try
        {
            string identity = Identity();
            bool probe = IsProbe(arguments);
            LogInvocation(identity, probe ? "probe" : "launch");
            if (probe)
            {
                return IsIncompatible(identity) ? 1 : 0;
            }

            WriteForwardedArguments(arguments);
            int exitCode;
            if (Int32.TryParse(Environment.GetEnvironmentVariable("X86QW_STUB_EXIT"), out exitCode))
            {
                return exitCode;
            }
            return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine("x86QW native Python shim: " + error.Message);
            return 97;
        }
    }
}
''',
            encoding="utf-8",
        )
        compiler_script = shim_root / "compile-shim.ps1"
        compiler_script.write_text(
            r'''param(
  [string]$SourcePath,
  [string]$OutputPath
)
$ErrorActionPreference = "Stop"
$Source = Get-Content -LiteralPath $SourcePath -Raw
Add-Type -TypeDefinition $Source -Language CSharp `
  -OutputAssembly $OutputPath -OutputType ConsoleApplication
''',
            encoding="utf-8",
        )
        executable = shim_root / "x86qw-python-shim.exe"
        powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
        if powershell is None:
            raise AssertionError("PowerShell não está disponível para compilar o shim .exe nativo")
        completed = subprocess.run(
            [
                powershell, "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-File", str(compiler_script),
                str(source), str(executable),
            ],
            check=False, capture_output=True, text=True,
        )
        if completed.returncode != 0 or not executable.is_file():
            raise AssertionError(
                "Não foi possível compilar o shim .exe nativo para x86qw.cmd:\n"
                f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
            )
        cls.windows_python_shim = executable

    @classmethod
    def tearDownClass(cls):
        if cls._windows_shim_temporary is not None:
            cls._windows_shim_temporary.cleanup()
        super().tearDownClass()

    def install_windows_python_shim(self, path: Path) -> Path:
        self.assertEqual("nt", os.name)
        self.assertIsNotNone(self.windows_python_shim)
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.windows_python_shim, path)
        return path

    def render_windows_launcher(self, launcher: Path, runtime: Path) -> None:
        source = launcher.read_text(encoding="utf-8")
        self.assertEqual(1, source.count("@X86QW_PYTHON@"))
        launcher.write_text(
            source.replace("@X86QW_PYTHON@", os.fspath(runtime).replace("%", "%%")),
            encoding="utf-8",
        )

    def prepare_launcher(self, root: Path, name: str) -> tuple[Path, Path]:
        root.mkdir(parents=True)
        launcher = root / name
        shutil.copy2(ROOT / "dist/installer/bin" / name, launcher)
        if name.endswith(".sh"):
            launcher.chmod(0o755)
        app = root / ".x86qw/cli/x86qw.pyz"
        app.parent.mkdir(parents=True)
        app.write_text(
            """import json, os, pathlib, sys
pathlib.Path(os.environ['X86QW_STUB_OUTPUT']).write_text(json.dumps(sys.argv[1:]), encoding='utf-8')
raise SystemExit(int(os.environ.get('X86QW_STUB_EXIT', '0')))
""",
            encoding="utf-8",
        )
        return launcher, app

    def test_public_command_contract_matches_catalog_help_and_launchers(self):
        commands = json.loads(CAPABILITIES.read_text(encoding="utf-8"))["commands"]
        product_commands = json.loads(PRODUCT.read_text(encoding="utf-8"))["commands"]
        self.assertEqual(commands, product_commands)
        self.assertIn("changes", commands)
        self.assertIn("migrate", commands)
        help_result = subprocess.run(
            [sys.executable, str(ROOT / "dist/installer/bin/manager.py"), "--help"],
            check=True, capture_output=True, text=True,
        )
        shell = (ROOT / "dist/installer/bin/x86qw.sh").read_text(encoding="utf-8")
        batch = (ROOT / "dist/installer/bin/x86qw.cmd").read_text(encoding="utf-8")
        for command in commands:
            with self.subTest(command=command):
                self.assertIn(command, help_result.stdout)
                self.assertIn(command, shell)
                self.assertIn(command, batch)

    def test_launcher_dispatch_sets_match_the_canonical_command_contract(self):
        launcher_contract.validate_public_launcher_contract(ROOT)

    def test_launchers_document_non_mutating_maintenance_options(self):
        shell = (ROOT / "dist/installer/bin/x86qw.sh").read_text(encoding="utf-8")
        batch = (ROOT / "dist/installer/bin/x86qw.cmd").read_text(encoding="utf-8")
        for source in (shell, batch):
            self.assertIn("changes [--sync-gitignore]", source)
            self.assertIn("migrate [--dry-run]", source)
            self.assertIn("doctor [--bundle]", source)
            self.assertIn("profile [--backup|--restore]", source)
            self.assertIn("library [--add|--remove]", source)

    @unittest.skipIf(os.name == "nt", "launcher Unix é exercitado nos runners POSIX")
    def test_unix_launcher_forwards_repair_and_long_play_arguments_exactly(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "instalação com espaços"
            launcher, _ = self.prepare_launcher(root, "x86qw.sh")
            output = root / "arguments.json"
            environment = dict(os.environ, X86QW_STUB_OUTPUT=str(output))
            arguments = [
                "play", "ktx", "--mode", "duel", "--map", "dm6",
                "--bots", "2", "--bot-skill", "8", "--no-color",
            ]
            completed = subprocess.run(
                [str(launcher), *arguments], env=environment, check=False,
            )
            self.assertEqual(0, completed.returncode)
            self.assertEqual(
                [*arguments, "--target", str(root)],
                json.loads(output.read_text(encoding="utf-8")),
            )
            completed = subprocess.run(
                [str(launcher), "repair", "--dry-run"], env=environment, check=False,
            )
            self.assertEqual(0, completed.returncode)
            self.assertEqual(
                ["--online-only", "--installed-cli", "repair", str(root), "--dry-run"],
                json.loads(output.read_text(encoding="utf-8")),
            )
            completed = subprocess.run(
                [str(launcher), "status", "--no-color"], env=environment, check=False,
            )
            self.assertEqual(0, completed.returncode)
            self.assertEqual(
                ["status", "--no-color", "--target", str(root)],
                json.loads(output.read_text(encoding="utf-8")),
            )
            for arguments, expected in (
                (["help"], ["--version"]),
                (["play", "--help"], ["play", "--help", "--target", str(root)]),
                (["verify"], ["--online-only", "--installed-cli", "verify", str(root)]),
                (["doctor"], ["doctor", "--target", str(root)]),
                (["status", "--json"], ["status", "--json", "--target", str(root)]),
                (["ui", "--output", "/tmp/x86qw-ui.html"], [
                    "ui", "--output", "/tmp/x86qw-ui.html", "--target", str(root),
                ]),
                (["uninstall", "--help"], ["--online-only", "--installed-cli", "uninstall", str(root), "--help"]),
            ):
                with self.subTest(arguments=arguments):
                    completed = subprocess.run(
                        [str(launcher), *arguments], env=environment,
                        check=False, capture_output=True,
                    )
                    self.assertEqual(
                        0,
                        completed.returncode,
                        msg=(
                            f"stdout={completed.stdout!r}\n"
                            f"stderr={completed.stderr!r}\n"
                            f"launcher={launcher.read_text(encoding='utf-8')!r}"
                        ),
                    )
                    self.assertEqual(expected, json.loads(output.read_text(encoding="utf-8")))

    @unittest.skipIf(os.name == "nt", "launcher Unix é exercitado nos runners POSIX")
    def test_unix_launcher_opens_the_navigator_without_arguments(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "instalação com espaços"
            launcher, _ = self.prepare_launcher(root, "x86qw.sh")
            output = root / "arguments.json"
            environment = dict(os.environ, X86QW_STUB_OUTPUT=str(output))
            completed = subprocess.run([str(launcher)], env=environment, check=False)
            self.assertEqual(0, completed.returncode)
            self.assertEqual(
                ["menu", str(root)],
                json.loads(output.read_text(encoding="utf-8")),
            )

    @unittest.skipIf(os.name == "nt", "launcher Unix é exercitado nos runners POSIX")
    def test_unix_launcher_preserves_child_exit_codes_for_every_dispatch_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "instalação com espaços"
            launcher, _ = self.prepare_launcher(root, "x86qw.sh")
            output = root / "arguments.json"
            environment = dict(
                os.environ,
                X86QW_STUB_OUTPUT=str(output),
                X86QW_STUB_EXIT="23",
            )
            for arguments in (
                [],
                ["version"],
                ["play", "--help"],
                ["repair", "--dry-run"],
            ):
                with self.subTest(arguments=arguments):
                    completed = subprocess.run(
                        [str(launcher), *arguments],
                        env=environment,
                        check=False,
                        capture_output=True,
                    )
                    self.assertEqual(23, completed.returncode)

    @unittest.skipUnless(os.name == "nt", "cmd.exe é exercitado somente no runner Windows")
    def test_windows_launcher_forwards_more_than_nine_arguments_and_exit_code(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "instalação com espaços e Unicode ç"
            launcher, _ = self.prepare_launcher(root, "x86qw.cmd")
            runtime = self.install_windows_python_shim(
                root / "Runtime Python persistido com espaços ç" / "python-x86qw.exe"
            )
            self.render_windows_launcher(launcher, runtime)
            empty_path = root / "PATH vazio"
            empty_path.mkdir()
            output = root / "arguments.json"
            log = root / "python.log"
            environment = dict(
                os.environ,
                PATH=os.fspath(empty_path),
                X86QW_STUB_OUTPUT=os.fspath(output),
                X86QW_PYTHON_LOG=os.fspath(log),
            )
            arguments = [
                "play", "ktx", "--mode", "duel", "--map", "dm6",
                "--bots", "2", "--bot-skill", "8", "--no-color",
                "--name", "Jogador com espaços ç",
            ]
            completed = subprocess.run(
                [
                    os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "call",
                    str(launcher), *arguments,
                ],
                env=environment, check=False,
            )
            self.assertEqual(0, completed.returncode)
            received = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(arguments, received[:-2])
            self.assertEqual("--target", received[-2])
            self.assertEqual(root.resolve(), Path(received[-1]).resolve())
            self.assertEqual(
                ["python-x86qw|probe", "python-x86qw|launch"],
                log.read_text(encoding="utf-8").splitlines(),
            )

            environment["X86QW_STUB_EXIT"] = "23"
            completed = subprocess.run(
                [
                    os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "call",
                    str(launcher), "repair", "--dry-run",
                ],
                env=environment, check=False,
            )
            self.assertEqual(23, completed.returncode)
            received = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(["--online-only", "--installed-cli", "repair", "--dry-run"], received[:-1])
            self.assertEqual(root.resolve(), Path(received[-1]).resolve())

            completed = subprocess.run(
                [
                    os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "call",
                    str(launcher), "status", "--no-color",
                ],
                env=dict(environment, X86QW_STUB_EXIT="0"), check=False,
            )
            self.assertEqual(0, completed.returncode)
            received = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(["status", "--no-color"], received[:-2])
            self.assertEqual("--target", received[-2])
            self.assertEqual(root.resolve(), Path(received[-1]).resolve())

            completed = subprocess.run(
                [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "call", str(launcher)],
                env=dict(environment, X86QW_STUB_EXIT="0"), check=False,
            )
            self.assertEqual(0, completed.returncode)
            received = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("menu", received[0])
            self.assertEqual(root.resolve(), Path(received[1]).resolve())

            for arguments, prefix, target_at_end in (
                (["help"], ["--version"], False),
                (["play", "--help"], ["play", "--help", "--target"], True),
                (["verify"], ["--online-only", "--installed-cli", "verify"], True),
                (["doctor"], ["doctor", "--target"], True),
                (["status", "--json"], ["status", "--json", "--target"], True),
                (["ui"], ["ui", "--target"], True),
                (["uninstall", "--help"], ["--online-only", "--installed-cli", "uninstall", "--help"], True),
            ):
                with self.subTest(arguments=arguments):
                    completed = subprocess.run(
                        [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "call", str(launcher), *arguments],
                        env=dict(environment, X86QW_STUB_EXIT="0"), check=False,
                        capture_output=True,
                    )
                    self.assertEqual(
                        0,
                        completed.returncode,
                        msg=(
                            f"stdout={completed.stdout!r}\n"
                            f"stderr={completed.stderr!r}\n"
                            f"launcher={launcher.read_text(encoding='utf-8')!r}"
                        ),
                    )
                    received = json.loads(output.read_text(encoding="utf-8"))
                    if target_at_end:
                        self.assertEqual(prefix, received[:-1])
                        self.assertEqual(root.resolve(), Path(received[-1]).resolve())
                    else:
                        self.assertEqual(prefix, received)
                    if arguments[0] == "uninstall":
                        self.assertTrue(launcher.is_file())

    @unittest.skipUnless(os.name == "nt", "cmd.exe é exercitado somente no runner Windows")
    def test_windows_launcher_resolves_native_exe_candidates_and_precedence(self):
        cases = (
            ("only-py", ("py",), "py"),
            ("only-python3", ("python3",), "python3"),
            ("only-python", ("python",), "python"),
            ("all-candidates", ("py", "python3", "python"), "py"),
        )
        for label, available, expected in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "instalação com espaços"
                launcher, _ = self.prepare_launcher(root, "x86qw.cmd")
                self.render_windows_launcher(
                    launcher, root / "runtime persistido removido" / "python.exe"
                )
                binaries = root / "bin"
                binaries.mkdir()
                for command in available:
                    self.install_windows_python_shim(binaries / f"{command}.exe")

                output = root / "arguments.json"
                log = root / "python.log"
                arguments = [
                    "play", "ktx", "--mode", "duel", "--map", "dm6",
                    "--bots", "2", "--bot-skill", "8", "--no-color",
                ]
                environment = dict(
                    os.environ,
                    PATH=os.fspath(binaries),
                    X86QW_STUB_OUTPUT=os.fspath(output),
                    X86QW_PYTHON_LOG=os.fspath(log),
                )
                completed = subprocess.run(
                    [
                        os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "call",
                        str(launcher), *arguments,
                    ],
                    env=environment, check=False, capture_output=True, text=True,
                )
                self.assertEqual(0, completed.returncode, completed.stderr)
                received = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual(arguments, received[:-2])
                self.assertEqual("--target", received[-2])
                self.assertEqual(root.resolve(), Path(received[-1]).resolve())
                self.assertEqual(
                    [f"{expected}|probe", f"{expected}|launch"],
                    log.read_text(encoding="utf-8").splitlines(),
                )

    @unittest.skipUnless(os.name == "nt", "cmd.exe é exercitado somente no runner Windows")
    def test_windows_launcher_skips_incompatible_candidate_for_next_native_exe(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "instalação com espaços"
            launcher, _ = self.prepare_launcher(root, "x86qw.cmd")
            self.render_windows_launcher(
                launcher, root / "runtime persistido removido" / "python.exe"
            )
            binaries = root / "bin"
            binaries.mkdir()
            for command in ("py", "python3", "python"):
                self.install_windows_python_shim(binaries / f"{command}.exe")

            output = root / "arguments.json"
            log = root / "python.log"
            environment = dict(
                os.environ,
                PATH=os.fspath(binaries),
                X86QW_STUB_OUTPUT=os.fspath(output),
                X86QW_PYTHON_LOG=os.fspath(log),
                X86QW_INCOMPATIBLE_SHIMS="py",
            )
            completed = subprocess.run(
                [
                    os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "call",
                    str(launcher), "version",
                ],
                env=environment, check=False, capture_output=True, text=True,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertEqual(["--version"], json.loads(output.read_text(encoding="utf-8")))
            self.assertEqual(
                ["py|probe", "python3|probe", "python3|launch"],
                log.read_text(encoding="utf-8").splitlines(),
            )

    @unittest.skipUnless(os.name == "nt", "cmd.exe é exercitado somente no runner Windows")
    def test_windows_launcher_recovers_removed_or_incompatible_persisted_runtime(self):
        for state in ("removed", "incompatible"):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "instalação com espaços e Unicode ç"
                launcher, _ = self.prepare_launcher(root, "x86qw.cmd")
                persisted = root / "Python persistido ç" / "python-persistido.exe"
                if state == "incompatible":
                    self.install_windows_python_shim(persisted)
                self.render_windows_launcher(launcher, persisted)

                binaries = root / "bin"
                binaries.mkdir()
                self.install_windows_python_shim(binaries / "python3.exe")
                output = root / "arguments.json"
                log = root / "python.log"
                environment = dict(
                    os.environ,
                    PATH=os.fspath(binaries),
                    X86QW_STUB_OUTPUT=os.fspath(output),
                    X86QW_PYTHON_LOG=os.fspath(log),
                )
                if state == "incompatible":
                    environment["X86QW_INCOMPATIBLE_SHIMS"] = "python-persistido"

                completed = subprocess.run(
                    [
                        os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "call",
                        str(launcher), "verify",
                    ],
                    env=environment, check=False, capture_output=True, text=True,
                )
                self.assertEqual(0, completed.returncode, completed.stderr)
                received = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual(
                    ["--online-only", "--installed-cli", "verify"], received[:-1]
                )
                self.assertEqual(root.resolve(), Path(received[-1]).resolve())
                expected = ["python3|probe", "python3|launch"]
                if state == "incompatible":
                    expected.insert(0, "python-persistido|probe")
                self.assertEqual(expected, log.read_text(encoding="utf-8").splitlines())

    @unittest.skipIf(os.name == "nt", "bootstrap Unix é exercitado nos runners POSIX")
    def test_unix_launcher_falls_back_when_persisted_runtime_disappears(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "instalação com espaços"
            launcher, _ = self.prepare_launcher(root, "x86qw.sh")
            source = launcher.read_text(encoding="utf-8")
            self.assertEqual(1, source.count("@X86QW_PYTHON@"))
            launcher.write_text(
                source.replace("@X86QW_PYTHON@", shlex.quote(os.fspath(root / "removido/python3"))),
                encoding="utf-8",
            )
            output = root / "arguments.json"
            completed = subprocess.run(
                [str(launcher), "version"],
                env=dict(os.environ, X86QW_STUB_OUTPUT=str(output)),
                check=False,
            )
            self.assertEqual(0, completed.returncode)
            self.assertEqual(["--version"], json.loads(output.read_text(encoding="utf-8")))

    @unittest.skipIf(os.name == "nt", "bootstrap Unix é exercitado nos runners POSIX")
    def test_unix_bootstrap_resolves_python3_python_and_prefers_python3(self):
        bootstrap = ROOT / "dist/installer/bin/install.sh"
        source = bootstrap.read_text(encoding="utf-8")
        version = source.split('INSTALLER_VERSION="', 1)[1].split('"', 1)[0]
        bundle = ROOT / f"dist/installer/packages/{version}/x86qw-installer-{version}.zip"
        self.assertTrue(bundle.is_file(), bundle)

        for available in ("python3", "python", "both"):
            with self.subTest(available=available), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "ambiente com espaço e Unicode ç"
                binaries = root / "bin"
                binaries.mkdir(parents=True)
                log = root / "python.log"
                for command in ("python3", "python"):
                    supported = available == "both" or available == command
                    body = (
                        "#!/bin/sh\n"
                        f"printf '%s\\n' {shlex.quote(command)} >> \"$X86QW_PYTHON_LOG\"\n"
                    )
                    if supported:
                        body += f"exec {shlex.quote(sys.executable)} \"$@\"\n"
                    else:
                        body += "exit 1\n"
                    wrapper = binaries / command
                    wrapper.write_text(body, encoding="utf-8")
                    wrapper.chmod(0o755)

                curl = binaries / "curl"
                curl.write_text(
                    """#!/bin/sh
exec /bin/cat "$X86QW_TEST_BUNDLE"
""",
                    encoding="utf-8",
                )
                curl.chmod(0o755)
                for command in ("mktemp", "rm"):
                    executable = shutil.which(command)
                    self.assertIsNotNone(executable, command)
                    (binaries / command).symlink_to(executable)

                completed = subprocess.run(
                    [str(bootstrap), "--help"],
                    env={
                        "PATH": os.fspath(binaries),
                        "TMPDIR": os.fspath(root),
                        "X86QW_TEST_BUNDLE": os.fspath(bundle),
                        "X86QW_PYTHON_LOG": os.fspath(log),
                        "PYTHONIOENCODING": "utf-8",
                    },
                    check=False, capture_output=True, text=True,
                )
                self.assertEqual(0, completed.returncode, completed.stderr)
                self.assertIn("usage: x86qw", completed.stdout)
                used = log.read_text(encoding="utf-8").splitlines()
                expected = "python3" if available in {"python3", "both"} else "python"
                self.assertEqual(expected, used[-1])
                if available == "both":
                    self.assertEqual({"python3"}, set(used))

    @unittest.skipIf(os.name == "nt", "bootstrap Unix é exercitado nos runners POSIX")
    def test_unix_bootstrap_piped_through_bash_keeps_terminal_input(self):
        import pty
        import select
        import warnings

        for bootstrap in (
            ROOT / "dist/installer/bin/install.sh",
            ROOT / "site/public/install.sh",
        ):
            with self.subTest(bootstrap=bootstrap), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                binaries = root / "bin"
                binaries.mkdir()
                answer_file = root / "answer"
                expected_answer = os.fspath(root / "instalação escolhida")

                runtime = binaries / "python3"
                runtime.write_text(
                    "#!/bin/sh\n"
                    "case \"${1:-}\" in\n"
                    "  */x86qw.pyz)\n"
                    "    if IFS= read -r answer; then\n"
                    "      printf '%s' \"$answer\" > \"$X86QW_STDIN_RESULT\"\n"
                    "    else\n"
                    "      printf '<EOF>' > \"$X86QW_STDIN_RESULT\"\n"
                    "    fi\n"
                    "    exit 0\n"
                    "    ;;\n"
                    "esac\n"
                    f"exec {shlex.quote(sys.executable)} \"$@\"\n",
                    encoding="utf-8",
                )
                runtime.chmod(0o755)
                curl = binaries / "curl"
                curl.write_text(
                    "#!/bin/sh\nexec /bin/cat \"$X86QW_TEST_BUNDLE\"\n",
                    encoding="utf-8",
                )
                curl.chmod(0o755)
                for command in ("mktemp", "rm"):
                    executable = shutil.which(command)
                    self.assertIsNotNone(executable, command)
                    (binaries / command).symlink_to(executable)

                environment = {
                    "PATH": os.fspath(binaries),
                    "TMPDIR": os.fspath(root),
                    "X86QW_STDIN_RESULT": os.fspath(answer_file),
                    "X86QW_TEST_BUNDLE": os.fspath(CURRENT_BUNDLE),
                    "PYTHONIOENCODING": "utf-8",
                }
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", DeprecationWarning)
                    pid, descriptor = pty.fork()
                if pid == 0:
                    os.execve(
                        "/bin/bash",
                        [
                            "/bin/bash",
                            "-c",
                            f"/bin/cat {shlex.quote(os.fspath(bootstrap))} | /bin/bash",
                        ],
                        environment,
                    )

                status = None
                output = bytearray()
                try:
                    os.write(descriptor, f"{expected_answer}\n".encode())
                    deadline = time.monotonic() + 20
                    while time.monotonic() < deadline:
                        ready, _, _ = select.select([descriptor], [], [], 0.1)
                        if ready:
                            try:
                                output.extend(os.read(descriptor, 65536))
                            except OSError:
                                pass
                        finished, status = os.waitpid(pid, os.WNOHANG)
                        if finished:
                            break
                    else:
                        os.kill(pid, 9)
                        _, status = os.waitpid(pid, 0)
                        self.fail("bootstrap não terminou:\n" + output.decode(errors="replace"))
                finally:
                    os.close(descriptor)

                self.assertEqual(0, os.waitstatus_to_exitcode(status), output.decode(errors="replace"))
                self.assertEqual(expected_answer, answer_file.read_text(encoding="utf-8"))

    @unittest.skipIf(os.name == "nt", "bootstrap Unix é exercitado nos runners POSIX")
    def test_unix_bootstrap_rejects_python_39_before_download(self):
        for bootstrap in (
            ROOT / "dist/installer/bin/install.sh",
            ROOT / "site/public/install.sh",
        ):
            with self.subTest(bootstrap=bootstrap), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                binaries = root / "bin"
                binaries.mkdir()
                for command in ("python3", "python"):
                    wrapper = binaries / command
                    wrapper.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                    wrapper.chmod(0o755)
                marker = root / "side-effect-called"
                for command in ("curl", "mktemp"):
                    wrapper = binaries / command
                    wrapper.write_text(
                        '#!/bin/sh\nprintf called > "$X86QW_SIDE_EFFECT_MARKER"\nexit 1\n',
                        encoding="utf-8",
                    )
                    wrapper.chmod(0o755)
                completed = subprocess.run(
                    ["/bin/bash", str(bootstrap)],
                    env={
                        "PATH": os.fspath(binaries),
                        "TMPDIR": os.fspath(root / "must-not-exist"),
                        "X86QW_SIDE_EFFECT_MARKER": os.fspath(marker),
                    },
                    check=False, capture_output=True, text=True,
                )
                self.assertEqual(1, completed.returncode)
                self.assertIn("Python 3.10 ou mais recente", completed.stderr)
                self.assertFalse(marker.exists())
                self.assertFalse((root / "must-not-exist").exists())

    @unittest.skipIf(os.name == "nt", "bootstrap Unix é exercitado nos runners POSIX")
    def test_unix_bootstrap_preserves_installer_exit_code(self):
        for bootstrap in (
            ROOT / "dist/installer/bin/install.sh",
            ROOT / "site/public/install.sh",
        ):
            source = bootstrap.read_text(encoding="utf-8")
            version = source.split('INSTALLER_VERSION="', 1)[1].split('"', 1)[0]
            bundle = ROOT / f"dist/installer/packages/{version}/x86qw-installer-{version}.zip"
            self.assertTrue(bundle.is_file(), bundle)
            with self.subTest(bootstrap=bootstrap), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                binaries = root / "bin"
                binaries.mkdir()
                runtime = binaries / "python3"
                runtime.write_text(
                    "#!/bin/sh\n"
                    "if [ \"${1:-}\" = \"-c\" ]; then exit 0; fi\n"
                    f"if [ \"${{1:-}}\" = \"/dev/fd/3\" ]; then exec {shlex.quote(sys.executable)} \"$@\"; fi\n"
                    "exit 23\n",
                    encoding="utf-8",
                )
                runtime.chmod(0o755)
                curl = binaries / "curl"
                curl.write_text(
                    """#!/bin/sh
exec /bin/cat "$X86QW_TEST_BUNDLE"
""",
                    encoding="utf-8",
                )
                curl.chmod(0o755)
                for command in ("mktemp", "rm"):
                    executable = shutil.which(command)
                    self.assertIsNotNone(executable, command)
                    (binaries / command).symlink_to(executable)

                completed = subprocess.run(
                    ["/bin/bash", str(bootstrap), "--help"],
                    env={
                        "PATH": os.fspath(binaries),
                        "TMPDIR": os.fspath(root),
                        "X86QW_TEST_BUNDLE": os.fspath(bundle),
                    },
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(23, completed.returncode, completed.stderr)

    def test_unix_bootstrap_hashes_in_streaming_blocks(self):
        source = (ROOT / "dist/installer/bin/install.sh").read_text(encoding="utf-8")
        self.assertIn("sys.stdin.buffer.read(min(64 * 1024, expected_size - received + 1))", source)
        self.assertNotIn('open(sys.argv[1],"rb").read()', source)

    def test_unix_bootstrap_has_bounded_https_download_contract(self):
        source = (ROOT / "dist/installer/bin/install.sh").read_text(encoding="utf-8")
        for fragment in (
            f'INSTALLER_SIZE="{CURRENT_BUNDLE_SIZE}"',
            "DOWNLOAD_BUDGET_SECONDS=\"180\"",
            "DOWNLOAD_TRANSFER_SECONDS=\"120\"",
            "DOWNLOAD_ATTEMPTS=\"3\"",
            "curl --disable --fail --location",
            "--proto '=https'",
            "--proto-redir '=https'",
            "--connect-timeout 15",
            '--max-time "$remaining_seconds"',
            '--dump-header "$headers"',
            '"$url" | receive_archive',
            'final_header_value "Content-Length"',
            'final_header_value "Retry-After"',
            'base * (0.8 + (0.4 * random.random()))',
            'wait_before_retry "$attempt" "$retry_after"',
            'Content-Length do instalador é inválido ou divergente',
            "tempfile.mkstemp(",
            "output.flush()",
            "os.fsync(output.fileno())",
            "if time.monotonic() >= deadline:",
            "os.replace(temporary_name, destination)",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, source)
        self.assertNotIn("--retry ", source)
        self.assertNotIn("--retry-max-time", source)
        self.assertNotIn("--max-filesize", source)

    @unittest.skipIf(os.name == "nt", "bootstrap Unix é exercitado nos runners POSIX")
    def test_unix_bootstrap_falls_back_after_http_200_with_invalid_content(self):
        bootstrap = ROOT / "dist/installer/bin/install.sh"
        bundle = CURRENT_BUNDLE
        self.assertEqual(CURRENT_BUNDLE_SIZE, bundle.stat().st_size, "execute git lfs pull antes dos testes")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binaries = root / "bin"
            binaries.mkdir()
            (binaries / "python3").symlink_to(sys.executable)
            for command in ("mktemp", "rm"):
                executable = shutil.which(command)
                self.assertIsNotNone(executable, command)
                (binaries / command).symlink_to(executable)

            calls = root / "curl-calls"
            curl = binaries / "curl"
            curl.write_text(
                """#!/bin/sh
if [ "$1" != "--disable" ]; then
  printf 'curl config was not disabled first\n' >&2
  exit 97
fi
count=0
if [ -f "$X86QW_CURL_CALLS" ]; then
  while IFS= read -r line; do count=$line; done < "$X86QW_CURL_CALLS"
fi
count=$((count + 1))
printf '%s\n' "$count" >> "$X86QW_CURL_CALLS"
if [ "$count" -eq 1 ]; then
  printf 'conteudo HTTP 200 corrompido'
else
  /bin/cat "$X86QW_TEST_BUNDLE"
fi
""",
                encoding="utf-8",
            )
            curl.chmod(0o755)

            completed = subprocess.run(
                ["/bin/bash", str(bootstrap), "--help"],
                env={
                    "PATH": os.fspath(binaries),
                    "TMPDIR": os.fspath(root),
                    "X86QW_CURL_CALLS": os.fspath(calls),
                    "X86QW_TEST_BUNDLE": os.fspath(bundle),
                    "PYTHONIOENCODING": "utf-8",
                },
                check=False,
                capture_output=True,
                text=True,
            )
            received_calls = calls.read_text(encoding="utf-8").splitlines()
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual(["1", "2"], received_calls)
        self.assertIn("mirror rejeitado por indisponibilidade ou integridade", completed.stderr)
        self.assertIn("usage: x86qw", completed.stdout)

    @unittest.skipIf(os.name == "nt", "bootstrap Unix é exercitado nos runners POSIX")
    def test_unix_bootstrap_rejects_divergent_content_length_before_accepting_mirror(self):
        bootstrap = ROOT / "dist/installer/bin/install.sh"
        bundle = CURRENT_BUNDLE
        self.assertEqual(CURRENT_BUNDLE_SIZE, bundle.stat().st_size, "execute git lfs pull antes dos testes")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binaries = root / "bin"
            binaries.mkdir()
            (binaries / "python3").symlink_to(sys.executable)
            for command in ("mktemp", "rm"):
                executable = shutil.which(command)
                self.assertIsNotNone(executable, command)
                (binaries / command).symlink_to(executable)

            calls = root / "curl-calls"
            curl = binaries / "curl"
            curl.write_text(
                """#!/bin/sh
headers=
previous=
for argument in "$@"; do
  if [ "$previous" = "--dump-header" ]; then headers=$argument; fi
  previous=$argument
done
count=0
if [ -f "$X86QW_CURL_CALLS" ]; then
  while IFS= read -r line; do count=$line; done < "$X86QW_CURL_CALLS"
fi
count=$((count + 1))
printf '%s\n' "$count" >> "$X86QW_CURL_CALLS"
if [ "$count" -eq 1 ]; then declared=1; else declared=$X86QW_TEST_SIZE; fi
printf 'HTTP/1.1 200 OK\r\nContent-Length: %s\r\n\r\n' "$declared" > "$headers"
/bin/cat "$X86QW_TEST_BUNDLE"
""",
                encoding="utf-8",
            )
            curl.chmod(0o755)

            completed = subprocess.run(
                ["/bin/bash", str(bootstrap), "--help"],
                env={
                    "PATH": os.fspath(binaries),
                    "TMPDIR": os.fspath(root),
                    "X86QW_CURL_CALLS": os.fspath(calls),
                    "X86QW_TEST_BUNDLE": os.fspath(bundle),
                    "X86QW_TEST_SIZE": str(bundle.stat().st_size),
                    "PYTHONIOENCODING": "utf-8",
                },
                check=False,
                capture_output=True,
                text=True,
            )
            received_calls = calls.read_text(encoding="utf-8").splitlines()
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual(["1", "2"], received_calls)
        self.assertIn("Content-Length do instalador", completed.stderr)
        self.assertIn("usage: x86qw", completed.stdout)

    @unittest.skipIf(os.name == "nt", "bootstrap Unix é exercitado nos runners POSIX")
    def test_unix_bootstrap_retries_partial_transfer_with_a_fresh_receiver(self):
        bootstrap = ROOT / "dist/installer/bin/install.sh"
        bundle = CURRENT_BUNDLE
        self.assertEqual(CURRENT_BUNDLE_SIZE, bundle.stat().st_size, "execute git lfs pull antes dos testes")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binaries = root / "bin"
            binaries.mkdir()
            (binaries / "python3").symlink_to(sys.executable)
            for command in ("mktemp", "rm"):
                executable = shutil.which(command)
                self.assertIsNotNone(executable, command)
                (binaries / command).symlink_to(executable)

            calls = root / "curl-calls"
            curl = binaries / "curl"
            curl.write_text(
                """#!/bin/sh
for argument in "$@"; do
  if [ "$argument" = "--max-filesize" ]; then exit 99; fi
done
count=0
if [ -f "$X86QW_CURL_CALLS" ]; then
  while IFS= read -r line; do count=$line; done < "$X86QW_CURL_CALLS"
fi
count=$((count + 1))
printf '%s\n' "$count" >> "$X86QW_CURL_CALLS"
if [ "$count" -eq 1 ]; then
  printf 'resposta parcial'
  exit 28
fi
/bin/cat "$X86QW_TEST_BUNDLE"
""",
                encoding="utf-8",
            )
            curl.chmod(0o755)

            completed = subprocess.run(
                ["/bin/bash", str(bootstrap), "--help"],
                env={
                    "PATH": os.fspath(binaries),
                    "TMPDIR": os.fspath(root),
                    "X86QW_CURL_CALLS": os.fspath(calls),
                    "X86QW_TEST_BUNDLE": os.fspath(bundle),
                    "PYTHONIOENCODING": "utf-8",
                },
                check=False,
                capture_output=True,
                text=True,
            )
            received_calls = calls.read_text(encoding="utf-8").splitlines()
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual(["1", "2"], received_calls)
        self.assertIn("tentativa 2/3", completed.stdout)
        self.assertIn("usage: x86qw", completed.stdout)

    @unittest.skipIf(os.name == "nt", "bootstrap Unix é exercitado nos runners POSIX")
    def test_unix_bootstrap_shares_one_budget_across_mirrors(self):
        canonical = ROOT / "dist/installer/bin/install.sh"
        bundle = CURRENT_BUNDLE
        self.assertEqual(CURRENT_BUNDLE_SIZE, bundle.stat().st_size, "execute git lfs pull antes dos testes")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bootstrap = root / "install-budget.sh"
            source = canonical.read_text(encoding="utf-8").replace(
                'DOWNLOAD_BUDGET_SECONDS="180"',
                'DOWNLOAD_BUDGET_SECONDS="0.8"',
                1,
            )
            bootstrap.write_text(source, encoding="utf-8")
            bootstrap.chmod(0o755)
            binaries = root / "bin"
            binaries.mkdir()
            (binaries / "python3").symlink_to(sys.executable)
            for command in ("mktemp", "rm"):
                executable = shutil.which(command)
                self.assertIsNotNone(executable, command)
                (binaries / command).symlink_to(executable)

            calls = root / "curl-calls"
            curl = binaries / "curl"
            curl.write_text(
                """#!/bin/sh
count=0
if [ -f "$X86QW_CURL_CALLS" ]; then
  while IFS= read -r line; do count=$line; done < "$X86QW_CURL_CALLS"
fi
count=$((count + 1))
printf '%s\n' "$count" >> "$X86QW_CURL_CALLS"
if [ "$count" -eq 1 ]; then
  printf 'mirror corrompido'
else
  /bin/sleep 1.0
  /bin/cat "$X86QW_TEST_BUNDLE"
fi
""",
                encoding="utf-8",
            )
            curl.chmod(0o755)

            completed = subprocess.run(
                ["/bin/bash", str(bootstrap), "--help"],
                env={
                    "PATH": os.fspath(binaries),
                    "TMPDIR": os.fspath(root),
                    "X86QW_CURL_CALLS": os.fspath(calls),
                    "X86QW_TEST_BUNDLE": os.fspath(bundle),
                    "PYTHONIOENCODING": "utf-8",
                },
                check=False,
                capture_output=True,
                text=True,
            )
            received_calls = calls.read_text(encoding="utf-8").splitlines()
        self.assertNotEqual(0, completed.returncode)
        self.assertEqual(
            ["1", "2"],
            received_calls,
            completed.stdout + completed.stderr,
        )
        self.assertIn("prazo total", completed.stderr)
        self.assertNotIn("usage: x86qw", completed.stdout)

    @unittest.skipIf(os.name == "nt", "bootstrap Unix é exercitado nos runners POSIX")
    def test_unix_bootstrap_skips_unaffordable_retry_after_for_next_mirror(self):
        bootstrap = ROOT / "dist/installer/bin/install.sh"
        bundle = CURRENT_BUNDLE
        self.assertEqual(CURRENT_BUNDLE_SIZE, bundle.stat().st_size, "execute git lfs pull antes dos testes")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binaries = root / "bin"
            binaries.mkdir()
            (binaries / "python3").symlink_to(sys.executable)
            for command in ("mktemp", "rm"):
                executable = shutil.which(command)
                self.assertIsNotNone(executable, command)
                (binaries / command).symlink_to(executable)

            calls = root / "curl-calls"
            curl = binaries / "curl"
            curl.write_text(
                """#!/bin/sh
headers=
previous=
for argument in "$@"; do
  if [ "$previous" = "--dump-header" ]; then headers=$argument; fi
  previous=$argument
done
count=0
if [ -f "$X86QW_CURL_CALLS" ]; then
  while IFS= read -r line; do count=$line; done < "$X86QW_CURL_CALLS"
fi
count=$((count + 1))
printf '%s\n' "$count" >> "$X86QW_CURL_CALLS"
if [ "$count" -eq 1 ]; then
  printf 'HTTP/1.1 503 Busy\r\nRetry-After: 999\r\n\r\n' > "$headers"
  exit 22
fi
printf 'HTTP/1.1 200 OK\r\nContent-Length: %s\r\n\r\n' "$X86QW_TEST_SIZE" > "$headers"
/bin/cat "$X86QW_TEST_BUNDLE"
""",
                encoding="utf-8",
            )
            curl.chmod(0o755)

            completed = subprocess.run(
                ["/bin/bash", str(bootstrap), "--help"],
                env={
                    "PATH": os.fspath(binaries),
                    "TMPDIR": os.fspath(root),
                    "X86QW_CURL_CALLS": os.fspath(calls),
                    "X86QW_TEST_BUNDLE": os.fspath(bundle),
                    "X86QW_TEST_SIZE": str(bundle.stat().st_size),
                    "PYTHONIOENCODING": "utf-8",
                },
                check=False,
                capture_output=True,
                text=True,
            )
            received_calls = calls.read_text(encoding="utf-8").splitlines()
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual(["1", "2"], received_calls)
        self.assertIn("mirror rejeitado", completed.stderr)
        self.assertIn("usage: x86qw", completed.stdout)

    def test_powershell_embedded_downloader_rejects_insecure_redirects_and_oversize(self):
        source = (ROOT / "dist/installer/bin/install.ps1").read_text(encoding="utf-8")
        marker = "$DownloaderSource = @'\n"
        downloader = source.split(marker, 1)[1].split("\n'@", 1)[0]
        definitions = downloader.split("\ntry:\n    download_mirrors(", 1)[0]
        namespace: dict[str, object] = {}
        exec(compile(definitions, "x86qw-bootstrap-download.py", "exec"), namespace)

        urllib_module = namespace["urllib"]
        handler = namespace["HttpsOnlyRedirectHandler"]()
        request = urllib_module.request.Request("https://example.invalid/source")
        with self.assertRaisesRegex(namespace["PolicyError"], "fora de HTTPS"):
            handler.redirect_request(
                request, None, 302, "Found", {}, "http://example.invalid/destination"
            )

        class RedirectBody:
            def __init__(self):
                self.closed = False

            def read(self, *_args):
                raise AssertionError("o corpo 3xx nao pode ser drenado")

            def close(self):
                self.closed = True

        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                body = RedirectBody()
                parent = mock.Mock()
                expected = object()
                parent.open.return_value = expected
                redirect_handler = namespace["HttpsOnlyRedirectHandler"]()
                redirect_handler.parent = parent
                redirected_request = urllib_module.request.Request(
                    "https://example.invalid/source", method="HEAD",
                )
                redirected_request.timeout = 1

                result = getattr(redirect_handler, f"http_error_{status}")(
                    redirected_request,
                    body,
                    status,
                    "Found",
                    {"location": "https://example.invalid/destination"},
                )

                self.assertIs(expected, result)
                self.assertTrue(body.closed)
                parent.open.assert_called_once()
                self.assertEqual("HEAD", parent.open.call_args.args[0].get_method())

        rejected_body = RedirectBody()
        rejected_parent = mock.Mock()
        rejected_handler = namespace["HttpsOnlyRedirectHandler"]()
        rejected_handler.parent = rejected_parent
        rejected_request = urllib_module.request.Request(
            "https://example.invalid/source",
        )
        rejected_request.timeout = 1
        with self.assertRaises(namespace["PolicyError"]):
            rejected_handler.http_error_302(
                rejected_request,
                rejected_body,
                302,
                "Found",
                {"location": "http://example.invalid/destination"},
            )
        self.assertTrue(rejected_body.closed)
        rejected_parent.open.assert_not_called()

        missing_body = RedirectBody()
        missing_handler = namespace["HttpsOnlyRedirectHandler"]()
        missing_handler.parent = mock.Mock()
        missing_request = urllib_module.request.Request("https://example.invalid/source")
        missing_request.timeout = 1
        self.assertIsNone(missing_handler.http_error_302(
            missing_request, missing_body, 302, "Found", {},
        ))
        self.assertTrue(missing_body.closed)
        missing_handler.parent.open.assert_not_called()

        loop_body = RedirectBody()
        loop_handler = namespace["HttpsOnlyRedirectHandler"]()
        loop_handler.parent = mock.Mock()
        loop_request = urllib_module.request.Request("https://example.invalid/source")
        loop_request.timeout = 1
        loop_request.redirect_dict = {
            "https://example.invalid/destination": loop_handler.max_repeats,
        }
        with self.assertRaises(urllib_module.error.HTTPError) as raised_redirect:
            loop_handler.http_error_302(
                loop_request,
                loop_body,
                302,
                "Found",
                {"location": "https://example.invalid/destination"},
            )
        raised_redirect.exception.close()
        loop_handler.parent.open.assert_not_called()

        class OversizeResponse:
            status = 200
            headers: dict[str, str] = {}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def geturl(self):
                return "https://example.invalid/archive.zip"

            def read(self, amount):
                return b"x" * amount

        class OversizeOpener:
            def open(self, *_args, **_kwargs):
                return OversizeResponse()

        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "archive.zip"
            with mock.patch.object(urllib_module.request, "build_opener") as build_opener:
                with self.assertRaises(namespace["PolicyError"]):
                    namespace["download_mirrors"](
                        [
                            "https://first.example.invalid/archive.zip",
                            "http://second.example.invalid/archive.zip",
                        ],
                        os.fspath(destination),
                        5,
                        "0" * 64,
                        1,
                        1,
                        0,
                        2,
                    )
            build_opener.assert_not_called()

            with mock.patch.object(
                urllib_module.request, "build_opener", return_value=OversizeOpener()
            ):
                with self.assertRaisesRegex(namespace["DownloadError"], "maior que o limite"):
                    namespace["download_mirrors"](
                        ["https://example.invalid/archive.zip"],
                        os.fspath(destination),
                        5,
                        "0" * 64,
                        1,
                        1,
                        0,
                        2,
                    )
            self.assertFalse(destination.exists())
            self.assertFalse(Path(os.fspath(destination) + ".part").exists())

        class IntegrityResponse:
            status = 200

            def __init__(self, body, url):
                self.body = body
                self.url = url
                self.offset = 0
                self.headers = {"Content-Length": str(len(body))}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def geturl(self):
                return self.url

            def read(self, amount):
                block = self.body[self.offset:self.offset + amount]
                self.offset += len(block)
                return block

            read1 = read

        class IntegrityFallbackOpener:
            def __init__(self):
                self.calls = []

            def open(self, request, **_kwargs):
                self.calls.append(request.full_url)
                body = b"wrong" if len(self.calls) == 1 else b"valid"
                return IntegrityResponse(body, request.full_url)

        fallback = IntegrityFallbackOpener()
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "archive.zip"
            with mock.patch.object(
                urllib_module.request, "build_opener", return_value=fallback,
            ):
                selected = namespace["download_mirrors"](
                    [
                        "https://first.example.invalid/archive.zip",
                        "https://second.example.invalid/archive.zip",
                    ],
                    os.fspath(destination),
                    5,
                    hashlib.sha256(b"valid").hexdigest(),
                    1,
                    1,
                    0,
                    3,
                )
            self.assertEqual(b"valid", destination.read_bytes())
        self.assertEqual("https://second.example.invalid/archive.zip", selected)
        self.assertEqual(2, len(fallback.calls))

        direct_close_calls = []

        class DirectSocket:
            def shutdown(self, how):
                direct_close_calls.append(("shutdown", how))

        class DirectConnection:
            sock = DirectSocket()

            def close(self):
                direct_close_calls.append("close")

        direct_registry = namespace["ConnectionRegistry"]()
        direct_registry.register(7, DirectConnection())
        direct_registry.cancel(7)
        self.assertEqual([
            ("shutdown", namespace["socket"].SHUT_RDWR), "close",
        ], direct_close_calls)

        class BlockingResolver:
            args = ["python", "resolver"]
            def __init__(self):
                self.returncode = None
                self.calls = 0
                self.killed = False

            def communicate(self, input=None, timeout=None):
                self.calls += 1
                if self.calls == 1:
                    raise namespace["subprocess"].TimeoutExpired(self.args, timeout)
                self.returncode = -9
                return b"", b""

            def kill(self):
                self.killed = True

        blocked_resolver = BlockingResolver()
        with mock.patch.object(
            namespace["subprocess"], "Popen", return_value=blocked_resolver,
        ), mock.patch.object(
            namespace["time"],
            "monotonic",
            side_effect=[100.0, 100.1, 100.1, 100.1],
        ), self.assertRaisesRegex(TimeoutError, "resolucao DNS"):
            namespace["resolve_addresses"]("example.invalid", 443, 1.0)
        self.assertTrue(blocked_resolver.killed)
        self.assertEqual(2, blocked_resolver.calls)

        registered = threading.Event()
        registered_workers = []
        cancelled_workers = []
        unblock = threading.Event()

        class CancelConnection:
            def close(self):
                unblock.set()

        class BlockingOpener:
            registry = None

            def open(self, _request, **_kwargs):
                identity = threading.get_ident()
                registered_workers.append((id(self.registry), identity))
                self.registry.register(identity, CancelConnection())
                registered.set()
                while not unblock.wait(0.1):
                    pass
                return IntegrityResponse(b"valid", "https://slow.example.invalid/archive.zip")

        blocked = BlockingOpener()
        real_thread_start = namespace["threading"].Thread.start
        real_monotonic = namespace["time"].monotonic
        clock_offset = [0.0]

        def controlled_monotonic():
            return real_monotonic() + clock_offset[0]

        def start_after_registration(thread):
            real_thread_start(thread)
            if thread.name == "x86qw-bootstrap-open":
                self.assertTrue(registered.wait(1))
                clock_offset[0] = 1.0

        registry_class = namespace["ConnectionRegistry"]
        real_registry_cancel = registry_class.cancel

        def observed_registry_cancel(registry, identity):
            cancelled_workers.append((id(registry), identity))
            return real_registry_cancel(registry, identity)

        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "archive.zip"
            try:
                with mock.patch.object(
                    urllib_module.request, "build_opener", return_value=blocked,
                ), mock.patch.object(
                    namespace["threading"].Thread, "start", start_after_registration,
                ), mock.patch.object(
                    namespace["time"], "monotonic", controlled_monotonic,
                ), mock.patch.object(
                    registry_class, "cancel", observed_registry_cancel,
                ):
                    with self.assertRaisesRegex(namespace["DownloadError"], "prazo total"):
                        namespace["download_mirrors"](
                            ["https://slow.example.invalid/archive.zip"],
                            os.fspath(destination),
                            5,
                            hashlib.sha256(b"valid").hexdigest(),
                            1,
                            1,
                            0,
                            0.05,
                        )
            finally:
                unblock.set()
            self.assertEqual(registered_workers, cancelled_workers)
            self.assertFalse(destination.exists())
        for _ in range(50):
            if not any(
                thread.name == "x86qw-bootstrap-open" and thread.is_alive()
                for thread in threading.enumerate()
            ):
                break
            time.sleep(0.01)
        self.assertFalse(any(
            thread.name == "x86qw-bootstrap-open" and thread.is_alive()
            for thread in threading.enumerate()
        ))

        class NotFoundOpener:
            def __init__(self):
                self.calls = 0

            def open(self, request, **_kwargs):
                self.calls += 1
                raise urllib_module.error.HTTPError(
                    request.full_url, 404, "Not Found", {}, None,
                )

        not_found = NotFoundOpener()
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "archive.zip"
            with mock.patch.object(
                urllib_module.request, "build_opener", return_value=not_found,
            ):
                with self.assertRaisesRegex(namespace["DownloadError"], "HTTP 404"):
                    namespace["download_mirrors"](
                        ["https://example.invalid/archive.zip"],
                        os.fspath(destination),
                        5,
                        "0" * 64,
                        1,
                        1,
                        2,
                        3,
                    )
        self.assertEqual(1, not_found.calls)

        for fragment in (
            "$InstallerConnectTimeoutSeconds = 15",
            "$InstallerTransferTimeoutSeconds = 120",
            "$InstallerRetryMaxSeconds = 180",
            "$InstallerRetries = 2",
            "time.monotonic() + retry_max_time",
            "if total_deadline <= attempt_deadline and total_deadline <= connection_deadline:",
            "timeout_error = DownloadError(\"prazo total excedido durante conexao ou headers\")",
            "reader = getattr(response, \"read1\", response.read)",
            "open_with_deadline(",
            "tempfile.mkstemp(",
            "os.fchmod(descriptor, 0o600)",
            "os.fsync(output.fileno())",
            "remaining(total_deadline)",
            "os.replace(part, destination)",
            "TRANSIENT_HTTP = frozenset({408, 425, 429, 500, 502, 503, 504})",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, source)

    def test_powershell_embedded_controller_retries_transient_failures(self):
        source = (ROOT / "dist/installer/bin/install.ps1").read_text(encoding="utf-8")
        marker = "$DownloaderSource = @'\n"
        downloader = source.split(marker, 1)[1].split("\n'@", 1)[0]
        definitions = downloader.split("\ntry:\n    download_mirrors(", 1)[0]
        namespace: dict[str, object] = {}
        exec(compile(definitions, "x86qw-bootstrap-download.py", "exec"), namespace)
        urllib_module = namespace["urllib"]

        class Response:
            status = 200

            def __init__(self, body, url, declared_size=None):
                self.body = body
                self.url = url
                self.offset = 0
                self.headers = {
                    "Content-Length": str(
                        len(body) if declared_size is None else declared_size
                    )
                }

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def geturl(self):
                return self.url

            def read1(self, amount):
                block = self.body[self.offset:self.offset + amount]
                self.offset += len(block)
                return block

            read = read1

        expected = hashlib.sha256(b"valid").hexdigest()

        class RetryAfterFallbackOpener:
            def __init__(self):
                self.calls = []

            def open(self, request, **_kwargs):
                self.calls.append(request.full_url)
                if len(self.calls) == 1:
                    raise urllib_module.error.HTTPError(
                        request.full_url,
                        503,
                        "Busy",
                        {"Retry-After": "999"},
                        None,
                    )
                return Response(b"valid", request.full_url)

        retry_after = RetryAfterFallbackOpener()
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "archive.zip"
            with mock.patch.object(
                urllib_module.request, "build_opener", return_value=retry_after,
            ):
                selected = namespace["download_mirrors"](
                    [
                        "https://first.example.invalid/archive.zip",
                        "https://second.example.invalid/archive.zip",
                    ],
                    os.fspath(destination),
                    5,
                    expected,
                    1,
                    1,
                    2,
                    2,
                )
            self.assertEqual(b"valid", destination.read_bytes())
        self.assertEqual("https://second.example.invalid/archive.zip", selected)
        self.assertEqual(2, len(retry_after.calls))

        class PartialThenValidOpener:
            def __init__(self):
                self.calls = 0

            def open(self, request, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    return Response(b"abc", request.full_url, declared_size=5)
                return Response(b"valid", request.full_url)

        partial = PartialThenValidOpener()
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "archive.zip"
            with mock.patch.object(
                urllib_module.request, "build_opener", return_value=partial,
            ), mock.patch.object(namespace["time"], "sleep", return_value=None):
                namespace["download_mirrors"](
                    ["https://partial.example.invalid/archive.zip"],
                    os.fspath(destination),
                    5,
                    expected,
                    1,
                    1,
                    1,
                    2,
                )
            self.assertEqual(b"valid", destination.read_bytes())
        self.assertEqual(2, partial.calls)

        class IncompleteThenValidOpener:
            def __init__(self):
                self.calls = 0

            def open(self, request, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    response = Response(b"valid", request.full_url)

                    def incomplete(_amount):
                        raise namespace["http"].client.IncompleteRead(b"abc", 2)

                    response.read1 = incomplete
                    response.read = incomplete
                    return response
                return Response(b"valid", request.full_url)

        incomplete = IncompleteThenValidOpener()
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "archive.zip"
            with mock.patch.object(
                urllib_module.request, "build_opener", return_value=incomplete,
            ), mock.patch.object(namespace["time"], "sleep", return_value=None):
                namespace["download_mirrors"](
                    ["https://incomplete.example.invalid/archive.zip"],
                    os.fspath(destination),
                    5,
                    expected,
                    1,
                    1,
                    1,
                    2,
                )
            self.assertEqual(b"valid", destination.read_bytes())
        self.assertEqual(2, incomplete.calls)

        release = threading.Event()

        class CancelConnection:
            def close(self):
                release.set()

        class TimeoutThenValidOpener:
            registry = None

            def __init__(self):
                self.calls = 0

            def open(self, request, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    self.registry.register(threading.get_ident(), CancelConnection())
                    # Do not let the fake response win the same deadline race
                    # as the production worker.  The downloader must cancel
                    # this open before it is allowed to return.
                    release.wait()
                return Response(b"valid", request.full_url)

        timeout = TimeoutThenValidOpener()
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "archive.zip"
            with mock.patch.object(
                urllib_module.request, "build_opener", return_value=timeout,
            ), mock.patch.object(namespace["time"], "sleep", return_value=None):
                namespace["download_mirrors"](
                    ["https://timeout.example.invalid/archive.zip"],
                    os.fspath(destination),
                    5,
                    expected,
                    1,
                    1,
                    1,
                    2,
                )
            self.assertEqual(b"valid", destination.read_bytes())
        self.assertTrue(release.is_set())
        self.assertEqual(2, timeout.calls)

        connect_release = threading.Event()

        class ConnectBudgetConnection:
            def close(self):
                connect_release.set()

        class ConnectBudgetThenValidOpener:
            registry = None

            def __init__(self):
                self.calls = 0
                self.call_times = []

            def open(self, request, **_kwargs):
                self.calls += 1
                self.call_times.append(time.monotonic())
                if self.calls == 1:
                    self.registry.register(
                        threading.get_ident(), ConnectBudgetConnection(),
                    )
                    connect_release.wait(2)
                return Response(b"valid", request.full_url)

        # Windows runners can take more than 50 ms merely to schedule the
        # replacement worker.  Keep this as a real-clock integration check,
        # but use a budget well below the bootstrap's production timeout.
        connect_timeout = 1.0
        connect_budget = ConnectBudgetThenValidOpener()
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "archive.zip"
            started = time.monotonic()
            with mock.patch.object(
                urllib_module.request,
                "build_opener",
                return_value=connect_budget,
            ), mock.patch.object(namespace["time"], "sleep", return_value=None):
                namespace["download_mirrors"](
                    ["https://connect-timeout.example.invalid/archive.zip"],
                    os.fspath(destination),
                    5,
                    expected,
                    connect_timeout,
                    1.2,
                    1,
                    2.5,
                )
            elapsed = time.monotonic() - started
            self.assertEqual(b"valid", destination.read_bytes())
        self.assertEqual(2, connect_budget.calls)
        self.assertTrue(connect_release.is_set())
        self.assertGreaterEqual(
            connect_budget.call_times[1] - connect_budget.call_times[0],
            connect_timeout * 0.8,
        )
        self.assertLess(elapsed, 1.5)

        class ValidOpener:
            def open(self, request, **_kwargs):
                return Response(b"valid", request.full_url)

        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "archive.zip"

            def slow_fsync(_descriptor):
                time.sleep(1.2)

            with mock.patch.object(
                urllib_module.request, "build_opener", return_value=ValidOpener(),
            ), mock.patch.object(namespace["os"], "fsync", side_effect=slow_fsync):
                with self.assertRaisesRegex(namespace["DownloadError"], "prazo total"):
                    namespace["download_mirrors"](
                        ["https://valid.example.invalid/archive.zip"],
                        os.fspath(destination),
                        5,
                        expected,
                        1,
                        1,
                        0,
                        1,
                    )
            self.assertFalse(destination.exists())

    def test_powershell_embedded_download_collects_blocked_resolver(self):
        source = (ROOT / "dist/installer/bin/install.ps1").read_text(encoding="utf-8")
        marker = "$DownloaderSource = @'\n"
        downloader = source.split(marker, 1)[1].split("\n'@", 1)[0]
        definitions = downloader.split("\ntry:\n    download_mirrors(", 1)[0]
        namespace: dict[str, object] = {}
        exec(compile(definitions, "x86qw-bootstrap-download.py", "exec"), namespace)

        class BlockingResolver:
            def __init__(self, *, reap_delay=0.2):
                self.args = ["python", "resolver"]
                self.returncode = None
                self.reap_delay = reap_delay
                self.killed = threading.Event()
                self.collected = threading.Event()
                self.inputs = []
                self.dns_started = threading.Event()
                self.dns_active = threading.Event()

            def communicate(self, input=None, timeout=None):
                self.inputs.append(input)
                if input == b"G":
                    self.dns_started.set()
                    self.dns_active.set()
                if not self.killed.wait(timeout):
                    raise subprocess.TimeoutExpired(self.args, timeout)
                time.sleep(self.reap_delay)
                self.returncode = -9
                self.collected.set()
                return b"", b""

            def kill(self):
                self.killed.set()
                self.dns_active.clear()

        process = BlockingResolver()
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "archive.zip"
            with mock.patch.object(
                namespace["subprocess"], "Popen", return_value=process,
            ):
                with self.assertRaisesRegex(namespace["DownloadError"], "prazo"):
                    namespace["download_mirrors"](
                        ["https://resolver.example.invalid/archive.zip"],
                        os.fspath(destination),
                        1,
                        hashlib.sha256(b"x").hexdigest(),
                        1,
                        1,
                        0,
                        1,
                    )
            self.assertFalse(destination.exists())

        self.assertTrue(process.killed.is_set())
        # The controller must stop the resolver before returning, but the
        # daemon worker may need one final scheduler turn to reap the killed
        # process.  Waiting on the event keeps this assertion portable without
        # weakening the subsequent no-residual-thread check.
        self.assertTrue(process.collected.wait(1))
        self.assertEqual(b"G", process.inputs[0])
        self.assertTrue(process.dns_started.is_set())
        self.assertFalse(any(
            thread.name == "x86qw-bootstrap-open" and thread.is_alive()
            for thread in threading.enumerate()
        ))

        slow_process = BlockingResolver(reap_delay=1.0)
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "slow-reap.zip"
            started = time.monotonic()
            with mock.patch.object(
                namespace["subprocess"], "Popen", return_value=slow_process,
            ):
                with self.assertRaisesRegex(namespace["DownloadError"], "prazo"):
                    namespace["download_mirrors"](
                        ["https://resolver.example.invalid/archive.zip"],
                        os.fspath(destination),
                        1,
                        hashlib.sha256(b"x").hexdigest(),
                        1,
                        1,
                        0,
                        1,
                    )
            elapsed = time.monotonic() - started
            self.assertFalse(destination.exists())

        # The event assertions below prove that the controller returned before
        # the one-second reaper completed.  A fixed wall-clock envelope keeps
        # the public deadline contract independent from production constants
        # while tolerating normal scheduler jitter on shared CI runners.
        self.assertLess(elapsed, 1.75)
        self.assertTrue(slow_process.killed.is_set())
        self.assertTrue(slow_process.dns_started.is_set())
        self.assertFalse(slow_process.dns_active.is_set())
        self.assertFalse(slow_process.collected.is_set())
        residual = [
            thread for thread in threading.enumerate()
            if thread.name == "x86qw-bootstrap-open" and thread.is_alive()
        ]
        self.assertEqual(1, len(residual))
        self.assertTrue(slow_process.collected.wait(2))
        residual[0].join(1)
        self.assertFalse(residual[0].is_alive())

        late_process = BlockingResolver()
        spawn_entered = threading.Event()
        release_spawn = threading.Event()

        def delayed_spawn(*_args, **_kwargs):
            spawn_entered.set()
            if not release_spawn.wait(5):
                raise AssertionError("the delayed resolver fixture was not released")
            return late_process

        release_timer = threading.Timer(3, release_spawn.set)
        release_timer.start()
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "late-resolver.zip"
            try:
                with mock.patch.object(
                    namespace["subprocess"], "Popen", side_effect=delayed_spawn,
                ):
                    with self.assertRaisesRegex(namespace["DownloadError"], "prazo"):
                        namespace["download_mirrors"](
                            ["https://resolver.example.invalid/archive.zip"],
                            os.fspath(destination),
                            1,
                            hashlib.sha256(b"x").hexdigest(),
                            1,
                            1,
                            0,
                            1,
                        )
                returned_before_spawn = not release_spawn.is_set()
                self.assertFalse(destination.exists())
            finally:
                release_spawn.set()
                release_timer.cancel()

        self.assertTrue(spawn_entered.is_set())
        self.assertTrue(returned_before_spawn)
        self.assertTrue(late_process.collected.wait(1))
        self.assertTrue(late_process.killed.is_set())
        self.assertNotIn(b"G", late_process.inputs)
        self.assertFalse(late_process.dns_started.is_set())
        self.assertFalse(any(
            thread.name == "x86qw-bootstrap-open" and thread.is_alive()
            for thread in threading.enumerate()
        ))

        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(namespace["subprocess"], "Popen") as spawn:
                for budget in (0.01, namespace["MIN_OPEN_BUDGET_SECONDS"]):
                    destination = Path(temporary) / f"tiny-{budget}.zip"
                    with self.subTest(budget=budget), self.assertRaisesRegex(
                        namespace["DownloadError"], "prazo"
                    ):
                        namespace["download_mirrors"](
                            ["https://resolver.example.invalid/archive.zip"],
                            os.fspath(destination),
                            1,
                            hashlib.sha256(b"x").hexdigest(),
                            budget,
                            budget,
                            0,
                            budget,
                        )
                    self.assertFalse(destination.exists())
            spawn.assert_not_called()

    @unittest.skipUnless(
        WINDOWS_POWERSHELL,
        "Windows PowerShell 5.1 é exercitado somente no runner Windows",
    )
    def test_public_bootstrap_parses_in_windows_powershell_51(self):
        parser = (
            "$tokens=$null; $errors=$null; "
            "[System.Management.Automation.Language.Parser]::ParseFile("
            "$env:X86QW_TEST_PARSE_PATH, [ref]$tokens, [ref]$errors) > $null; "
            "if ($errors.Count) { $errors | ForEach-Object { Write-Error $_ }; exit 1 }"
        )
        for bootstrap in (
            ROOT / "dist/installer/bin/install.ps1",
            ROOT / "site/public/install.ps1",
        ):
            with self.subTest(bootstrap=bootstrap):
                environment = os.environ.copy()
                environment["X86QW_TEST_PARSE_PATH"] = os.fspath(bootstrap)
                completed = subprocess.run(
                    [
                        WINDOWS_POWERSHELL,
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-Command",
                        parser,
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
                self.assertEqual(0, completed.returncode, completed.stderr)

        manager_tree = ast.parse(
            (ROOT / "dist/installer/bin/manager.py").read_text(encoding="utf-8")
        )
        public_command = next(
            ast.literal_eval(statement.value)
            for statement in manager_tree.body
            if isinstance(statement, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "PUBLIC_POWERSHELL_BOOTSTRAP_COMMAND"
                for target in statement.targets
            )
        )
        parse_command = (
            "$tokens=$null; $errors=$null; "
            "[System.Management.Automation.Language.Parser]::ParseInput("
            "$env:X86QW_TEST_PARSE_INPUT, [ref]$tokens, [ref]$errors) > $null; "
            "if ($errors.Count) { $errors | ForEach-Object { Write-Error $_ }; exit 1 }"
        )
        environment = os.environ.copy()
        environment["X86QW_TEST_PARSE_INPUT"] = public_command
        completed = subprocess.run(
            [
                WINDOWS_POWERSHELL,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                parse_command,
            ],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)

    @unittest.skipUnless(POWERSHELL, "PowerShell não está disponível neste runner")
    def test_public_powershell_bootstrap_materializes_archive_helper_with_legacy_native_arguments(self):
        with tempfile.TemporaryDirectory() as temporary:
            harness = Path(temporary) / "legacy-native-arguments-harness.ps1"
            harness.write_text(
                r'''param(
  [string]$Bootstrap,
  [string]$Fixture,
  [string]$RealPython
)
$PSNativeCommandArgumentPassing = "Legacy"
$global:X86QWInstallerReached = $false
$env:X86QW_TEST_FIXTURE = $Fixture
$Source = Get-Content -LiteralPath $Bootstrap -Raw
$CandidatePattern = '(?ms)  \$PythonCandidates = @\(.*?^  \)\r?\n  foreach'
if ([regex]::Matches($Source, $CandidatePattern).Count -ne 1) {
  throw "nao foi possivel controlar o runtime Python do bootstrap"
}
$CandidateReplacement = @'
  $PythonCandidates = @(
    [pscustomobject]@{ Command = $RealPython; Arguments = @() }
  )
  foreach
'@
$Source = [regex]::Replace($Source, $CandidatePattern, $CandidateReplacement)
$DownloaderPattern = "(?ms)    \`$DownloaderSource = @'.*?^'@\r?\n    \[System\.IO\.File\]::WriteAllText\("
if ([regex]::Matches($Source, $DownloaderPattern).Count -ne 1) {
  throw "nao foi possivel controlar o downloader do bootstrap"
}
$DownloaderReplacement = @"
    `$DownloaderSource = @'
import os
import shutil
import sys

shutil.copyfile(os.environ["X86QW_TEST_FIXTURE"], sys.argv[1])
'@
    [System.IO.File]::WriteAllText(
"@
$Source = [regex]::Replace($Source, $DownloaderPattern, $DownloaderReplacement)
$InstallerInvocation = '    & $PythonRuntime.Command @InstallerArguments'
if ([regex]::Matches(
  $Source,
  [regex]::Escape($InstallerInvocation)
).Count -ne 1) {
  throw "nao foi possivel controlar a execucao final do instalador"
}
$Source = $Source.Replace(
  $InstallerInvocation,
  '    $global:X86QWInstallerReached = $true; $global:LASTEXITCODE = 0'
)
Invoke-Expression $Source
if (-not $global:X86QWInstallerReached) {
  throw "o bootstrap nao alcancou o instalador verificado"
}
Write-Output "X86QW_LEGACY_MATERIALIZATION_OK"
''',
                encoding="utf-8",
            )
            for runtime in POWERSHELL_RUNTIMES:
                for bootstrap in (
                    ROOT / "dist/installer/bin/install.ps1",
                    ROOT / "site/public/install.ps1",
                ):
                    with self.subTest(runtime=runtime, bootstrap=bootstrap):
                        completed = subprocess.run(
                            [
                                runtime,
                                "-NoProfile",
                                "-ExecutionPolicy",
                                "Bypass",
                                "-File",
                                str(harness),
                                str(bootstrap),
                                str(CURRENT_BUNDLE),
                                sys.executable,
                            ],
                            check=False,
                            capture_output=True,
                            text=True,
                        )
                        self.assertEqual(0, completed.returncode, completed.stderr)
                        self.assertIn(
                            "X86QW_LEGACY_MATERIALIZATION_OK",
                            completed.stdout,
                        )

    @unittest.skipUnless(POWERSHELL, "PowerShell não está disponível neste runner")
    def test_public_powershell_bootstrap_creates_private_workdir_before_content(self):
        bootstrap = ROOT / "site/public/install.ps1"
        for candidate in (
            ROOT / "dist/installer/bin/install.ps1",
            bootstrap,
        ):
            with self.subTest(no_pathname_cleanup=candidate):
                self.assertNotIn(
                    "[System.IO.Directory]::Delete($Path",
                    candidate.read_text(encoding="utf-8"),
                )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            broad_temporary = root / "broad-temporary"
            broad_temporary.mkdir()
            report = root / "private-workdir.json"
            harness = root / "private-workdir-harness.ps1"
            harness.write_text(
                r'''param(
  [string]$Bootstrap,
  [string]$BroadTemporary,
  [string]$Report
)
$WindowsPlatform = (
  [System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT
)
if ($WindowsPlatform) {
  $Icacls = Join-Path $env:SystemRoot "System32\icacls.exe"
  & $Icacls $BroadTemporary "/inheritance:r" "/grant:r" "*S-1-1-0:(OI)(CI)F" | Out-Null
  if ($LASTEXITCODE -ne 0) {
    throw "nao foi possivel preparar o diretorio temporario amplo"
  }
}
$env:TEMP = $BroadTemporary
$env:TMP = $BroadTemporary

function Get-Command {
  param([string]$Name, [object]$ErrorAction)
  if ($Name -eq "python") {
    return [pscustomobject]@{ Name = "python" }
  }
  return $null
}

function python {
  if ($args -contains "-c") {
    Set-Variable -Name LASTEXITCODE -Scope 1 -Value 0
    return
  }
  for ($Index = 0; $Index -lt $args.Count; $Index++) {
    if ([string]$args[$Index] -like "*x86qw-bootstrap-download.py") {
      $Downloader = [string]$args[$Index]
      $WorkDir = Split-Path -Parent $Downloader
      if ($WindowsPlatform) {
        $CurrentSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        $Acl = Microsoft.PowerShell.Security\Get-Acl -LiteralPath $WorkDir
        $Rules = @($Acl.GetAccessRules(
          $true,
          $true,
          [System.Security.Principal.SecurityIdentifier]
        ))
        $Payload = [ordered]@{
          windows = $true
          protected = $Acl.AreAccessRulesProtected
          owner = $Acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value
          current_sid = $CurrentSid
          principals = @($Rules | ForEach-Object { $_.IdentityReference.Value } | Sort-Object)
          inherited = @($Rules | ForEach-Object { $_.IsInherited })
        }
      } else {
        $Payload = [ordered]@{
          windows = $false
          mode = [int][System.IO.File]::GetUnixFileMode($WorkDir)
        }
      }
      [System.IO.File]::WriteAllText(
        $Report,
        ($Payload | ConvertTo-Json -Compress),
        (New-Object System.Text.UTF8Encoding($false))
      )
      Set-Variable -Name LASTEXITCODE -Scope 1 -Value 1
      return
    }
  }
  Set-Variable -Name LASTEXITCODE -Scope 1 -Value 1
}

$Source = Get-Content -LiteralPath $Bootstrap -Raw
try {
  Invoke-Expression $Source
} catch {
  # The controlled downloader stops the bootstrap after inspecting the workdir.
}
''',
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    POWERSHELL,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(harness),
                    str(bootstrap),
                    str(broad_temporary),
                    str(report),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            observed = json.loads(report.read_text(encoding="utf-8"))

        if observed["windows"]:
            self.assertTrue(observed["protected"])
            self.assertEqual(observed["current_sid"], observed["owner"])
            self.assertEqual(
                sorted((observed["current_sid"], "S-1-5-18")),
                observed["principals"],
            )
            self.assertEqual([False, False], observed["inherited"])
        else:
            self.assertEqual(0o700, observed["mode"])

    @unittest.skipUnless(POWERSHELL, "PowerShell não está disponível neste runner")
    def test_public_powershell_bootstrap_preserves_the_calling_session(self):
        bootstrap = ROOT / "site/public/install.ps1"
        source = bootstrap.read_text(encoding="utf-8")
        version = source.split('$InstallerVersion = "', 1)[1].split('"', 1)[0]
        digest = source.split('$InstallerSha256 = "', 1)[1].split('"', 1)[0]
        fixture = ROOT / f"dist/installer/packages/{version}/x86qw-installer-{version}.zip"
        self.assertEqual(digest, hashlib.sha256(fixture.read_bytes()).hexdigest())
        with tempfile.TemporaryDirectory() as temporary:
            harness = Path(temporary) / "bootstrap-harness.ps1"
            harness.write_text(
                r'''param(
  [string]$Bootstrap,
  [string]$MockVersion,
  [string]$Fixture
)
$global:X86QWTestMockVersion = $MockVersion
$global:X86QWTestFixture = $Fixture
$global:X86QWArchiveTempPrivate = $false
function Invoke-WebRequest {
  param([switch]$UseBasicParsing, [string]$Uri, [string]$OutFile)
  [System.IO.File]::WriteAllBytes($OutFile, [byte[]]@(0))
}
function Get-Command {
  param([string]$Name, [object]$ErrorAction)
  if ($Name -eq "python") {
    return [pscustomobject]@{ Name = "python" }
  }
  return $null
}
function python {
  if ($args -contains "-c") {
    Set-Variable -Name LASTEXITCODE -Scope 1 -Value 0
    return
  }
  if ([string]$args[0] -like "*x86qw-bootstrap-materialize.py") {
    Set-Variable -Name LASTEXITCODE -Scope 1 -Value 0
    return
  }
  if ([string]$args[0] -like "*x86qw-bootstrap-extract.py") {
    $HelperRoot = [string]$args[1]
    $WorkDir = Split-Path -Parent $HelperRoot
    $global:X86QWArchiveTempPrivate = (
      $env:TEMP -eq $WorkDir -and $env:TMP -eq $WorkDir
    )
    Set-Variable -Name LASTEXITCODE -Scope 1 -Value 0
    return
  }
  for ($Index = 0; $Index -lt $args.Count; $Index++) {
    if ([string]$args[$Index] -like "*x86qw-bootstrap-download.py") {
      $Archive = [string]$args[$Index + 1]
      Copy-Item -LiteralPath $global:X86QWTestFixture -Destination $Archive
      Set-Variable -Name LASTEXITCODE -Scope 1 -Value 0
      return
    }
  }
  Set-Variable -Name LASTEXITCODE -Scope 1 -Value 7
}
$ErrorActionPreference = "Continue"
$ExpectedConsoleCodePage = [Console]::OutputEncoding.CodePage
$OutputEncoding = [System.Text.Encoding]::ASCII
$ExpectedProcessTemp = [Environment]::GetEnvironmentVariable("TEMP", "Process")
$ExpectedProcessTmp = [Environment]::GetEnvironmentVariable("TMP", "Process")
$Source = Get-Content -LiteralPath $Bootstrap -Raw
Invoke-Expression $Source
Write-Output "X86QW_BOOTSTRAP_SURVIVED:$global:LASTEXITCODE"
Write-Output "X86QW_ERROR_ACTION_AFTER:$ErrorActionPreference"
Write-Output ("X86QW_CONSOLE_ENCODING_RESTORED:" + ([Console]::OutputEncoding.CodePage -eq $ExpectedConsoleCodePage))
Write-Output ("X86QW_PIPELINE_ENCODING_RESTORED:" + ($OutputEncoding.CodePage -eq [System.Text.Encoding]::ASCII.CodePage))
Write-Output ("X86QW_ARCHIVE_TEMP_PRIVATE:" + $global:X86QWArchiveTempPrivate)
Write-Output ("X86QW_PROCESS_TEMP_RESTORED:" + (
  [Environment]::GetEnvironmentVariable("TEMP", "Process") -eq $ExpectedProcessTemp -and
  [Environment]::GetEnvironmentVariable("TMP", "Process") -eq $ExpectedProcessTmp
))
if (Get-Variable -Name InstallerVersion -ErrorAction SilentlyContinue) {
  Write-Output "X86QW_INSTALLER_VERSION_LEAKED"
}
''',
                encoding="utf-8",
            )
            for runtime in POWERSHELL_RUNTIMES:
                with self.subTest(runtime=runtime):
                    completed = subprocess.run(
                        [
                            runtime, "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", str(harness), str(bootstrap), version, str(fixture),
                        ],
                        check=False, capture_output=True, text=True,
                    )
                    self.assertEqual(0, completed.returncode, completed.stderr)
                    self.assertIn("X86QW_BOOTSTRAP_SURVIVED:7", completed.stdout)
                    self.assertIn("X86QW_ERROR_ACTION_AFTER:Continue", completed.stdout)
                    self.assertIn("X86QW_CONSOLE_ENCODING_RESTORED:True", completed.stdout)
                    self.assertIn("X86QW_PIPELINE_ENCODING_RESTORED:True", completed.stdout)
                    self.assertIn("X86QW_ARCHIVE_TEMP_PRIVATE:True", completed.stdout)
                    self.assertIn("X86QW_PROCESS_TEMP_RESTORED:True", completed.stdout)
                    self.assertNotIn("X86QW_INSTALLER_VERSION_LEAKED", completed.stdout)
                    self.assertIn("instalador terminou", completed.stderr)
                    self.assertIn("7", completed.stderr)

    @unittest.skipUnless(POWERSHELL, "PowerShell não está disponível neste runner")
    def test_public_powershell_bootstrap_uses_one_bounded_mirror_controller(self):
        bootstrap = ROOT / "site/public/install.ps1"
        source = bootstrap.read_text(encoding="utf-8")
        version = source.split('$InstallerVersion = "', 1)[1].split('"', 1)[0]
        digest = source.split('$InstallerSha256 = "', 1)[1].split('"', 1)[0]
        self.assertEqual(
            1,
            source.count(
                "$Actual = (Get-FileHash -Algorithm SHA256 "
                "-LiteralPath $Archive).Hash.ToLowerInvariant()"
            ),
        )
        self.assertIn(
            "if ($ArchiveSize -ne $InstallerSize -or "
            "$Actual -ne $InstallerSha256)",
            source,
        )
        fixture = ROOT / f"dist/installer/packages/{version}/x86qw-installer-{version}.zip"
        self.assertEqual(digest, hashlib.sha256(fixture.read_bytes()).hexdigest())
        with tempfile.TemporaryDirectory() as temporary:
            corrupt_fixture = Path(temporary) / fixture.name
            corrupt_bytes = bytearray(fixture.read_bytes())
            corrupt_bytes[0] ^= 0xFF
            corrupt_fixture.write_bytes(corrupt_bytes)
            harness = Path(temporary) / "integrity-fallback-harness.ps1"
            harness.write_text(
                r'''param(
  [string]$Bootstrap,
  [string]$MockVersion,
  [string]$Fixture
)
$global:X86QWTestDownloadCalls = 0
$global:X86QWTestMockVersion = $MockVersion
$global:X86QWTestFixture = $Fixture
function Get-Command {
  param([string]$Name, [object]$ErrorAction)
  if ($Name -eq "python") { return [pscustomobject]@{ Name = "python" } }
  return $null
}
function python {
  if ($args -contains "-c") {
    Set-Variable -Name LASTEXITCODE -Scope 1 -Value 0
    return
  }
  for ($Index = 0; $Index -lt $args.Count; $Index++) {
    if ([string]$args[$Index] -like "*x86qw-bootstrap-download.py") {
      $global:X86QWTestDownloadCalls += 1
      $Archive = [string]$args[$Index + 1]
      Copy-Item -LiteralPath $global:X86QWTestFixture -Destination $Archive
      Set-Variable -Name LASTEXITCODE -Scope 1 -Value 0
      return
    }
  }
  Set-Variable -Name LASTEXITCODE -Scope 1 -Value 0
}
& $Bootstrap "--help"
Write-Output "X86QW_DOWNLOAD_CALLS:$global:X86QWTestDownloadCalls"
''',
                encoding="utf-8",
            )
            for runtime in POWERSHELL_RUNTIMES:
                with self.subTest(runtime=runtime):
                    completed = subprocess.run(
                        [
                            runtime,
                            "-NoProfile",
                            "-ExecutionPolicy",
                            "Bypass",
                            "-File",
                            str(harness),
                            str(bootstrap),
                            version,
                            str(fixture),
                        ],
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(0, completed.returncode, completed.stderr)
                    self.assertIn("X86QW_DOWNLOAD_CALLS:1", completed.stdout)

                    rejected = subprocess.run(
                        [
                            runtime,
                            "-NoProfile",
                            "-ExecutionPolicy",
                            "Bypass",
                            "-File",
                            str(harness),
                            str(bootstrap),
                            version,
                            str(corrupt_fixture),
                        ],
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertNotEqual(0, rejected.returncode)
                    self.assertIn(
                        "downloader retornou um instalador divergente",
                        rejected.stderr,
                    )

    @unittest.skipUnless(POWERSHELL, "PowerShell não está disponível neste runner")
    def test_public_powershell_bootstrap_rejects_store_alias_before_download(self):
        bootstrap = ROOT / "site/public/install.ps1"
        with tempfile.TemporaryDirectory() as temporary:
            harness = Path(temporary) / "missing-python-harness.ps1"
            harness.write_text(
                r'''param([string]$Bootstrap)
function Get-Command {
  param([string]$Name, [object]$ErrorAction)
  if ($Name -eq "python") {
    return [pscustomobject]@{ Name = "python" }
  }
  return $null
}
function python {
  Write-Output "STORE_ALIAS_OUTPUT_MUST_STAY_HIDDEN"
  Set-Variable -Name LASTEXITCODE -Scope 1 -Value 9009
}
function Invoke-WebRequest {
  throw "DOWNLOAD_MUST_NOT_RUN"
}
$Source = Get-Content -LiteralPath $Bootstrap -Raw
try {
  Invoke-Expression $Source
} catch {
  Write-Output ("X86QW_EXPECTED_ERROR:" + $_.Exception.Message)
}
''',
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(harness), str(bootstrap),
                ],
                check=False, capture_output=True, text=True,
            )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("X86QW_EXPECTED_ERROR:x86QW: Python 3.10", completed.stdout)
        self.assertIn("winget install --id Python.Python.3.13 -e", completed.stdout)
        self.assertNotIn("STORE_ALIAS_OUTPUT_MUST_STAY_HIDDEN", completed.stdout)
        self.assertNotIn("DOWNLOAD_MUST_NOT_RUN", completed.stdout)

    @unittest.skipUnless(POWERSHELL, "PowerShell não está disponível neste runner")
    def test_public_powershell_bootstrap_resolves_py_only_and_prefers_it(self):
        bootstrap = ROOT / "site/public/install.ps1"
        source = bootstrap.read_text(encoding="utf-8")
        version = source.split('$InstallerVersion = "', 1)[1].split('"', 1)[0]
        digest = source.split('$InstallerSha256 = "', 1)[1].split('"', 1)[0]
        with tempfile.TemporaryDirectory() as temporary:
            arguments_path = Path(temporary) / "arguments.json"
            harness = Path(temporary) / "py-launcher-harness.ps1"
            harness.write_text(
                r'''param(
  [string]$Bootstrap,
  [string]$MockVersion,
  [string]$MockDigest,
  [string]$ArgumentsPath,
  [string]$CandidateMode
)
function Get-Command {
  param([string]$Name, [object]$ErrorAction)
  if ($Name -eq "py" -or ($CandidateMode -eq "all" -and $Name -in @("python3", "python"))) {
    return [pscustomobject]@{ Name = $Name }
  }
  return $null
}
function Invoke-WebRequest {
  param([switch]$UseBasicParsing, [string]$Uri, [string]$OutFile)
  [System.IO.File]::WriteAllBytes($OutFile, [byte[]]@(0))
}
function Get-FileHash {
  param([string]$Algorithm, [string]$Path, [string]$LiteralPath)
  [pscustomobject]@{ Hash = $MockDigest }
}
function py {
  if ($args -contains "-c") {
    Set-Variable -Name LASTEXITCODE -Scope 1 -Value 0
    return
  }
  for ($Index = 0; $Index -lt $args.Count; $Index++) {
    if ([string]$args[$Index] -like "*x86qw-bootstrap-download.py") {
      $Archive = [string]$args[$Index + 1]
      $Size = [int]$args[$Index + 2]
      [System.IO.File]::WriteAllBytes($Archive, (New-Object byte[] $Size))
      Set-Variable -Name LASTEXITCODE -Scope 1 -Value 0
      return
    }
  }
  [System.IO.File]::WriteAllText($ArgumentsPath, ($args | ConvertTo-Json -Compress))
  Set-Variable -Name LASTEXITCODE -Scope 1 -Value 0
}
function python3 { throw "python3 nao deveria ser sondado depois de py" }
function python { throw "python nao deveria ser sondado depois de py" }
$UnicodeTarget = "C:\Jogos\Usu" + [char]0x00e1 + "rios com espa" + [char]0x00e7 + "os"
& $Bootstrap "--target" $UnicodeTarget
''',
                encoding="utf-8",
            )
            for candidate_mode in ("py-only", "all"):
                with self.subTest(candidate_mode=candidate_mode):
                    completed = subprocess.run(
                        [
                            POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", str(harness), str(bootstrap), version, digest,
                            str(arguments_path), candidate_mode,
                        ],
                        check=False, capture_output=True, text=True,
                    )
                    received = json.loads(arguments_path.read_text(encoding="utf-8"))
                    self.assertEqual(0, completed.returncode, completed.stderr)
                    self.assertEqual("-3", received[0])
                    self.assertTrue(received[1].endswith("x86qw.pyz"), received)
                    self.assertEqual("--online-only", received[2])
                    self.assertEqual(
                        ["--target", r"C:\Jogos\Usuários com espaços"],
                        received[3:],
                    )

    @unittest.skipUnless(POWERSHELL, "PowerShell não está disponível neste runner")
    def test_public_powershell_bootstrap_accepts_python3_without_py(self):
        bootstrap = ROOT / "site/public/install.ps1"
        source = bootstrap.read_text(encoding="utf-8")
        version = source.split('$InstallerVersion = "', 1)[1].split('"', 1)[0]
        digest = source.split('$InstallerSha256 = "', 1)[1].split('"', 1)[0]
        with tempfile.TemporaryDirectory() as temporary:
            arguments_path = Path(temporary) / "arguments.json"
            harness = Path(temporary) / "python3-harness.ps1"
            harness.write_text(
                r'''param(
  [string]$Bootstrap,
  [string]$MockVersion,
  [string]$MockDigest,
  [string]$ArgumentsPath
)
function Get-Command {
  param([string]$Name, [object]$ErrorAction)
  if ($Name -eq "python3") { return [pscustomobject]@{ Name = "python3" } }
  return $null
}
function Invoke-WebRequest {
  param([switch]$UseBasicParsing, [string]$Uri, [string]$OutFile)
  [System.IO.File]::WriteAllBytes($OutFile, [byte[]]@(0))
}
function Get-FileHash {
  param([string]$Algorithm, [string]$Path, [string]$LiteralPath)
  [pscustomobject]@{ Hash = $MockDigest }
}
function python3 {
  if ($args -contains "-c") {
    Set-Variable -Name LASTEXITCODE -Scope 1 -Value 0
    return
  }
  for ($Index = 0; $Index -lt $args.Count; $Index++) {
    if ([string]$args[$Index] -like "*x86qw-bootstrap-download.py") {
      $Archive = [string]$args[$Index + 1]
      $Size = [int]$args[$Index + 2]
      [System.IO.File]::WriteAllBytes($Archive, (New-Object byte[] $Size))
      Set-Variable -Name LASTEXITCODE -Scope 1 -Value 0
      return
    }
  }
  [System.IO.File]::WriteAllText($ArgumentsPath, ($args | ConvertTo-Json -Compress))
  Set-Variable -Name LASTEXITCODE -Scope 1 -Value 0
}
$Source = Get-Content -LiteralPath $Bootstrap -Raw
Invoke-Expression $Source
''',
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(harness), str(bootstrap), version, digest,
                    str(arguments_path),
                ],
                check=False, capture_output=True, text=True,
            )
            received = json.loads(arguments_path.read_text(encoding="utf-8"))
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertTrue(received[0].endswith("x86qw.pyz"), received)
        self.assertEqual("--online-only", received[1])

    @unittest.skipUnless(POWERSHELL, "PowerShell não está disponível neste runner")
    def test_public_powershell_bootstrap_rejects_python_39_before_download(self):
        bootstrap = ROOT / "site/public/install.ps1"
        with tempfile.TemporaryDirectory() as temporary:
            harness = Path(temporary) / "python39-harness.ps1"
            harness.write_text(
                r'''param([string]$Bootstrap)
function Get-Command {
  param([string]$Name, [object]$ErrorAction)
  if ($Name -eq "python") { return [pscustomobject]@{ Name = "python" } }
  return $null
}
function python {
  Set-Variable -Name LASTEXITCODE -Scope 1 -Value 1
}
function Invoke-WebRequest { throw "DOWNLOAD_MUST_NOT_RUN" }
$Source = Get-Content -LiteralPath $Bootstrap -Raw
try {
  Invoke-Expression $Source
} catch {
  Write-Output ("X86QW_EXPECTED_ERROR:" + $_.Exception.Message)
}
''',
                encoding="utf-8",
            )
            completed = subprocess.run(
                [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness), str(bootstrap)],
                check=False, capture_output=True, text=True,
            )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("X86QW_EXPECTED_ERROR:x86QW: Python 3.10", completed.stdout)
        self.assertNotIn("DOWNLOAD_MUST_NOT_RUN", completed.stdout + completed.stderr)

    def test_public_powershell_bootstrap_is_ascii_safe_for_windows_powershell(self):
        source = (ROOT / "site/public/install.ps1").read_text(encoding="utf-8")
        source.encode("ascii")
        self.assertIn('[Console]::OutputEncoding = $Utf8Encoding', source)
        self.assertIn('Command = "py"; Arguments = @("-3")', source)
        self.assertIn("Python 3.10 ou mais recente nao foi encontrado", source)


if __name__ == "__main__":
    unittest.main()
