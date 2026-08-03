from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CAPABILITIES = ROOT / "maintenance/inventory/capabilities.json"
PRODUCT = ROOT / "site/public/api/v1/product.json"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell.exe")


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
                (["uninstall", "--help"], ["--online-only", "--installed-cli", "uninstall", str(root), "--help"]),
            ):
                with self.subTest(arguments=arguments):
                    completed = subprocess.run(
                        [str(launcher), *arguments], env=environment,
                        check=False, capture_output=True,
                    )
                    self.assertEqual(0, completed.returncode)
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
                (["uninstall", "--help"], ["--online-only", "--installed-cli", "uninstall", "--help"], True),
            ):
                with self.subTest(arguments=arguments):
                    completed = subprocess.run(
                        [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "call", str(launcher), *arguments],
                        env=dict(environment, X86QW_STUB_EXIT="0"), check=False,
                        capture_output=True,
                    )
                    self.assertEqual(0, completed.returncode)
                    received = json.loads(output.read_text(encoding="utf-8"))
                    if target_at_end:
                        self.assertEqual(prefix, received[:-1])
                        self.assertEqual(root.resolve(), Path(received[-1]).resolve())
                    else:
                        self.assertEqual(prefix, received)

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
output=
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-o" ]; then output=$2; shift 2; else shift; fi
done
exec /bin/cp "$X86QW_TEST_BUNDLE" "$output"
""",
                    encoding="utf-8",
                )
                curl.chmod(0o755)
                for command in ("unzip", "mktemp", "rm"):
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
                for command in ("curl", "mktemp", "unzip"):
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
                    f"if [ \"${{1:-}}\" = \"-\" ]; then exec {shlex.quote(sys.executable)} \"$@\"; fi\n"
                    "exit 23\n",
                    encoding="utf-8",
                )
                runtime.chmod(0o755)
                curl = binaries / "curl"
                curl.write_text(
                    """#!/bin/sh
output=
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-o" ]; then output=$2; shift 2; else shift; fi
done
exec /bin/cp "$X86QW_TEST_BUNDLE" "$output"
""",
                    encoding="utf-8",
                )
                curl.chmod(0o755)
                for command in ("unzip", "mktemp", "rm"):
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
        self.assertIn("stream.read(1024 * 1024)", source)
        self.assertNotIn('open(sys.argv[1],"rb").read()', source)

    @unittest.skipUnless(POWERSHELL, "PowerShell não está disponível neste runner")
    def test_public_powershell_bootstrap_preserves_the_calling_session(self):
        bootstrap = ROOT / "site/public/install.ps1"
        source = bootstrap.read_text(encoding="utf-8")
        version = source.split('$InstallerVersion = "', 1)[1].split('"', 1)[0]
        digest = source.split('$InstallerSha256 = "', 1)[1].split('"', 1)[0]
        with tempfile.TemporaryDirectory() as temporary:
            harness = Path(temporary) / "bootstrap-harness.ps1"
            harness.write_text(
                r'''param(
  [string]$Bootstrap,
  [string]$MockVersion,
  [string]$MockDigest
)
function Invoke-WebRequest {
  param([switch]$UseBasicParsing, [string]$Uri, [string]$OutFile)
  [System.IO.File]::WriteAllBytes($OutFile, [byte[]]@(0))
}
function Get-FileHash {
  param([string]$Algorithm, [string]$Path)
  [pscustomobject]@{ Hash = $MockDigest }
}
function Expand-Archive {
  param([string]$Path, [string]$DestinationPath)
  $Root = Join-Path $DestinationPath ("x86qw-installer-" + $MockVersion)
  New-Item -ItemType Directory -Path $Root | Out-Null
  New-Item -ItemType File -Path (Join-Path $Root "x86qw.pyz") | Out-Null
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
  Set-Variable -Name LASTEXITCODE -Scope 1 -Value 7
}
$ErrorActionPreference = "Continue"
$ExpectedConsoleCodePage = [Console]::OutputEncoding.CodePage
$OutputEncoding = [System.Text.Encoding]::ASCII
$Source = Get-Content -LiteralPath $Bootstrap -Raw
Invoke-Expression $Source
Write-Output "X86QW_BOOTSTRAP_SURVIVED:$global:LASTEXITCODE"
Write-Output "X86QW_ERROR_ACTION_AFTER:$ErrorActionPreference"
Write-Output ("X86QW_CONSOLE_ENCODING_RESTORED:" + ([Console]::OutputEncoding.CodePage -eq $ExpectedConsoleCodePage))
Write-Output ("X86QW_PIPELINE_ENCODING_RESTORED:" + ($OutputEncoding.CodePage -eq [System.Text.Encoding]::ASCII.CodePage))
if (Get-Variable -Name InstallerVersion -ErrorAction SilentlyContinue) {
  Write-Output "X86QW_INSTALLER_VERSION_LEAKED"
}
''',
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(harness), str(bootstrap), version, digest,
                ],
                check=False, capture_output=True, text=True,
            )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("X86QW_BOOTSTRAP_SURVIVED:7", completed.stdout)
        self.assertIn("X86QW_ERROR_ACTION_AFTER:Continue", completed.stdout)
        self.assertIn("X86QW_CONSOLE_ENCODING_RESTORED:True", completed.stdout)
        self.assertIn("X86QW_PIPELINE_ENCODING_RESTORED:True", completed.stdout)
        self.assertNotIn("X86QW_INSTALLER_VERSION_LEAKED", completed.stdout)
        self.assertIn("instalador terminou", completed.stderr)
        self.assertIn("7", completed.stderr)

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
  param([string]$Algorithm, [string]$Path)
  [pscustomobject]@{ Hash = $MockDigest }
}
function Expand-Archive {
  param([string]$Path, [string]$DestinationPath)
  $Root = Join-Path $DestinationPath ("x86qw-installer-" + $MockVersion)
  New-Item -ItemType Directory -Path $Root | Out-Null
  New-Item -ItemType File -Path (Join-Path $Root "x86qw.pyz") | Out-Null
}
function py {
  if ($args -contains "-c") {
    Set-Variable -Name LASTEXITCODE -Scope 1 -Value 0
    return
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
  param([string]$Algorithm, [string]$Path)
  [pscustomobject]@{ Hash = $MockDigest }
}
function Expand-Archive {
  param([string]$Path, [string]$DestinationPath)
  $Root = Join-Path $DestinationPath ("x86qw-installer-" + $MockVersion)
  New-Item -ItemType Directory -Path $Root | Out-Null
  New-Item -ItemType File -Path (Join-Path $Root "x86qw.pyz") | Out-Null
}
function python3 {
  if ($args -contains "-c") {
    Set-Variable -Name LASTEXITCODE -Scope 1 -Value 0
    return
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
