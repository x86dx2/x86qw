//go:build !windows

package lock

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"time"
)

const noFollow = syscall.O_NOFOLLOW

// serialize holds flock on the sessions directory, like
// locking.installation_acquisition_mutex on POSIX.
func serialize(target, sessions string, fn func() error) error {
	directory, err := os.Open(sessions)
	if err != nil {
		return err
	}
	defer directory.Close()
	if err := syscall.Flock(int(directory.Fd()), syscall.LOCK_EX); err != nil {
		return err
	}
	defer syscall.Flock(int(directory.Fd()), syscall.LOCK_UN)
	return fn()
}

// Identity returns ("alive"|"dead"|"inconclusive") and the identity of pid,
// computed exactly like processes.process_identity.
func Identity(pid int) (ProcessIdentity, string) {
	if pid <= 0 {
		return ProcessIdentity{}, "dead"
	}
	switch runtime.GOOS {
	case "darwin":
		return darwinIdentity(pid)
	case "linux":
		return linuxIdentity(pid)
	}
	return ProcessIdentity{}, "inconclusive"
}

// ThisBoot reports whether a creation token can name a process of the
// running system: one from before the last boot names none, whatever now
// holds its PID.
func ThisBoot(token string) bool {
	switch {
	case strings.HasPrefix(token, "linux:"):
		boot, _, _ := strings.Cut(strings.TrimPrefix(token, "linux:"), ":")
		current, err := os.ReadFile("/proc/sys/kernel/random/boot_id")
		return err != nil || boot == strings.TrimSpace(string(current))
	case strings.HasPrefix(token, "macos:"):
		started, err := time.ParseInLocation("Mon Jan 2 15:04:05 2006", strings.TrimPrefix(token, "macos:"), time.Local)
		if err != nil {
			return true
		}
		output, err := exec.Command("sysctl", "-n", "kern.boottime").Output()
		var seconds int64
		if _, scanErr := fmt.Sscanf(string(output), "{ sec = %d", &seconds); err != nil || scanErr != nil {
			return true
		}
		return !started.Before(time.Unix(seconds, 0))
	}
	return true
}

func darwinIdentity(pid int) (ProcessIdentity, string) {
	run := func(field string) (string, int, error) {
		command := exec.Command("ps", "-p", strconv.Itoa(pid), "-o", field+"=")
		command.Env = append(os.Environ(), "LC_ALL=C")
		output, err := command.Output()
		code := 0
		var exit *exec.ExitError
		if errors.As(err, &exit) {
			code, err = exit.ExitCode(), nil
		}
		return strings.TrimSpace(string(output)), code, err
	}
	started, code, err := run("lstart")
	if err != nil {
		return ProcessIdentity{}, "inconclusive"
	}
	if code == 1 && started == "" {
		return ProcessIdentity{}, "dead"
	}
	if code != 0 || started == "" {
		return ProcessIdentity{}, "inconclusive"
	}
	comm, code, err := run("comm")
	if err != nil || code != 0 || comm == "" {
		return ProcessIdentity{}, "inconclusive"
	}
	return ProcessIdentity{PID: pid, Token: "macos:" + strings.Join(strings.Fields(started), " "), Executable: realpath(comm)}, "alive"
}

func linuxIdentity(pid int) (ProcessIdentity, string) {
	stat, err := os.ReadFile(fmt.Sprintf("/proc/%d/stat", pid))
	if errors.Is(err, os.ErrNotExist) {
		return ProcessIdentity{}, "dead"
	}
	if err != nil {
		return ProcessIdentity{}, "inconclusive"
	}
	text := string(stat)
	closing := strings.LastIndex(text, ")")
	if closing < 0 || closing+2 > len(text) {
		return ProcessIdentity{}, "inconclusive"
	}
	fields := strings.Fields(text[closing+2:])
	if len(fields) <= 19 {
		return ProcessIdentity{}, "inconclusive"
	}
	if fields[0] == "Z" {
		return ProcessIdentity{}, "dead"
	}
	boot := "boot-unknown"
	if data, err := os.ReadFile("/proc/sys/kernel/random/boot_id"); err == nil {
		boot = strings.TrimSpace(string(data))
	}
	exe, err := os.Readlink(fmt.Sprintf("/proc/%d/exe", pid))
	if err != nil {
		return ProcessIdentity{}, "inconclusive"
	}
	// A binary replaced under the process reads "path (deleted)".
	return ProcessIdentity{PID: pid, Token: "linux:" + boot + ":" + fields[19], Executable: realpath(strings.TrimSuffix(exe, " (deleted)"))}, "alive"
}
