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
		t.Fatal("published cfg differs from the payload")
	}
	t.Setenv("X86QW_TEST_CHILD", "sleep")
	executable, _ := os.Executable()
	process, err := Spawn(executable, nil, target)
	if err != nil {
		t.Fatal(err)
	}
	if exited, _ := process.ExitsWithin(300 * time.Millisecond); exited {
		t.Fatal("process exited early")
	}
	if err := config.Transfer(process.Pid); err != nil {
		t.Fatal(err)
	}
	if err := Recover(target); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(config.config); err != nil {
		t.Fatal("cfg removed while its controller lives")
	}
	process.Kill()
	if exited, _ := process.ExitsWithin(5 * time.Second); !exited {
		t.Fatal("process did not stop")
	}
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
