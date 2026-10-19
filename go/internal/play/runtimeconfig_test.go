//go:build !windows

package play

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestRuntimeConfigLifecycleWithPythonRecovery(t *testing.T) {
	target := t.TempDir()
	os.MkdirAll(filepath.Join(target, "qw"), 0o755)
	os.MkdirAll(filepath.Join(target, ".x86qw"), 0o700)
	payload, err := Payload(nil, []string{"echo hi"})
	if err != nil {
		t.Fatal(err)
	}
	config, err := Create(target, payload)
	if err != nil {
		t.Fatal(err)
	}
	if data, _ := os.ReadFile(config.config); string(data) != string(payload) {
		t.Fatal("cfg publicado diverge do payload")
	}
	process, err := Spawn("/bin/sleep", []string{"30"}, target)
	if err != nil {
		t.Fatal(err)
	}
	if err := config.Transfer(process.Pid); err != nil {
		t.Fatal(err)
	}
	if exited, _ := ExitsWithin(process, 500*time.Millisecond); exited {
		t.Fatal("processo saiu cedo")
	}
	// Alive controller: recovery keeps the cfg.
	if err := Recover(target); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(config.config); err != nil {
		t.Fatal("cfg removido com controlador vivo")
	}
	process.Kill()
	time.Sleep(300 * time.Millisecond)

	repo, _ := filepath.Abs(filepath.Join("..", "..", ".."))
	python, err := exec.LookPath("python3.13")
	if err != nil {
		if python, err = exec.LookPath("python3"); err != nil {
			t.Skip("sem Python")
		}
	}
	command := exec.Command(python, "-c", `
import sys
from pathlib import Path
from x86qw_runtime.gameplay.runtime_configs import recover_runtime_configs
print(recover_runtime_configs(Path(sys.argv[1])))`, target)
	command.Dir = repo
	command.Env = append(os.Environ(), "PYTHONPATH="+repo)
	output, err := command.CombinedOutput()
	if err != nil {
		if strings.Contains(string(output), "ModuleNotFoundError") {
			t.Skip("x86qw_runtime indisponível")
		}
		t.Fatalf("recuperação Python falhou: %v\n%s", err, output)
	}
	if _, err := os.Stat(config.config); !os.IsNotExist(err) {
		t.Fatalf("Python não removeu o cfg do controlador morto: %s", output)
	}
	if _, err := os.Stat(config.journal); !os.IsNotExist(err) {
		t.Fatal("Python não removeu o journal")
	}
}
