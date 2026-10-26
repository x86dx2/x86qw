//go:build windows

package services

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"testing"
	"time"

	"github.com/x86dx2/x86qw/go/internal/lock"
)

func waitStatus(t *testing.T, pid int, want string) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for processGroupStatus(pid) != want {
		if time.Now().After(deadline) {
			t.Fatalf("PID %d did not become %s", pid, want)
		}
		time.Sleep(50 * time.Millisecond)
	}
}

func child(t *testing.T) []string {
	t.Helper()
	t.Setenv("X86QW_TEST_CHILD", "sleep")
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	return []string{executable}
}

func TestJobObjectStartAndStop(t *testing.T) {
	command, err := startDetached(child(t), t.TempDir(), nil, false)
	if err != nil {
		t.Fatal(err)
	}
	pid := command.Process.Pid
	waitStatus(t, pid, "alive")
	if err := stopGroups([]*exec.Cmd{command}, map[int]bool{}); err != nil {
		t.Fatal(err)
	}
	waitStatus(t, pid, "dead")
	if job.handle != 0 {
		t.Fatal("Job Object was not closed")
	}
}

func TestJobObjectKillsOnClose(t *testing.T) {
	command, err := startDetached(child(t), t.TempDir(), nil, false)
	if err != nil {
		t.Fatal(err)
	}
	pid := command.Process.Pid
	waitStatus(t, pid, "alive")
	if err := closeJob(); err != nil {
		t.Fatal(err)
	}
	waitStatus(t, pid, "dead")
	command.Wait()
}

func TestTerminateRecordedProcess(t *testing.T) {
	command := exec.Command(child(t)[0])
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	pid := command.Process.Pid
	identity, status := lock.Identity(pid)
	if status != "alive" {
		t.Fatalf("identity: %s", status)
	}
	entry := map[string]any{"pid": json.Number(strconv.Itoa(pid)), "executable": identity.Executable, "creation_token": identity.Token}
	mismatch := map[string]any{"pid": json.Number(strconv.Itoa(pid)), "executable": identity.Executable, "creation_token": "other"}
	if result, err := terminateRecordedProcess(mismatch); err != nil || result != "identity_mismatch" {
		t.Fatalf("mismatched identity: %s %v", result, err)
	}
	if result, err := terminateRecordedProcess(entry); err != nil || result != "terminated" {
		t.Fatalf("%s %v", result, err)
	}
	command.Wait()
	if result, err := terminateRecordedProcess(entry); err != nil || result != "already_dead" {
		t.Fatalf("second call: %s %v", result, err)
	}
}

func TestFileIdentity(t *testing.T) {
	path := filepath.Join(t.TempDir(), "a.cfg")
	os.WriteFile(path, []byte("1"), 0o600)
	info, _ := os.Lstat(path)
	first := identityAt(path, info)
	if first == ([2]int64{}) || identityAt(path, info) != first {
		t.Fatalf("unstable identity: %v", first)
	}
	file, _ := os.Open(path)
	byHandle, err := identityOfFile(file)
	file.Close()
	if err != nil || byHandle != first {
		t.Fatalf("identity by handle: %v %v", byHandle, err)
	}
	replacement := path + ".new"
	os.WriteFile(replacement, []byte("1"), 0o600)
	if err := os.Rename(replacement, path); err != nil {
		t.Fatal(err)
	}
	info, _ = os.Lstat(path)
	if identityAt(path, info) == first {
		t.Fatal("a replaced file kept its identity")
	}
}
