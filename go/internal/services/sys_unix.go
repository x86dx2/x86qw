//go:build !windows

package services

import (
	"errors"
	"os"
	"os/exec"
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

// processGroupStatus mirrors posix_process_group_status (kill -0 probe).
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
	pid, _ := intField(entry["pid"])
	group, ok := intField(entry["process_group"])
	return ok && group == pid && group > 1 && processGroupStatus(group) == "alive"
}

// terminateRecordedProcess mirrors sessions.terminate_recorded_process.
func terminateRecordedProcess(entry map[string]any) (string, error) {
	status := processProbe(entry)
	pid, _ := intField(entry["pid"])
	if status == "dead" {
		group, ok := intField(entry["process_group"])
		if !ok || group != pid || group <= 1 {
			return "already_dead", nil
		}
		switch processGroupStatus(group) {
		case "dead":
			return "already_dead", nil
		case "inconclusive":
			return "", errorf("O líder PID %d terminou, mas seu grupo ficou inconclusivo.", pid)
		}
	}
	if status == "identity_mismatch" {
		return "identity_mismatch", nil
	}
	if status != "alive" && status != "dead" {
		return "", errorf("A identidade de um processo órfão ficou inconclusiva.")
	}
	if err := signalRecorded(entry, false); err != nil {
		if errors.Is(err, syscall.ESRCH) {
			return "already_dead", nil
		}
		return "", errorf("Não foi possível encerrar o processo órfão PID %d.", pid)
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
		return "", errorf("Não foi possível forçar o processo órfão PID %d.", pid)
	}
	deadline = time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		if !processStillMatches(entry) {
			return "killed", nil
		}
		time.Sleep(50 * time.Millisecond)
	}
	return "", errorf("O processo órfão PID %d não encerrou; estado preservado.", pid)
}

// startDetached starts a service in its own session/process group.
func startDetached(arguments []string, dir string, output *os.File) (*exec.Cmd, error) {
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
			return errorf("Não foi possível confirmar o grupo de processos %d; estado preservado.", group)
		case "alive":
			if err := syscall.Kill(-group, syscall.SIGTERM); err != nil && !errors.Is(err, syscall.ESRCH) {
				return errorf("Não foi possível encerrar o grupo de processos %d.", group)
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
			return errorf("Não foi possível forçar o encerramento do grupo %d.", group)
		}
		groupDeadline := time.Now().Add(time.Second)
		for processGroupStatus(group) == "alive" {
			if time.Now().After(groupDeadline) {
				return errorf("O grupo de processos %d permaneceu ativo após SIGKILL.", group)
			}
			time.Sleep(50 * time.Millisecond)
		}
	}
	return nil
}

// exitCode mirrors Popen.poll: negative signal number for signaled exits.
func exitCode(state *os.ProcessState) int {
	if status, ok := state.Sys().(syscall.WaitStatus); ok && status.Signaled() {
		return -int(status.Signal())
	}
	return state.ExitCode()
}

var serviceSignals = []os.Signal{syscall.SIGINT, syscall.SIGTERM}

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
		return nil, errorf("Não foi possível criar o controlador em segundo plano: %v", err)
	}
	if err := command.Start(); err != nil {
		return nil, errorf("Não foi possível criar o controlador em segundo plano: %v", err)
	}
	_, writeErr := stdin.Write(request)
	if err := errors.Join(writeErr, stdin.Close()); err != nil {
		syscall.Kill(-command.Process.Pid, syscall.SIGKILL)
		command.Wait()
		return nil, errorf("O handoff privado do controlador falhou: %v", err)
	}
	return command, nil
}

var _ = lock.Identity

// replaceFile atomically moves temporary over path.
func replaceFile(temporary, path string) error { return os.Rename(temporary, path) }
