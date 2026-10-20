//go:build windows

package play

import (
	"os"
	"os/exec"
	"time"

	"golang.org/x/sys/windows"
)

// Spawn starts the client with cwd = target and no inherited stdio, like
// supervisor_core.spawn_detached_client on Windows.
func Spawn(executable string, args []string, dir string) (*os.Process, error) {
	command := exec.Command(executable, args...)
	command.Dir = dir
	command.Stdin, command.Stdout, command.Stderr = nil, nil, nil
	if err := command.Start(); err != nil {
		return nil, err
	}
	process := command.Process
	go command.Wait()
	return process, nil
}

// ExitsWithin reports whether the process ended during the grace period.
func ExitsWithin(process *os.Process, grace time.Duration) (bool, int) {
	handle, err := windows.OpenProcess(windows.SYNCHRONIZE|windows.PROCESS_QUERY_LIMITED_INFORMATION, false, uint32(process.Pid))
	if err != nil {
		return true, -1
	}
	defer windows.CloseHandle(handle)
	event, err := windows.WaitForSingleObject(handle, uint32(grace/time.Millisecond))
	if err != nil || event != windows.WAIT_OBJECT_0 {
		return false, 0
	}
	var code uint32
	if windows.GetExitCodeProcess(handle, &code) != nil {
		return true, -1
	}
	return true, int(code)
}
