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
	return removeWhenGone([]int{os.Getpid()}, aside)
}

// deferLauncher keeps x86qw.cmd in place while the cmd.exe running it is
// alive (cmd.exe rereads the batch file after each command, so deleting it
// fails the launcher with "The batch file cannot be found") and removes it
// once this process and its parent have exited.
func deferLauncher(path string) bool {
	if !strings.EqualFold(filepath.Base(path), "x86qw.cmd") {
		return false
	}
	return removeWhenGone([]int{os.Getpid(), os.Getppid()}, path)
}

// removeWhenGone starts a hidden PowerShell that waits (at most a minute)
// for pids to exit and then removes path, and its folder if that is left
// empty (the installation folder after a purge).
func removeWhenGone(pids []int, path string) bool {
	ids := make([]string, len(pids))
	for index, pid := range pids {
		ids[index] = fmt.Sprint(pid)
	}
	quote := func(path string) string { return "'" + strings.ReplaceAll(path, "'", "''") + "'" }
	script := fmt.Sprintf("Wait-Process -Id %s -Timeout 60 -ErrorAction SilentlyContinue; Remove-Item -LiteralPath %s -Recurse -Force; "+
		"try { [IO.Directory]::Delete(%s) } catch {}", strings.Join(ids, ","), quote(path), quote(filepath.Dir(path)))
	command := exec.Command("powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script)
	// Its working folder must not be the one it removes.
	command.Dir = filepath.Dir(filepath.Dir(path))
	command.SysProcAttr = &syscall.SysProcAttr{CreationFlags: windows.CREATE_NO_WINDOW | windows.CREATE_NEW_PROCESS_GROUP}
	if err := command.Start(); err != nil {
		return false
	}
	command.Process.Release()
	return true
}
