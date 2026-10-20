//go:build windows

package maint

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"

	"golang.org/x/sys/windows"
)

// removeAfterExit handles the one path Windows refuses to delete during
// uninstall: original moved to aside while it holds this running x86qw.exe
// (os.Executable keeps reporting the path the image was loaded from). A
// detached, windowless PowerShell waits for this process to exit and removes
// aside; true means the removal was scheduled.
func removeAfterExit(original, aside string) bool {
	executable, err := os.Executable()
	if err != nil {
		return false
	}
	relative, err := filepath.Rel(original, executable)
	if err != nil || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
		return false
	}
	// Wait for this process to exit (at most a minute), then remove aside.
	script := fmt.Sprintf("Wait-Process -Id %d -Timeout 60 -ErrorAction SilentlyContinue; Remove-Item -LiteralPath '%s' -Recurse -Force",
		os.Getpid(), strings.ReplaceAll(aside, "'", "''"))
	command := exec.Command("powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script)
	command.SysProcAttr = &syscall.SysProcAttr{CreationFlags: windows.CREATE_NO_WINDOW | windows.CREATE_NEW_PROCESS_GROUP}
	if err := command.Start(); err != nil {
		return false
	}
	command.Process.Release()
	return true
}
