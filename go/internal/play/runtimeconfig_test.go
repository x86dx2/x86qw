//go:build !windows

package play

import (
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
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

// TestJournalWriterOverTheLimit is the process
// TestAFailedJournalWriteLeavesNothing runs with a file size limit the
// journal does not fit in.
func TestJournalWriterOverTheLimit(t *testing.T) {
	path := os.Getenv("X86QW_TEST_JOURNAL")
	if path == "" {
		t.Skip("run by TestAFailedJournalWriteLeavesNothing")
	}
	var limit syscall.Rlimit
	syscall.Getrlimit(syscall.RLIMIT_FSIZE, &limit)
	limit.Cur = 16
	if err := syscall.Setrlimit(syscall.RLIMIT_FSIZE, &limit); err != nil {
		t.Fatal(err)
	}
	if writeJournal(path, journalDocument{Format: 1, Project: "x86qw", Type: "ktx-runtime-config"}, true) == nil {
		t.Fatal("the journal fit")
	}
}

// A journal cut short, by a full disk for one, would stop every later KTX
// launch at recovery: a first write that fails leaves no journal behind.
func TestAFailedJournalWriteLeavesNothing(t *testing.T) {
	path := filepath.Join(t.TempDir(), "x86qw-ktx-session-0123456789abcdef01234567.json")
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	writer := exec.Command(self, "-test.run=^TestJournalWriterOverTheLimit$")
	writer.Env = append(os.Environ(), "X86QW_TEST_JOURNAL="+path)
	if output, err := writer.CombinedOutput(); err != nil {
		t.Fatalf("%v\n%s", err, output)
	}
	if _, err := os.Lstat(path); !os.IsNotExist(err) {
		t.Fatal("the failed write left a journal behind")
	}
}
