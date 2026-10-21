//go:build windows

package play

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestRuntimeConfigLifecycleWindows(t *testing.T) {
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
	t.Setenv("X86QW_TEST_CHILD", "sleep")
	executable, _ := os.Executable()
	process, err := Spawn(executable, nil, target)
	if err != nil {
		t.Fatal(err)
	}
	if exited, _ := process.ExitsWithin(300 * time.Millisecond); exited {
		t.Fatal("processo saiu cedo")
	}
	if err := config.Transfer(process.Pid); err != nil {
		t.Fatal(err)
	}
	if err := Recover(target); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(config.config); err != nil {
		t.Fatal("cfg removido com controlador vivo")
	}
	process.Kill()
	if exited, _ := process.ExitsWithin(5 * time.Second); !exited {
		t.Fatal("processo não encerrou")
	}
	if err := Recover(target); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(config.config); !os.IsNotExist(err) {
		t.Fatal("cfg do controlador morto não foi removido")
	}
	if _, err := os.Stat(config.journal); !os.IsNotExist(err) {
		t.Fatal("journal do controlador morto não foi removido")
	}
}
