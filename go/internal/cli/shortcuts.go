package cli

import (
	"context"
	_ "embed"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

// Windows Start Menu and Desktop shortcuts: both point at
// the installation's x86qw.exe with the project icon (one that points at
// the x86qw.cmd of the layout before 2.5 is ours too); a shortcut that
// points elsewhere is never replaced or removed.

//go:embed assets/x86qw.ico
var icon []byte

const shortcutScript = `param(
  [ValidateSet("install", "remove", "legacy")][string]$Mode,
  [string]$Launcher,
  [string]$Legacy,
  [string]$Icon,
  [string]$StartMenuShortcut,
  [string]$DesktopShortcut
)
$ErrorActionPreference = "Stop"
$Shell = New-Object -ComObject WScript.Shell
$LauncherFull = [IO.Path]::GetFullPath($Launcher)
$Ours = @($LauncherFull, [IO.Path]::GetFullPath($Legacy))
foreach ($Path in @($StartMenuShortcut, $DesktopShortcut)) {
  if ($Mode -eq "legacy") {
    if ((Test-Path -LiteralPath $Path -PathType Leaf) -and
        [IO.Path]::GetFullPath($Shell.CreateShortcut($Path).TargetPath) -eq [IO.Path]::GetFullPath($Legacy)) {
      Write-Output $Path
    }
    continue
  }
  if ($Mode -eq "remove") {
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
      $Existing = $Shell.CreateShortcut($Path)
      if ($Ours -contains [IO.Path]::GetFullPath($Existing.TargetPath)) {
        Remove-Item -LiteralPath $Path -Force
      }
    }
    continue
  }
  if (Test-Path -LiteralPath $Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
      throw "The shortcut path is not a file: $Path"
    }
    $Existing = $Shell.CreateShortcut($Path)
    if ($Ours -notcontains [IO.Path]::GetFullPath($Existing.TargetPath)) {
      throw "An unmanaged shortcut is already at: $Path"
    }
  }
  $Parent = Split-Path -Parent $Path
  [IO.Directory]::CreateDirectory($Parent) | Out-Null
  $Shortcut = $Shell.CreateShortcut($Path)
  $Shortcut.TargetPath = $LauncherFull
  $Shortcut.WorkingDirectory = [IO.Path]::GetDirectoryName($LauncherFull)
  $Shortcut.IconLocation = "$Icon,0"
  $Shortcut.Description = "x86QW - QuakeWorld"
  $Shortcut.Save()
}
`

// ShortcutPaths are the Start Menu and Desktop shortcut locations.
func ShortcutPaths() ([2]string, error) {
	appdata, profile := os.Getenv("APPDATA"), os.Getenv("USERPROFILE")
	if appdata == "" || profile == "" {
		return [2]string{}, errors.New("APPDATA and USERPROFILE are needed to create shortcuts.")
	}
	return [2]string{
		filepath.Join(appdata, "Microsoft", "Windows", "Start Menu", "Programs", "x86QW.lnk"),
		filepath.Join(profile, "Desktop", "x86QW.lnk"),
	}, nil
}

func runShortcutScript(mode, root, iconPath string, shortcuts [2]string) error {
	_, err := shortcutScriptOutput(mode, root, iconPath, shortcuts)
	return err
}

// shortcutScriptOutput runs the shortcut script and returns what it printed.
func shortcutScriptOutput(mode, root, iconPath string, shortcuts [2]string) (string, error) {
	powershell, err := exec.LookPath("powershell.exe")
	if err != nil {
		return "", errors.New("Windows PowerShell is not available to create shortcuts.")
	}
	dir, err := os.MkdirTemp("", "x86qw-shortcut.")
	if err != nil {
		return "", err
	}
	defer os.RemoveAll(dir)
	script := filepath.Join(dir, "shortcut.ps1")
	payload := "\ufeff" + strings.ReplaceAll(shortcutScript, "\n", "\r\n")
	if err := os.WriteFile(script, []byte(payload), 0o600); err != nil {
		return "", err
	}
	// A cold PowerShell behind an antivirus can take half a minute to start.
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	command := exec.CommandContext(ctx, powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script,
		"-Mode", mode, "-Launcher", Command(root), "-Legacy", filepath.Join(root, "x86qw.cmd"), "-Icon", iconPath,
		"-StartMenuShortcut", shortcuts[0], "-DesktopShortcut", shortcuts[1])
	output, err := command.CombinedOutput()
	if err != nil {
		detail := strings.TrimSpace(string(output))
		switch {
		case ctx.Err() != nil:
			detail = "PowerShell did not answer within 2 minutes"
		case detail == "":
			detail = err.Error()
		}
		return "", fmt.Errorf("Could not set up the Windows shortcuts: %s", detail)
	}
	return string(output), nil
}

// InstallShortcuts writes the icon next to the CLI and creates both
// shortcuts to root\x86qw.exe; it returns the Start Menu shortcut.
func InstallShortcuts(root string) (string, error) {
	shortcuts, err := ShortcutPaths()
	if err != nil {
		return "", err
	}
	iconPath := filepath.Join(root, filepath.FromSlash(cliDir), "x86qw.ico")
	if err := os.WriteFile(iconPath, icon, 0o644); err != nil {
		return "", err
	}
	if err := runShortcutScript("install", root, iconPath, shortcuts); err != nil {
		return "", err
	}
	return shortcuts[0], nil
}

// RemoveShortcuts deletes the shortcuts that point at root's CLI, or at
// its x86qw.cmd before 2.5.
func RemoveShortcuts(root string) error {
	shortcuts, err := ShortcutPaths()
	if err != nil {
		return err
	}
	return runShortcutScript("remove", root, filepath.Join(root, filepath.FromSlash(cliDir), "x86qw.ico"), shortcuts)
}
