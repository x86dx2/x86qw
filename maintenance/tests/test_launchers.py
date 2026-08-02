from __future__ import annotations

import json
import os
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
            root = Path(temporary) / "install with spaces"
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

    @unittest.skipIf(os.name == "nt", "launcher Unix é exercitado nos runners POSIX")
    def test_unix_launcher_opens_the_navigator_without_arguments(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "install with spaces"
            launcher, _ = self.prepare_launcher(root, "x86qw.sh")
            output = root / "arguments.json"
            environment = dict(os.environ, X86QW_STUB_OUTPUT=str(output))
            completed = subprocess.run([str(launcher)], env=environment, check=False)
            self.assertEqual(0, completed.returncode)
            self.assertEqual(
                ["menu", str(root)],
                json.loads(output.read_text(encoding="utf-8")),
            )

    @unittest.skipUnless(os.name == "nt", "cmd.exe é exercitado somente no runner Windows")
    def test_windows_launcher_forwards_more_than_nine_arguments_and_exit_code(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "install with spaces"
            launcher, _ = self.prepare_launcher(root, "x86qw.cmd")
            output = root / "arguments.json"
            environment = dict(os.environ, X86QW_STUB_OUTPUT=str(output))
            arguments = [
                "play", "ktx", "--mode", "duel", "--map", "dm6",
                "--bots", "2", "--bot-skill", "8", "--no-color",
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
  if ($args -contains "--version") {
    Write-Output "Python 3.13.0"
    Set-Variable -Name LASTEXITCODE -Scope 1 -Value 0
    return
  }
  Set-Variable -Name LASTEXITCODE -Scope 1 -Value 7
}
$Source = Get-Content -LiteralPath $Bootstrap -Raw
Invoke-Expression $Source
Write-Output "X86QW_BOOTSTRAP_SURVIVED:$global:LASTEXITCODE"
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
    def test_public_powershell_bootstrap_prefers_py_launcher_with_python_3(self):
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
  [string]$ArgumentsPath
)
function Get-Command {
  param([string]$Name, [object]$ErrorAction)
  if ($Name -eq "py") {
    return [pscustomobject]@{ Name = "py" }
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
  if ($args -contains "--version") {
    Write-Output "Python 3.13.0"
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
        self.assertEqual("-3", received[0])
        self.assertTrue(received[1].endswith("x86qw.pyz"), received)
        self.assertEqual("--online-only", received[2])

    def test_public_powershell_bootstrap_is_ascii_safe_for_windows_powershell(self):
        source = (ROOT / "site/public/install.ps1").read_text(encoding="utf-8")
        source.encode("ascii")
        self.assertIn('[Console]::OutputEncoding = $Utf8Encoding', source)
        self.assertIn('Command = "py"; Arguments = @("-3")', source)
        self.assertIn("Python 3.10 ou mais recente nao foi encontrado", source)


if __name__ == "__main__":
    unittest.main()
