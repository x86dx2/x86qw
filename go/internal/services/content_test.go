package services

import (
	"archive/zip"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/x86dx2/x86qw/go/internal/lock"
)

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

// writePK3 writes a package holding members, name to content.
func writePK3(t *testing.T, path string, members map[string]string) {
	t.Helper()
	file, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	writer := zip.NewWriter(file)
	for name, body := range members {
		out, err := writer.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		out.Write([]byte(body))
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	file.Close()
}

// payload is a KTX-like package: hundreds of small files in a few folders.
func payload(files int) map[string]string {
	members := map[string]string{"qwprogs.qvm": "progs", "server.cfg": "// autoexec"}
	for index := range files {
		folder := []string{"locs", "configs", "bots/maps", "sound/ktx"}[index%4]
		members[fmt.Sprintf("%s/member-%04d.txt", folder, index)] = fmt.Sprintf("content %d", index)
	}
	return members
}

// A KTX-sized payload is published for the session in a constant number of
// journal writes, whatever its size, and leaves the game folder as it was.
func TestMaterializeAPayloadAndPutTheFolderBack(t *testing.T) {
	journal := testJournal(t)
	gamedir := filepath.Join(journal.Target, "qw")
	os.MkdirAll(filepath.Join(gamedir, "configs"), 0o755)
	os.WriteFile(filepath.Join(gamedir, "configs", "member-0001.txt"), []byte("content 1"), 0o644)
	members := payload(800)
	pk3 := filepath.Join(journal.Target, "qw", "ktx.pk3")
	writePK3(t, pk3, members)
	writesBefore := journal.writes
	materialized, err := MaterializePK3(pk3, gamedir, "KTX", journal)
	if err != nil {
		t.Fatal(err)
	}
	if writes := journal.writes - writesBefore; writes > 3 {
		t.Fatalf("%d journal writes for %d members", writes, len(members))
	}
	for name, body := range members {
		if data, err := os.ReadFile(filepath.Join(gamedir, filepath.FromSlash(name))); err != nil || string(data) != body {
			t.Fatalf("%s: %q %v", name, data, err)
		}
	}
	created, ready := 0, 0
	for _, item := range journal.list("materialized_files") {
		entry := item.(map[string]any)
		if entry["created_by_session"] == true {
			created++
		}
		if entry["state"] == "ready" {
			ready++
		}
	}
	if created != len(members)-1 || ready != len(members) || len(journal.list("created_directories")) != 5 {
		t.Fatalf("journal: %d created, %d ready, directories %v", created, ready, journal.list("created_directories"))
	}
	materialized.Cleanup(func(warning string) { t.Error(warning) })
	var left []string
	filepath.WalkDir(gamedir, func(path string, entry os.DirEntry, err error) error {
		if err == nil && path != gamedir {
			left = append(left, strings.TrimPrefix(path, gamedir))
		}
		return nil
	})
	if strings.Join(left, ",") != strings.Join([]string{string(filepath.Separator) + "configs", filepath.Join(string(filepath.Separator)+"configs", "member-0001.txt"), string(filepath.Separator) + "ktx.pk3"}, ",") {
		t.Fatalf("left in the game folder: %v", left)
	}
}

// A personal file that differs from a payload member stops the session
// before anything is published, and stays as it was.
func TestMaterializeRefusesAPersonalFileInTheWay(t *testing.T) {
	journal := testJournal(t)
	gamedir := filepath.Join(journal.Target, "qw")
	os.MkdirAll(filepath.Join(gamedir, "configs"), 0o755)
	mine := filepath.Join(gamedir, "configs", "member-0001.txt")
	os.WriteFile(mine, []byte("my own"), 0o644)
	pk3 := filepath.Join(gamedir, "ktx.pk3")
	writePK3(t, pk3, payload(40))
	if _, err := MaterializePK3(pk3, gamedir, "KTX", journal); err == nil || !strings.Contains(err.Error(), "conflicts") {
		t.Fatalf("a differing personal file should stop it: %v", err)
	}
	if data, _ := os.ReadFile(mine); string(data) != "my own" {
		t.Fatalf("the personal file changed: %q", data)
	}
	entries, _ := os.ReadDir(gamedir)
	if len(entries) != 2 {
		t.Fatalf("published despite the conflict: %v", entries)
	}
}

// A payload folder that is a link out of the installation is refused:
// nothing is written where it leads.
func TestMaterializeWritesNothingOutsideTheInstallation(t *testing.T) {
	journal := testJournal(t)
	gamedir := filepath.Join(journal.Target, "qw")
	os.MkdirAll(gamedir, 0o755)
	outside := t.TempDir()
	if err := os.Symlink(outside, filepath.Join(gamedir, "locs")); err != nil {
		t.Skip(err)
	}
	pk3 := filepath.Join(gamedir, "ktx.pk3")
	writePK3(t, pk3, payload(40))
	if _, err := MaterializePK3(pk3, gamedir, "KTX", journal); err == nil {
		t.Fatal("a payload folder linked out of the installation was accepted")
	}
	if entries, _ := os.ReadDir(outside); len(entries) != 0 {
		t.Fatalf("written outside the installation: %v", entries)
	}
}
