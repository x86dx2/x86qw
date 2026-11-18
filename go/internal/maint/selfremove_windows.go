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
	// However long the app that ran the uninstall stays open.
	return removeWhenGone([]int{os.Getpid()}, aside, 0)
}

// deferLauncher keeps x86qw.cmd in place while the cmd.exe running it is
// alive (cmd.exe rereads the batch file after each command, so deleting it
// fails the launcher with "The batch file cannot be found") and removes it
// once this process and its parent have exited.
func deferLauncher(path string) bool {
	if !strings.EqualFold(filepath.Base(path), "x86qw.cmd") {
		return false
	}
	// An interactive cmd.exe outlives the batch file: a minute is enough.
	return removeWhenGone([]int{os.Getpid(), os.Getppid()}, path, 60)
}

// removeWhenGone starts a hidden PowerShell that runs removalScript.
func removeWhenGone(pids []int, path string, limit int) bool {
	command := exec.Command("powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", removalScript(pids, path, limit))
	// Its working folder must not be one it removes.
	command.Dir = os.TempDir()
	command.SysProcAttr = &syscall.SysProcAttr{CreationFlags: windows.CREATE_NO_WINDOW | windows.CREATE_NEW_PROCESS_GROUP}
	if err := command.Start(); err != nil {
		return false
	}
	command.Process.Release()
	return true
}

// removalScript waits for pids to exit (at most limit seconds, when limit is
// not 0) and then removes path, and its folder if that is left empty (the
// installation folder after a purge), and then an empty ~/Games.
func removalScript(pids []int, path string, limit int) string {
	ids := make([]string, len(pids))
	for index, pid := range pids {
		ids[index] = fmt.Sprint(pid)
	}
	wait := "Wait-Process -Id " + strings.Join(ids, ",")
	if limit > 0 {
		wait += fmt.Sprintf(" -Timeout %d", limit)
	}
	quote := func(path string) string { return "'" + strings.ReplaceAll(path, "'", "''") + "'" }
	folder := filepath.Dir(path)
	remove := "[IO.Directory]::Delete(" + quote(folder) + ")"
	if parent := filepath.Dir(folder); isGamesFolder(parent) {
		remove += "; [IO.Directory]::Delete(" + quote(parent) + ")"
	}
	return fmt.Sprintf("%s -ErrorAction SilentlyContinue; Remove-Item -LiteralPath %s -Recurse -Force; "+
		"try { %s } catch {}", wait, quote(path), remove)
}

// removesItself is never true on Windows, where a running executable cannot
// be removed: it is set aside and goes once it exits.
func removesItself(path string) bool { return false }
