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
// the installation's x86qw.cmd with the project icon; a shortcut that
// points elsewhere is never replaced or removed.

//go:embed assets/x86qw.ico
var icon []byte

const shortcutScript = `param(
  [ValidateSet("install", "remove")][string]$Mode,
  [string]$Launcher,
  [string]$Icon,
  [string]$StartMenuShortcut,
  [string]$DesktopShortcut
)
$ErrorActionPreference = "Stop"
$Shell = New-Object -ComObject WScript.Shell
$LauncherFull = [IO.Path]::GetFullPath($Launcher)
foreach ($Path in @($StartMenuShortcut, $DesktopShortcut)) {
  if ($Mode -eq "remove") {
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
      $Existing = $Shell.CreateShortcut($Path)
      if ([IO.Path]::GetFullPath($Existing.TargetPath) -eq $LauncherFull) {
        Remove-Item -LiteralPath $Path -Force
      }
    }
    continue
  }
  if (Test-Path -LiteralPath $Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
      throw "O caminho do atalho nao e um arquivo: $Path"
    }
    $Existing = $Shell.CreateShortcut($Path)
    if ([IO.Path]::GetFullPath($Existing.TargetPath) -ne $LauncherFull) {
      throw "Um atalho nao gerenciado ja ocupa: $Path"
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
		return [2]string{}, errors.New("APPDATA e USERPROFILE são necessários para criar atalhos.")
	}
	return [2]string{
		filepath.Join(appdata, "Microsoft", "Windows", "Start Menu", "Programs", "x86QW.lnk"),
		filepath.Join(profile, "Desktop", "x86QW.lnk"),
	}, nil
}

func runShortcutScript(mode, launcher, iconPath string, shortcuts [2]string) error {
	powershell, err := exec.LookPath("powershell.exe")
	if err != nil {
		return errors.New("Windows PowerShell não está disponível para criar atalhos.")
	}
	dir, err := os.MkdirTemp("", "x86qw-shortcut.")
	if err != nil {
		return err
	}
	defer os.RemoveAll(dir)
	script := filepath.Join(dir, "shortcut.ps1")
	payload := "\ufeff" + strings.ReplaceAll(shortcutScript, "\n", "\r\n")
	if err := os.WriteFile(script, []byte(payload), 0o600); err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	command := exec.CommandContext(ctx, powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script,
		"-Mode", mode, "-Launcher", launcher, "-Icon", iconPath, "-StartMenuShortcut", shortcuts[0], "-DesktopShortcut", shortcuts[1])
	output, err := command.CombinedOutput()
	if err != nil {
		detail := strings.TrimSpace(string(output))
		if detail == "" {
			detail = err.Error()
		}
		return fmt.Errorf("Não foi possível configurar os atalhos do Windows: %s", detail)
	}
	return nil
}

// InstallShortcuts writes the icon next to the CLI and creates both
// shortcuts to root\x86qw.cmd; it returns the Start Menu shortcut.
func InstallShortcuts(root string) (string, error) {
	shortcuts, err := ShortcutPaths()
	if err != nil {
		return "", err
	}
	iconPath := filepath.Join(root, filepath.FromSlash(cliDir), "x86qw.ico")
	if err := os.WriteFile(iconPath, icon, 0o644); err != nil {
		return "", err
	}
	if err := runShortcutScript("install", filepath.Join(root, "x86qw.cmd"), iconPath, shortcuts); err != nil {
		return "", err
	}
	return shortcuts[0], nil
}

// RemoveShortcuts deletes the shortcuts that point at root\x86qw.cmd.
func RemoveShortcuts(root string) error {
	shortcuts, err := ShortcutPaths()
	if err != nil {
		return err
	}
	return runShortcutScript("remove", filepath.Join(root, "x86qw.cmd"), filepath.Join(root, filepath.FromSlash(cliDir), "x86qw.ico"), shortcuts)
}
