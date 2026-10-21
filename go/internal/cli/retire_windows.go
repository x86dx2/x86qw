//go:build windows

package cli

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"

	"golang.org/x/sys/windows"
)

// retireBatch removes x86qw.cmd. cmd.exe rereads a batch file after each
// command, so when it is this process's parent, and may be running that
// very file, the file goes only once that cmd.exe has ended.
func retireBatch(path string) error {
	if _, err := os.Lstat(path); errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if parent := os.Getppid(); strings.EqualFold(processName(parent), "cmd.exe") {
		script := fmt.Sprintf("Wait-Process -Id %d -ErrorAction SilentlyContinue; Remove-Item -LiteralPath '%s' -Force",
			parent, strings.ReplaceAll(path, "'", "''"))
		command := exec.Command("powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script)
		command.SysProcAttr = &syscall.SysProcAttr{CreationFlags: windows.CREATE_NO_WINDOW | windows.CREATE_NEW_PROCESS_GROUP}
		if err := command.Start(); err != nil {
			return err
		}
		return command.Process.Release()
	}
	return os.Remove(path)
}

// processName is the executable name of pid, "" when it cannot be read.
func processName(pid int) string {
	handle, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, uint32(pid))
	if err != nil {
		return ""
	}
	defer windows.CloseHandle(handle)
	buffer := make([]uint16, windows.MAX_LONG_PATH)
	size := uint32(len(buffer))
	if err := windows.QueryFullProcessImageName(handle, 0, &buffer[0], &size); err != nil {
		return ""
	}
	return filepath.Base(windows.UTF16ToString(buffer[:size]))
}
