//go:build !windows

package lock

import (
	"bytes"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func pythonRuntime(t *testing.T) (string, string) {
	t.Helper()
	repo, _ := filepath.Abs(filepath.Join("..", "..", ".."))
	for _, name := range []string{"python3.13", "python3"} {
		if path, err := exec.LookPath(name); err == nil {
			check := exec.Command(path, "-c", "import x86qw_runtime.session_control")
			check.Dir = repo
			if check.Run() == nil {
				return path, repo
			}
		}
	}
	t.Skip("sem Python com x86qw_runtime")
	return "", ""
}

// python runs session_control against target and returns its output.
func python(t *testing.T, script, target string) string {
	t.Helper()
	interpreter, repo := pythonRuntime(t)
	command := exec.Command(interpreter, "-c", script, target)
	command.Dir = repo
	command.Env = append(os.Environ(), "PYTHONPATH="+repo)
	output, _ := command.CombinedOutput()
	return string(output)
}

const pythonAcquire = `
import sys
from pathlib import Path
from x86qw_runtime.session_control import InstallationLock, SessionControlError
try:
    lock = InstallationLock.acquire(Path(sys.argv[1]), "update")
except SessionControlError as error:
    print("REFUSED", error)
else:
    print("ACQUIRED", lock.session_id)
    lock.release()
`

const pythonCrash = `
import os, sys
from pathlib import Path
from x86qw_runtime.session_control import InstallationLock
InstallationLock.acquire(Path(sys.argv[1]), "update")
os._exit(0)
`

func TestLockFormatMatchesPython(t *testing.T) {
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

func TestPythonRespectsLiveGoLock(t *testing.T) {
	target := t.TempDir()
	lock, err := Acquire(target, "update")
	if err != nil {
		t.Fatal(err)
	}
	output := python(t, pythonAcquire, target)
	lock.Release()
	if !strings.Contains(output, "REFUSED") || !strings.Contains(output, "ativa") {
		t.Fatalf("Python deveria recusar um lock Go vivo: %s", output)
	}
	if output := python(t, pythonAcquire, target); !strings.Contains(output, "ACQUIRED") {
		t.Fatalf("Python deveria adquirir após a liberação: %s", output)
	}
}

func TestPythonReclaimsCrashedGoLock(t *testing.T) {
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
	absolute := Resolve(target)
	owner := Owner{Format: 3, Project: "x86qw", SessionID: newSessionID(), OperationKind: "maintenance", Command: "update",
		ControllerPID: identity.PID, ControllerStartToken: identity.Token, ControllerExecutable: identity.Executable,
		CreatedAt: "2026-10-19T00:00:00.000000+00:00", Installation: absolute, PrivateFilesystem: 1}
	payload, _ := json.MarshalIndent(owner, "", "  ")
	os.WriteFile(filepath.Join(sessions, "active.lock"), append(payload, '\n'), 0o600)
	if output := python(t, pythonAcquire, target); !strings.Contains(output, "REFUSED") {
		t.Fatalf("Python deveria ver o controlador vivo: %s", output)
	}
	child.Process.Kill()
	child.Wait()
	if output := python(t, pythonAcquire, target); !strings.Contains(output, "ACQUIRED") {
		t.Fatalf("Python deveria recuperar o lock de um controlador morto: %s", output)
	}
}

func TestGoReclaimsCrashedPythonLock(t *testing.T) {
	target := t.TempDir()
	python(t, pythonCrash, target)
	if _, err := os.Stat(filepath.Join(target, ".x86qw", "sessions", "active.lock")); err != nil {
		t.Fatalf("Python não deixou o lock: %v", err)
	}
	lock, err := Acquire(target, "update")
	if err != nil {
		t.Fatalf("Go deveria recuperar o lock de um Python morto: %v", err)
	}
	if err := lock.Release(); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(lock.Path); err == nil {
		t.Fatal("lock não foi liberado")
	}
}
