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
// PATH changes; entries are compared without case or trailing slash.
const userPathScript = `param(
  [ValidateSet("add", "remove")][string]$Mode,
  [string]$Dir
)
$ErrorActionPreference = "Stop"
$Wanted = $Dir.TrimEnd('\')
$Current = [Environment]::GetEnvironmentVariable("Path", "User")
$Parts = @()
if ($Current) { $Parts = @($Current -split ';' | Where-Object { $_ -ne '' -and $_.TrimEnd('\') -ine $Wanted }) }
if ($Mode -eq "add") { $Parts += $Wanted }
[Environment]::SetEnvironmentVariable("Path", ($Parts -join ';'), "User")
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
