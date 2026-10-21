//go:build !windows

package play

import (
	"os/exec"
	"syscall"
)

// Spawn starts the client detached: new session, no terminal, cwd = dir.
func Spawn(executable string, args []string, dir string) (*Child, error) {
	command := exec.Command(executable, args...)
	command.Dir = dir
	command.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	return start(command)
}
