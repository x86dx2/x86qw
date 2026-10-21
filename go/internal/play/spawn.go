package play

import (
	"os"
	"os/exec"
	"time"
)

// Child is a client Spawn started; it is reaped in the background.
type Child struct {
	*os.Process
	done chan int
}

func start(command *exec.Cmd) (*Child, error) {
	command.Stdin, command.Stdout, command.Stderr = nil, nil, nil
	if err := command.Start(); err != nil {
		return nil, err
	}
	child := &Child{Process: command.Process, done: make(chan int, 1)}
	go func() {
		command.Wait()
		child.done <- command.ProcessState.ExitCode()
	}()
	return child, nil
}

// ExitsWithin reports whether the child ended during grace, with its exit
// code.
func (c *Child) ExitsWithin(grace time.Duration) (bool, int) {
	select {
	case code := <-c.done:
		c.done <- code
		return true, code
	case <-time.After(grace):
		return false, 0
	}
}
