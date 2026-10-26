//go:build !windows

package services

import (
	"encoding/json"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/x86dx2/x86qw/go/internal/lock"
	"github.com/x86dx2/x86qw/go/internal/play"
	"github.com/x86dx2/x86qw/go/internal/pyjson"
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
				payload, _ := pyjson.Dumps(map[string]any{"format": 1, "project": "x86qw", "session_id": journal.SessionID, "requested_at": isoNow()}, -1)
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

// orphanGroup starts a job whose leader exits at once while a member keeps
// the group alive, like `cat log | less` once cat ends; it returns both.
func orphanGroup(t *testing.T) (leader, member int) {
	t.Helper()
	job := exec.Command("/bin/sh", "-c", "sleep 30 >/dev/null 2>&1 & echo $!")
	job.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	out, err := job.Output()
	if err != nil {
		t.Fatal(err)
	}
	member, _ = strconv.Atoi(strings.TrimSpace(string(out)))
	t.Cleanup(func() { syscall.Kill(member, syscall.SIGKILL) })
	return job.Process.Pid, member
}

// creationToken is a token like lock.Identity's for a process started at
// when, in this boot or, with stale, in one before it.
func creationToken(t *testing.T, when time.Time, stale bool) string {
	t.Helper()
	switch runtime.GOOS {
	case "darwin":
		return "macos:" + when.Format("Mon Jan 2 15:04:05 2006")
	case "linux":
		boot, err := os.ReadFile("/proc/sys/kernel/random/boot_id")
		if err != nil || stale {
			return "linux:00000000-0000-0000-0000-000000000000:1"
		}
		return "linux:" + strings.TrimSpace(string(boot)) + ":1"
	}
	t.Skip("no process identity here")
	return ""
}

// A journal left by a session that died with the machine names a leader
// PID that another job may now use as its process group: recovery leaves
// that group alone, and still ends an orphaned group of this boot.
func TestRecoveryEndsOnlyGroupsOfThisBoot(t *testing.T) {
	for _, stale := range []bool{true, false} {
		leader, member := orphanGroup(t)
		when := time.Now()
		if stale {
			when = time.Date(2020, 1, 2, 10, 0, 0, 0, time.Local)
		}
		number := json.Number(strconv.Itoa(leader))
		entry := map[string]any{"label": "MVDSV", "pid": number, "process_group": number,
			"creation_token": creationToken(t, when, stale), "executable": "/Users/nobody/Games/x86qw/mvdsv/mvdsv"}
		result, err := terminateRecordedProcess(entry)
		if err != nil {
			t.Fatal(err)
		}
		if alive := syscall.Kill(member, 0) == nil; alive != stale {
			t.Fatalf("stale journal %v: result %q, the group's member alive %v", stale, result, alive)
		}
	}
}

// A stock ezQuake sends rcon hashed (cl_crypt_rcon 1). A server only for
// this machine has to take it hashed too, or every rcon is a bad password.
func TestALocalServerTakesHashedRcon(t *testing.T) {
	journal := testJournal(t)
	target := journal.Target
	fakeRuntime(t, target, "mvdsv")
	os.MkdirAll(filepath.Join(target, "qw"), 0o755)
	session := &Session{}
	game := play.Game{ID: "ktx", Label: "KTX", Gamedir: "qw", PersonalConfig: "qw/x86qw-ktx-user.cfg"}
	options := &HostOptions{Bind: "127.0.0.1", Port: 27500, Maxclients: 16, RconPassword: "secret"}
	if _, err := HostSpec(target, options, &Hosted{Game: game}, session, journal); err != nil {
		t.Fatal(err)
	}
	hashed := false
	for _, config := range session.Temporary {
		data, _ := os.ReadFile(config.Path)
		hashed = hashed || strings.Contains(string(data), "sv_crypt_rcon 1")
	}
	if !hashed {
		t.Fatal("the local server takes rcon only in plain text")
	}
}
