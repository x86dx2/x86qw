//go:build !windows

package play

import (
	"os"
	"os/exec"
	"syscall"
	"time"
)

// Spawn starts the client detached: new session, no terminal, cwd = target.
func Spawn(executable string, args []string, dir string) (*os.Process, error) {
	command := exec.Command(executable, args...)
	command.Dir = dir
	command.Stdin, command.Stdout, command.Stderr = nil, nil, nil
	command.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	if err := command.Start(); err != nil {
		return nil, err
	}
	process := command.Process
	go command.Wait() // reap if it exits while we still run
	return process, nil
}

// ExitsWithin reports whether the process ended during the grace period.
func ExitsWithin(process *os.Process, grace time.Duration) (bool, int) {
	deadline := time.Now().Add(grace)
	for time.Now().Before(deadline) {
		if err := process.Signal(syscall.Signal(0)); err != nil {
			return true, -1
		}
		time.Sleep(250 * time.Millisecond)
	}
	return false, 0
}
