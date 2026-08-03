import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dist/installer/bin"))

import services  # noqa: E402


WINDOWS_POWERSHELL = shutil.which("powershell.exe") or shutil.which("powershell")


@unittest.skipUnless(os.name == "nt", "sharing guards are exercised on Windows")
class WindowsDeleteGuardTests(unittest.TestCase):
    def test_active_installation_lock_prevents_control_plane_rename(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "quake-world"
            target.mkdir()
            lock = services.SessionLock.acquire(target, "host")
            control = target / ".x86qw"
            moved = target / ".x86qw-moved"
            renamed = False
            try:
                try:
                    os.replace(control, moved)
                    renamed = True
                except OSError:
                    pass
                self.assertFalse(
                    renamed,
                    "the active control plane was renamed while its lock was alive",
                )
            finally:
                if renamed:
                    os.replace(moved, control)
                lock.release()

            os.replace(control, moved)
            self.assertTrue(moved.is_dir())

    def test_sensitive_config_cannot_be_renamed_before_session_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "quake-world"
            target.mkdir()
            game = target / "qw"
            game.mkdir()
            journal = services.SessionJournal(target)
            config = services.temporary_config(
                game, "x86qw-sensitive-", ["rcon_password segredo"], journal,
                sensitive=True,
            )
            moved = game / "replacement.cfg"
            renamed = False
            try:
                try:
                    os.replace(config, moved)
                    renamed = True
                except OSError:
                    pass
                self.assertFalse(
                    renamed,
                    "a sensitive config was renamed while its session was alive",
                )
            finally:
                if renamed:
                    os.replace(moved, config)
                services.cleanup_current_session(journal, [config], [])
            self.assertFalse(config.exists())

    @unittest.skipUnless(WINDOWS_POWERSHELL, "Windows PowerShell is unavailable")
    def test_bootstrap_workdir_cannot_be_renamed_during_download(self):
        bootstrap = ROOT / "site/public/install.ps1"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            broad_temporary = root / "broad-temporary"
            broad_temporary.mkdir()
            report = root / "delete-guard.json"
            harness = root / "delete-guard-harness.ps1"
            harness.write_text(
                r'''param(
  [string]$Bootstrap,
  [string]$BroadTemporary,
  [string]$Report
)
$Icacls = Join-Path $env:SystemRoot "System32\icacls.exe"
& $Icacls $BroadTemporary "/inheritance:r" "/grant:r" "*S-1-1-0:(OI)(CI)F" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "cannot prepare broad temporary directory" }
$env:TEMP = $BroadTemporary
$env:TMP = $BroadTemporary

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
      $WorkDir = Split-Path -Parent ([string]$args[$Index])
      $Moved = $WorkDir + "-moved"
      $Blocked = $false
      try {
        [System.IO.Directory]::Move($WorkDir, $Moved)
        [System.IO.Directory]::Move($Moved, $WorkDir)
      } catch {
        $Blocked = $true
      }
      [System.IO.File]::WriteAllText(
        $Report,
        (([ordered]@{ blocked = $Blocked }) | ConvertTo-Json -Compress),
        (New-Object System.Text.UTF8Encoding($false))
      )
      Set-Variable -Name LASTEXITCODE -Scope 1 -Value 1
      return
    }
  }
  Set-Variable -Name LASTEXITCODE -Scope 1 -Value 1
}

$Source = Get-Content -LiteralPath $Bootstrap -Raw
try { Invoke-Expression $Source } catch { }
''',
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    WINDOWS_POWERSHELL,
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
                timeout=30,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            observed = json.loads(report.read_text(encoding="utf-8"))
            self.assertTrue(
                observed["blocked"],
                "the bootstrap work directory was renamed while in use",
            )


if __name__ == "__main__":
    unittest.main()
