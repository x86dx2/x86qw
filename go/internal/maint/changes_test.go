package maint

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestChangesReportAddedModifiedAndDeletedFiles(t *testing.T) {
	root := managedInstall(t)
	write(t, filepath.Join(root, "qw", "maps", "x86qw-a.bsp"), "map a edited")
	os.Remove(filepath.Join(root, "qw", "maps", "x86qw-b.bsp"))
	write(t, filepath.Join(root, "qw", "my.cfg"), "bind x")
	// Operational paths never show up.
	write(t, filepath.Join(root, "qw", "demos", "other.mvd"), "demo")
	write(t, filepath.Join(root, "ezquake", "temp", "x"), "temp")
	changes, err := InspectChanges(root)
	if err != nil {
		t.Fatal(err)
	}
	var got []string
	for _, change := range changes {
		got = append(got, change.Status+" "+change.Path+" "+change.Component)
	}
	// The runtime, PAKs, demos, temp files and the launcher are operational.
	expected := "A ezquake/configs/config.cfg |M qw/maps/x86qw-a.bsp nquake-maps|D qw/maps/x86qw-b.bsp nquake-maps|A qw/my.cfg "
	if strings.Join(got, "|") != expected {
		t.Fatalf("changes:\n%s\nexpected:\n%s", strings.Join(got, "\n"), strings.ReplaceAll(expected, "|", "\n"))
	}
}

func TestChangesRefuseInventoriesThatDisagree(t *testing.T) {
	root := managedInstall(t)
	record(t, root, "nquake-models", map[string]string{"qw/maps/x86qw-a.bsp": "other content"})
	if _, err := InspectChanges(root); err == nil || !strings.Contains(err.Error(), "disagree") {
		t.Fatalf("conflicting inventories should fail: %v", err)
	}
}

func TestGitignoreListsOperationalAndManagedPaths(t *testing.T) {
	root := managedInstall(t)
	record(t, root, "nquake-models", map[string]string{"qw/progs/a b[1].mdl": "model"})
	if err := SyncGitignore(root); err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(read(t, filepath.Join(root, ".gitignore")), "\n")
	has := func(line string) bool {
		for _, candidate := range lines {
			if candidate == line {
				return true
			}
		}
		return false
	}
	for _, line := range []string{"/.x86qw", "/qw/demos", "/ezquake-nightly-x86_64.AppImage", "/qw/maps/x86qw-a.bsp", `/qw/progs/a\ b\[1\].mdl`} {
		if !has(line) {
			t.Errorf("%q missing from .gitignore", line)
		}
	}
	if exists(filepath.Join(root, ".gitignore.tmp")) {
		t.Fatal("the temporary file was left behind")
	}
	// The .gitignore itself is operational, so it is never a change.
	changes, err := InspectChanges(root)
	if err != nil {
		t.Fatal(err)
	}
	for _, change := range changes {
		if change.Path == ".gitignore" {
			t.Fatal(".gitignore showed up as a change")
		}
	}
}
