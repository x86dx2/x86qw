package cli

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

// The Windows user PATH gets the installation folder, where x86qw.cmd
// lives, so a new terminal finds the x86qw command. Only the user's own
// PATH changes, read and written in the registry as stored: entries such
// as %USERPROFILE%\bin stay unexpanded and the value keeps its kind.
// Entries are compared without case or trailing backslash.
const userPathScript = `param(
  [ValidateSet("add", "remove")][string]$Mode,
  [string]$Dir
)
$ErrorActionPreference = "Stop"
$Wanted = $Dir.TrimEnd('\')
$Key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey("Environment")
try {
  $Kind = [Microsoft.Win32.RegistryValueKind]::ExpandString
  $Current = ""
  if ($Key.GetValueNames() -contains "Path") {
    $Kind = $Key.GetValueKind("Path")
    $Current = [string]$Key.GetValue("Path", "", [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
  }
  $Parts = @($Current -split ';' | Where-Object { $_ -ne '' -and $_.TrimEnd('\') -ine $Wanted })
  if ($Mode -eq "add") { $Parts += $Wanted }
  $Key.SetValue("Path", ($Parts -join ';'), $Kind)
} finally {
  $Key.Close()
}
# Tell running programs the environment changed, so new terminals see it.
[Environment]::SetEnvironmentVariable("X86QW_PATH_REFRESH", "1", "User")
[Environment]::SetEnvironmentVariable("X86QW_PATH_REFRESH", $null, "User")
`

// AddUserPath puts root on the Windows user PATH.
func AddUserPath(root string) error { return runUserPathScript("add", root) }

// RemoveUserPath takes root off the Windows user PATH.
func RemoveUserPath(root string) error { return runUserPathScript("remove", root) }

func runUserPathScript(mode, root string) error {
	powershell, err := exec.LookPath("powershell.exe")
	if err != nil {
		return errors.New("Windows PowerShell não está disponível para ajustar o PATH.")
	}
	dir, err := os.MkdirTemp("", "x86qw-path.")
	if err != nil {
		return err
	}
	defer os.RemoveAll(dir)
	script := filepath.Join(dir, "path.ps1")
	if err := os.WriteFile(script, []byte("\ufeff"+strings.ReplaceAll(userPathScript, "\n", "\r\n")), 0o600); err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	output, err := exec.CommandContext(ctx, powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script,
		"-Mode", mode, "-Dir", root).CombinedOutput()
	if err != nil {
		detail := strings.TrimSpace(string(output))
		if detail == "" {
			detail = err.Error()
		}
		return fmt.Errorf("Não foi possível ajustar o PATH do usuário: %s", detail)
	}
	return nil
}
