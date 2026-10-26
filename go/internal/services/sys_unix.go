//go:build !windows

package services

import (
	"errors"
	"os"
	"os/exec"
	"os/signal"
	"syscall"
	"time"

	"github.com/x86dx2/x86qw/go/internal/lock"
)

// identityAt is the (st_dev, st_ino) of the entry at path, read from info.
func identityAt(path string, info os.FileInfo) [2]int64 {
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok {
		return [2]int64{}
	}
	return [2]int64{int64(stat.Dev), int64(stat.Ino)}
}

func identityOfFile(file *os.File) ([2]int64, error) {
	info, err := file.Stat()
	if err != nil {
		return [2]int64{}, err
	}
	return identityAt(file.Name(), info), nil
}

// processGroupStatus probes a process group with kill -0.
func processGroupStatus(group int) string {
	err := syscall.Kill(-group, 0)
	switch {
	case err == nil:
		return "alive"
	case errors.Is(err, syscall.ESRCH):
		return "dead"
	}
	return "inconclusive"
}

func signalRecorded(entry map[string]any, force bool) error {
	pid, _ := intField(entry["pid"])
	group, okGroup := intField(entry["process_group"])
	sig := syscall.SIGTERM
	if force {
		sig = syscall.SIGKILL
	}
	if okGroup && group == pid && group > 1 {
		return syscall.Kill(-group, sig)
	}
	return syscall.Kill(pid, sig)
}

func processStillMatches(entry map[string]any) bool {
	status := processProbe(entry)
	if status == "alive" {
		return true
	}
	if status != "dead" {
		return false
	}
	return orphanedGroup(entry)
}

// orphanedGroup reports whether the recorded leader is gone but its process
// group lives on. A token from before the last boot owns no group: another
// job may hold that number now.
func orphanedGroup(entry map[string]any) bool {
	pid, _ := intField(entry["pid"])
	group, ok := intField(entry["process_group"])
	token, _ := entry["creation_token"].(string)
	return ok && group == pid && group > 1 && lock.ThisBoot(token) && processGroupStatus(group) == "alive"
}

// terminateRecordedProcess ends a journaled process (and its group) once it
// is confirmed to be the one recorded.
func terminateRecordedProcess(entry map[string]any) (string, error) {
	status := processProbe(entry)
	pid, _ := intField(entry["pid"])
	if status == "dead" {
		group, ok := intField(entry["process_group"])
		token, _ := entry["creation_token"].(string)
		if !ok || group != pid || group <= 1 || !lock.ThisBoot(token) {
			return "already_dead", nil
		}
		switch processGroupStatus(group) {
		case "dead":
			return "already_dead", nil
		case "inconclusive":
			return "", errorf("Leader PID %d ended, but its group is inconclusive.", pid)
		}
	}
	if status == "identity_mismatch" {
		return "identity_mismatch", nil
	}
	if status != "alive" && status != "dead" {
		return "", errorf("The identity of an orphaned process is inconclusive.")
	}
	if err := signalRecorded(entry, false); err != nil {
		if errors.Is(err, syscall.ESRCH) {
			return "already_dead", nil
		}
		return "", errorf("Could not stop orphaned process PID %d.", pid)
	}
	deadline := time.Now().Add(4 * time.Second)
	for time.Now().Before(deadline) {
		if !processStillMatches(entry) {
			return "terminated", nil
		}
		time.Sleep(50 * time.Millisecond)
	}
	if err := signalRecorded(entry, true); err != nil {
		if errors.Is(err, syscall.ESRCH) {
			return "terminated", nil
		}
		return "", errorf("Could not force-stop orphaned process PID %d.", pid)
	}
	deadline = time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		if !processStillMatches(entry) {
			return "killed", nil
		}
		time.Sleep(50 * time.Millisecond)
	}
	return "", errorf("Orphaned process PID %d did not stop; its state was kept.", pid)
}

// startDetached starts a service in its own session/process group. Hidden
// only matters on Windows, where servers open windows of their own.
func startDetached(arguments []string, dir string, output *os.File, hidden bool) (*exec.Cmd, error) {
	command := exec.Command(arguments[0], arguments[1:]...)
	command.Dir = dir
	if output != nil {
		command.Stdout, command.Stderr = output, output
	}
	command.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	if err := command.Start(); err != nil {
		return nil, err
	}
	return command, nil
}

// stopGroups terminates each process group (SIGTERM, then SIGKILL).
func stopGroups(commands []*exec.Cmd, exited map[int]bool) error {
	var groups []int
	for index := len(commands) - 1; index >= 0; index-- {
		group := commands[index].Process.Pid
		groups = append(groups, group)
		switch processGroupStatus(group) {
		case "inconclusive":
			return errorf("Could not confirm process group %d; its state was kept.", group)
		case "alive":
			if err := syscall.Kill(-group, syscall.SIGTERM); err != nil && !errors.Is(err, syscall.ESRCH) {
				return errorf("Could not stop process group %d.", group)
			}
		}
	}
	deadline := time.Now().Add(4 * time.Second)
	for _, group := range groups {
		for time.Now().Before(deadline) && processGroupStatus(group) == "alive" && !exited[group] {
			time.Sleep(50 * time.Millisecond)
		}
	}
	for _, group := range groups {
		if processGroupStatus(group) != "alive" {
			continue
		}
		if err := syscall.Kill(-group, syscall.SIGKILL); err != nil && !errors.Is(err, syscall.ESRCH) {
			return errorf("Could not force-stop group %d.", group)
		}
		groupDeadline := time.Now().Add(time.Second)
		for processGroupStatus(group) == "alive" {
			if time.Now().After(groupDeadline) {
				return errorf("Process group %d stayed alive after SIGKILL.", group)
			}
			time.Sleep(50 * time.Millisecond)
		}
	}
	return nil
}

// exitCode is the process's exit code, or the negative signal number when a
// signal ended it.
func exitCode(state *os.ProcessState) int {
	if status, ok := state.Sys().(syscall.WaitStatus); ok && status.Signaled() {
		return -int(status.Signal())
	}
	return state.ExitCode()
}

// serviceSignals end a stack: Ctrl+C, a kill, and a closed terminal or a
// dropped SSH session, unless nohup asked to ignore that.
var serviceSignals = func() []os.Signal {
	signals := []os.Signal{syscall.SIGINT, syscall.SIGTERM}
	if !signal.Ignored(syscall.SIGHUP) {
		signals = append(signals, syscall.SIGHUP)
	}
	return signals
}()

func signalNumber(sig os.Signal) int {
	if number, ok := sig.(syscall.Signal); ok {
		return int(number)
	}
	return int(syscall.SIGINT)
}

// spawnBackgroundController detaches one controller and hands it the
// secrets exclusively through stdin (core.spawn_background_controller).
func spawnBackgroundController(executable string, arguments []string, request []byte) (*exec.Cmd, error) {
	command := exec.Command(executable, arguments...)
	command.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	stdin, err := command.StdinPipe()
	if err != nil {
		return nil, errorf("Could not create the background controller: %v", err)
	}
	if err := command.Start(); err != nil {
		return nil, errorf("Could not create the background controller: %v", err)
	}
	_, writeErr := stdin.Write(request)
	if err := errors.Join(writeErr, stdin.Close()); err != nil {
		syscall.Kill(-command.Process.Pid, syscall.SIGKILL)
		command.Wait()
		return nil, errorf("The controller's private handoff failed: %v", err)
	}
	return command, nil
}

// replaceFile atomically moves temporary over path.
