//go:build windows

package play

import "os/exec"

// Spawn starts the client with cwd = dir and no inherited stdio.
func Spawn(executable string, args []string, dir string) (*Child, error) {
	command := exec.Command(executable, args...)
	command.Dir = dir
	return start(command)
}
