//go:build !windows

package lock

import (
	"bytes"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"
)

func TestLockFormat(t *testing.T) {
	target := t.TempDir()
	lock, err := Acquire(target, "update")
	if err != nil {
		t.Fatal(err)
	}
	defer lock.Release()
	payload, _ := os.ReadFile(lock.Path)
	var document map[string]any
	if err := json.Unmarshal(payload, &document); err != nil {
		t.Fatal(err)
	}
	if len(document) != 11 || document["format"] != float64(3) || !bytes.HasSuffix(payload, []byte("}\n")) {
		t.Fatalf("lock: %s", payload)
	}
	info, _ := os.Stat(lock.Path)
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("mode %v", info.Mode())
	}
}

func TestSecondAcquireRespectsLiveLock(t *testing.T) {
	target := t.TempDir()
	lock, err := Acquire(target, "update")
	if err != nil {
		t.Fatal(err)
	}
	if second, err := Acquire(target, "update"); err == nil {
		second.Release()
		t.Fatal("a live lock should be refused")
	}
	lock.Release()
	again, err := Acquire(target, "update")
	if err != nil {
		t.Fatalf("should acquire after release: %v", err)
	}
	again.Release()
}

// macOS names a process by argv[0] as typed: a CLI started as "x86qw" from
// the installation is the same live controller to a probe from any folder.
func TestProbeOfACommandStartedByNameFromAnotherFolder(t *testing.T) {
	sleep, err := exec.LookPath("sleep")
	if err != nil {
		t.Skip(err)
	}
	target := t.TempDir()
	os.WriteFile(filepath.Join(target, "x86qw"), []byte("cli"), 0o755)
	child := &exec.Cmd{Path: sleep, Args: []string{"x86qw", "30"}, Dir: target}
	if err := child.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { child.Process.Kill(); child.Wait() }()
	t.Chdir(target)
	identity, status := Identity(child.Process.Pid)
	if status != "alive" {
		t.Skip("process identity unavailable")
	}
	t.Chdir(t.TempDir())
	if got := Probe(identity.PID, identity.Token, identity.Executable); got != "alive" {
		t.Fatalf("seen from another folder the live controller is %s (%s)", got, identity.Executable)
	}
}

// Linux shows a binary replaced under a running process as "path (deleted)":
// an update that put a new CLI in place leaves the controller alive.
func TestProbeOfAControllerWhoseBinaryWasReplaced(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("/proc/<pid>/exe is Linux's")
	}
	sleep, err := exec.LookPath("sleep")
	if err != nil {
		t.Skip(err)
	}
	payload, err := os.ReadFile(sleep)
	if err != nil {
		t.Fatal(err)
	}
	binary := filepath.Join(t.TempDir(), "x86qw")
	if err := os.WriteFile(binary, payload, 0o755); err != nil {
		t.Fatal(err)
	}
	child := exec.Command(binary, "30")
	if err := child.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { child.Process.Kill(); child.Wait() }()
	identity, status := Identity(child.Process.Pid)
	if status != "alive" {
		t.Skip("process identity unavailable")
	}
	os.WriteFile(binary+".new", payload, 0o755)
	if err := os.Rename(binary+".new", binary); err != nil {
		t.Fatal(err)
	}
	if got := Probe(identity.PID, identity.Token, identity.Executable); got != "alive" {
		t.Fatalf("after the binary was replaced the live controller is %s", got)
	}
}

func TestReclaimsLockOfDeadController(t *testing.T) {
	target := t.TempDir()
	child := exec.Command("sleep", "30")
	if err := child.Start(); err != nil {
		t.Fatal(err)
	}
	identity, status := Identity(child.Process.Pid)
	if status != "alive" {
		t.Skip("process identity unavailable")
	}
	sessions := filepath.Join(target, ".x86qw", "sessions")
	os.MkdirAll(sessions, 0o700)
	os.Chmod(filepath.Join(target, ".x86qw"), 0o700)
	owner := Owner{Format: 3, Project: "x86qw", SessionID: newSessionID(), OperationKind: "maintenance", Command: "update",
		ControllerPID: identity.PID, ControllerStartToken: identity.Token, ControllerExecutable: identity.Executable,
		CreatedAt: "2026-10-19T00:00:00.000000+00:00", Installation: Resolve(target), PrivateFilesystem: 1}
	payload, _ := json.MarshalIndent(owner, "", "  ")
	os.WriteFile(filepath.Join(sessions, "active.lock"), append(payload, '\n'), 0o600)
	if lock, err := Acquire(target, "update"); err == nil {
		lock.Release()
		t.Fatal("the live controller should keep the lock")
	}
	child.Process.Kill()
	child.Wait()
	lock, err := Acquire(target, "update")
	if err != nil {
		t.Fatalf("should reclaim the lock of a dead controller: %v", err)
	}
	if err := lock.Release(); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(lock.Path); err == nil {
		t.Fatal("lock was not released")
	}
}
