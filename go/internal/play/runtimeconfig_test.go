//go:build !windows

package play

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestRuntimeConfigLifecycleRecovery(t *testing.T) {
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
		t.Fatal("published cfg differs from the payload")
	}
	process, err := Spawn("/bin/sleep", []string{"30"}, target)
	if err != nil {
		t.Fatal(err)
	}
	if err := config.Transfer(process.Pid); err != nil {
		t.Fatal(err)
	}
	if exited, _ := process.ExitsWithin(500 * time.Millisecond); exited {
		t.Fatal("process exited early")
	}
	// Alive controller: recovery keeps the cfg.
	if err := Recover(target); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(config.config); err != nil {
		t.Fatal("cfg removed while its controller lives")
	}
	process.Kill()
	time.Sleep(300 * time.Millisecond)

	// Dead controller: recovery removes the cfg and its journal.
	if err := Recover(target); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(config.config); !os.IsNotExist(err) {
		t.Fatal("the dead controller's cfg was not removed")
	}
	if _, err := os.Stat(config.journal); !os.IsNotExist(err) {
		t.Fatal("the dead controller's journal was not removed")
	}
}
