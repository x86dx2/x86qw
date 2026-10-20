//go:build !windows

package services

import (
	"io"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/x86dx2/x86qw/go/internal/lock"
)

// These tests supervise /bin/sleep and /bin/sh stand-ins; no game server is
// ever started.

func testJournal(t *testing.T) *Journal {
	t.Helper()
	target := t.TempDir()
	self, status := lock.Identity(os.Getpid())
	if status != "alive" {
		t.Skip("process identity unavailable")
	}
	owner := &lock.Owner{SessionID: "20261019T120000Z-0123456789ab", Command: "host",
		ControllerPID: self.PID, ControllerStartToken: self.Token, ControllerExecutable: self.Executable}
	journal, err := NewJournal(target, owner, false, "")
	if err != nil {
		t.Fatal(err)
	}
	return journal
}

func quiet() *Console { return &Console{Out: io.Discard, Err: io.Discard} }

func TestJournalRoundTrip(t *testing.T) {
	journal := testJournal(t)
	config, err := WriteTemporaryConfig(journal.Target, "xh_", [][]byte{[]byte("map \"dm6\"")}, journal)
	if err != nil {
		t.Fatal(err)
	}
	if info, _ := os.Stat(config.Path); info.Mode().Perm() != 0o600 {
		t.Fatalf("cfg mode %v", info.Mode())
	}
	data, err := LoadJournal(journal.Path)
	if err != nil {
		t.Fatal(err)
	}
	files := data["temporary_files"].([]any)
	if len(files) != 1 || files[0].(map[string]any)["state"] != "ready" {
		t.Fatalf("temporary files: %v", files)
	}
	paths, err := SessionJournalPaths(journal.Target, journal.SessionID)
	if err != nil || len(paths) != 1 {
		t.Fatalf("paths %v %v", paths, err)
	}
	if !cleanupSensitive(config.Path, config.Identity) {
		t.Fatal("cfg not removed")
	}
}

func TestSupervisorStopRequest(t *testing.T) {
	journal := testJournal(t)
	spec := &Spec{Label: "QWFWD", Arguments: []string{"/bin/sleep", "30"}, Cwd: journal.Target,
		Parameters: [][2]string{{"bind", "127.0.0.1"}}}
	go func() {
		deadline := time.Now().Add(10 * time.Second)
		for time.Now().Before(deadline) {
			if data, err := LoadJournal(journal.Path); err == nil && data["status"] == "running" {
				payload := pyDumps(map[string]any{"format": 1, "project": "x86qw", "session_id": journal.SessionID, "requested_at": isoNow()}, false)
				publishStopRequest(filepath.Join(journal.Directory, "stop.request"), []byte(payload+"\n"))
				return
			}
			time.Sleep(20 * time.Millisecond)
		}
	}()
	code, err := RunProcesses([]*Spec{spec}, journal, quiet(), nil)
	if err != nil || code != 0 {
		t.Fatalf("code %d err %v", code, err)
	}
	processes := journal.Data["processes"].([]any)
	entry := processes[0].(map[string]any)
	if processGroupStatus(entry["pid"].(int)) != "dead" {
		t.Fatal("process group survived the stop request")
	}
	if entry["runtime"] != "qwfwd" || entry["state"] != "ready" {
		t.Fatalf("process entry: %v", entry)
	}
}

func TestSupervisorChildExit(t *testing.T) {
	journal := testJournal(t)
	spec := &Spec{Label: "MVDSV", Arguments: []string{"/bin/sh", "-c", "sleep 0.3; exit 3"}, Cwd: journal.Target}
	code, err := RunProcesses([]*Spec{spec}, journal, quiet(), nil)
	if err != nil || code != 3 {
		t.Fatalf("code %d err %v", code, err)
	}
}

func TestSupervisorRejectsChangedBinary(t *testing.T) {
	journal := testJournal(t)
	spec := &Spec{Label: "MVDSV", Arguments: []string{"/bin/sleep", "30"}, Cwd: journal.Target, ExpectedSHA256: "00"}
	if _, err := RunProcesses([]*Spec{spec}, journal, quiet(), nil); err == nil {
		t.Fatal("binary with a different hash was started")
	}
	if len(journal.Data["processes"].([]any)) != 0 {
		t.Fatal("rejected binary was journaled")
	}
}
