//go:build !windows

package lock

import (
	"bytes"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
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
		t.Fatalf("modo %v", info.Mode())
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
		t.Fatal("um lock vivo deveria ser recusado")
	}
	lock.Release()
	again, err := Acquire(target, "update")
	if err != nil {
		t.Fatalf("deveria adquirir após a liberação: %v", err)
	}
	again.Release()
}

func TestReclaimsLockOfDeadController(t *testing.T) {
	target := t.TempDir()
	child := exec.Command("sleep", "30")
	if err := child.Start(); err != nil {
		t.Fatal(err)
	}
	identity, status := Identity(child.Process.Pid)
	if status != "alive" {
		t.Skip("identidade de processo indisponível")
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
		t.Fatal("o controlador vivo deveria manter o lock")
	}
	child.Process.Kill()
	child.Wait()
	lock, err := Acquire(target, "update")
	if err != nil {
		t.Fatalf("deveria recuperar o lock de um controlador morto: %v", err)
	}
	if err := lock.Release(); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(lock.Path); err == nil {
		t.Fatal("lock não foi liberado")
	}
}
