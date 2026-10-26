package maint

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/cli"
)

func TestUninstallRemovesManagedFilesAndKeepsPersonalOnes(t *testing.T) {
	isolate(t)
	root := managedInstall(t)
	write(t, filepath.Join(root, "qw", "maps", "x86qw-b.bsp"), "map b edited")
	report, err := Uninstall(root)
	if err != nil {
		t.Fatal(err)
	}
	for _, gone := range []string{"ezquake-nightly-x86_64.AppImage", "qw/maps/x86qw-a.bsp", "x86qw.sh", cli.BinaryName(), ".x86qw"} {
		if exists(filepath.Join(root, filepath.FromSlash(gone))) {
			t.Errorf("%s should have been removed", gone)
		}
	}
	for _, kept := range []string{"id1/pak0.pak", "id1/pak1.pak", "ezquake/configs/config.cfg", "qw/demos/match.mvd"} {
		if !exists(filepath.Join(root, filepath.FromSlash(kept))) {
			t.Errorf("%s should have been kept", kept)
		}
	}
	if read(t, filepath.Join(root, "qw", "maps", "x86qw-b.bsp")) != "map b edited" {
		t.Error("the file the user modified was not kept")
	}
	if len(report.Warnings) != 1 || !strings.Contains(report.Warnings[0], "x86qw-b.bsp") {
		t.Errorf("warnings: %v", report.Warnings)
	}
	lines := strings.Join(report.Lines, "\n")
	if !strings.Contains(lines, "Permanent x86QW CLI removed") || !strings.Contains(lines, "Managed components removed") {
		t.Errorf("report: %s", lines)
	}
}

func TestUninstallRefusesWhatItDoesNotManage(t *testing.T) {
	isolate(t)
	plain := t.TempDir()
	write(t, filepath.Join(plain, "notes.txt"), "mine")
	if _, err := Uninstall(plain); err == nil || !strings.Contains(err.Error(), "managed identity") {
		t.Fatalf("a folder without x86QW should be refused: %v", err)
	}
	if !exists(filepath.Join(plain, "notes.txt")) {
		t.Fatal("the refusal deleted files")
	}
	legacy := managedInstall(t)
	write(t, filepath.Join(legacy, filepath.FromSlash(nquakeReceipt)), "format=1\n")
	if _, err := Uninstall(legacy); err == nil || !strings.Contains(err.Error(), "pre-1.0 metadata") {
		t.Fatalf("a pre-1.0 layout should be refused: %v", err)
	}
	if !exists(filepath.Join(legacy, "ezquake-nightly-x86_64.AppImage")) {
		t.Fatal("the refusal removed the runtime")
	}
}

func TestUninstallOfACLIOnlyFolderKeepsPersonalFiles(t *testing.T) {
	isolate(t)
	root := filepath.Join(t.TempDir(), "x86qw")
	write(t, filepath.Join(root, ".x86qw", "cli", "receipt"), `{"version":"2.3.2"}`)
	write(t, cli.Command(root), "#!/bin/sh\n")
	write(t, filepath.Join(root, "qw", "demos", "match.mvd"), "demo")
	report, err := Uninstall(root)
	if err != nil {
		t.Fatal(err)
	}
	if exists(filepath.Join(root, ".x86qw")) || exists(cli.Command(root)) {
		t.Fatal("the CLI and the metadata should be gone")
	}
	if !exists(filepath.Join(root, "qw", "demos", "match.mvd")) {
		t.Fatal("the demo was deleted")
	}
	if !strings.Contains(strings.Join(report.Lines, "\n"), "Permanent x86QW CLI removed") {
		t.Fatalf("report: %v", report.Lines)
	}
}

// Only a regular file at <target>/x86qw is the CLI: a folder there, such as
// another installation, survives the uninstall of the outer one.
func TestUninstallKeepsAFolderNamedLikeTheCLI(t *testing.T) {
	isolate(t)
	inner := managedInstall(t)
	outer := filepath.Dir(inner)
	write(t, filepath.Join(outer, filepath.FromSlash(stateRelative)), read(t, filepath.Join(inner, filepath.FromSlash(stateRelative))))
	if _, err := Uninstall(outer); err != nil {
		t.Fatal(err)
	}
	if !exists(filepath.Join(inner, "qw", "demos", "match.mvd")) {
		t.Fatal("the uninstall deleted the folder named like the CLI")
	}
}

// A managed file the user replaced with a link is theirs: uninstall keeps
// it, says so and still finishes.
func TestUninstallKeepsAManagedFileTurnedIntoALink(t *testing.T) {
	isolate(t)
	root := managedInstall(t)
	elsewhere := filepath.Join(t.TempDir(), "my-map.bsp")
	write(t, elsewhere, "my own map")
	managed := filepath.Join(root, "qw", "maps", "x86qw-a.bsp")
	os.Remove(managed)
	if err := os.Symlink(elsewhere, managed); err != nil {
		t.Skip(err)
	}
	report, err := Uninstall(root)
	if err != nil {
		t.Fatal(err)
	}
	if !exists(managed) || exists(filepath.Join(root, ".x86qw")) {
		t.Fatalf("link kept %v, metadata left %v", exists(managed), exists(filepath.Join(root, ".x86qw")))
	}
	if !strings.Contains(strings.Join(report.Warnings, "\n"), "x86qw-a.bsp") {
		t.Fatalf("warnings: %v", report.Warnings)
	}
}

// A managed folder the user turned into a link to elsewhere leads out of
// the installation: what lies there is not uninstalled.
func TestUninstallStaysInsideTheInstallation(t *testing.T) {
	isolate(t)
	root := managedInstall(t)
	shared := filepath.Join(t.TempDir(), "shared-maps")
	if err := os.Rename(filepath.Join(root, "qw", "maps"), shared); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(shared, filepath.Join(root, "qw", "maps")); err != nil {
		t.Skip(err)
	}
	report, err := Uninstall(root)
	if err != nil {
		t.Fatal(err)
	}
	if !exists(filepath.Join(shared, "x86qw-a.bsp")) || !strings.Contains(strings.Join(report.Warnings, "\n"), "x86qw-a.bsp") {
		t.Fatalf("outside the installation: a %v; warnings %v", exists(filepath.Join(shared, "x86qw-a.bsp")), report.Warnings)
	}
}

// Purge removes what x86QW and its games made, inventoried or not, and keeps
// what was in the folder before, as the installer promised.
func TestPurgeKeepsWhatX86QWDidNotMake(t *testing.T) {
	cache := isolate(t)
	ownedCache(t, cache)
	root := managedInstall(t)
	record(t, root, "mvdsv", map[string]string{"docs/licenses/mvdsv.md": "license"})
	write(t, filepath.Join(root, "arena", "maps", "downloaded.bsp"), "from a server")
	write(t, filepath.Join(root, catalog.ClientSpecs[1].Runtime("nightly")+".x86qw-kept.1"), "the copy repair kept")
	write(t, filepath.Join(root, "Doom", "save.dsg"), "another game's save")
	write(t, filepath.Join(root, "taxes.pdf"), "unrelated")
	// What a write cut short left beside x86QW's own files goes too; a logs
	// folder, which neither x86QW nor its servers make, stays.
	write(t, filepath.Join(root, ".x86qw.123456.tmp"), "half a CLI")
	write(t, filepath.Join(root, "..gitignore.123456.tmp"), "half a .gitignore")
	write(t, filepath.Join(root, "logs", "mine.log"), "the user's")
	report, err := Purge(root)
	if err != nil {
		t.Fatal(err)
	}
	entries, _ := os.ReadDir(root)
	var left []string
	for _, entry := range entries {
		left = append(left, entry.Name())
	}
	if strings.Join(left, ",") != "Doom,logs,taxes.pdf" {
		t.Fatalf("left behind: %v", left)
	}
	if !strings.Contains(strings.Join(report.Lines, "\n"), "Kept 3 items") {
		t.Fatalf("report: %v", report.Lines)
	}
}

func TestPurgeRemovesTheInstallationAndTheCache(t *testing.T) {
	cache := isolate(t)
	ownedCache(t, cache)
	root := managedInstall(t)
	report, err := Purge(root)
	if err != nil {
		t.Fatal(err)
	}
	if exists(root) || exists(cache) {
		t.Fatalf("installation (%v) or cache (%v) left behind", exists(root), exists(cache))
	}
	lines := strings.Join(report.Lines, "\n")
	if !strings.Contains(lines, "Cache removed") || !strings.Contains(lines, "Installation folder removed") {
		t.Fatalf("report: %s", lines)
	}
}

// A cache holding files the installer does not own stops purge before it
// touches anything, the installation included.
func TestPurgeStopsOnAForeignCache(t *testing.T) {
	cache := isolate(t)
	write(t, filepath.Join(cache, "foreign.txt"), "not x86QW's")
	root := managedInstall(t)
	if _, err := Purge(root); err == nil || !strings.Contains(err.Error(), "do not belong to the installer") {
		t.Fatalf("a foreign cache should stop the purge: %v", err)
	}
	if !exists(filepath.Join(root, "id1", "pak0.pak")) || !exists(filepath.Join(cache, "foreign.txt")) {
		t.Fatal("the refused purge deleted files")
	}
}

func TestPurgeOfAMissingFolderStillClearsTheCache(t *testing.T) {
	cache := isolate(t)
	ownedCache(t, cache)
	missing := filepath.Join(t.TempDir(), "never-installed")
	report, err := Purge(missing)
	if err != nil {
		t.Fatal(err)
	}
	if exists(cache) || !strings.Contains(strings.Join(report.Lines, "\n"), "No installation folder") {
		t.Fatalf("cache %v, report %v", exists(cache), report.Lines)
	}
}

func TestQuarantineRestoresEverythingWhenOneMoveFails(t *testing.T) {
	root := t.TempDir()
	first := filepath.Join(root, "first")
	write(t, filepath.Join(first, "a.txt"), "a")
	missing := filepath.Join(root, "missing")
	if err := quarantineRemove([]string{first, missing}); err == nil {
		t.Fatal("moving a missing path should fail")
	}
	if read(t, filepath.Join(first, "a.txt")) != "a" {
		t.Fatal("the first path did not come back from aside")
	}
	entries, _ := os.ReadDir(root)
	if len(entries) != 1 {
		t.Fatalf("quarantine left behind: %v", entries)
	}
}

func TestMinimalRemovalPathsDropsDescendants(t *testing.T) {
	sep := string(filepath.Separator)
	got := minimalRemovalPaths([]string{"a" + sep + "b" + sep + "c", "a" + sep + "b", "a" + sep + "bc", "a" + sep + "b"})
	if strings.Join(got, ",") != "a"+sep+"b,a"+sep+"bc" {
		t.Fatalf("%v", got)
	}
}
