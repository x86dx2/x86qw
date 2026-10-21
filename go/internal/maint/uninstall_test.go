package maint

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestUninstallRemovesManagedFilesAndKeepsPersonalOnes(t *testing.T) {
	isolate(t)
	root := managedInstall(t)
	write(t, filepath.Join(root, "qw", "maps", "x86qw-b.bsp"), "map b edited")
	report, err := Uninstall(root)
	if err != nil {
		t.Fatal(err)
	}
	for _, gone := range []string{"ezquake-nightly-x86_64.AppImage", "qw/maps/x86qw-a.bsp", "x86qw.sh", ".x86qw"} {
		if exists(filepath.Join(root, filepath.FromSlash(gone))) {
			t.Errorf("%s should have been removed", gone)
		}
	}
	for _, kept := range []string{"id1/pak0.pak", "id1/pak1.pak", "ezquake/configs/config.cfg", "qw/demos/partida.mvd"} {
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
	write(t, filepath.Join(plain, "notas.txt"), "minhas")
	if _, err := Uninstall(plain); err == nil || !strings.Contains(err.Error(), "managed identity") {
		t.Fatalf("a folder without x86QW should be refused: %v", err)
	}
	if !exists(filepath.Join(plain, "notas.txt")) {
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
	write(t, filepath.Join(root, "x86qw.sh"), "#!/bin/sh\n")
	write(t, filepath.Join(root, "qw", "demos", "partida.mvd"), "demo")
	report, err := Uninstall(root)
	if err != nil {
		t.Fatal(err)
	}
	if exists(filepath.Join(root, ".x86qw")) || exists(filepath.Join(root, "x86qw.sh")) {
		t.Fatal("the CLI and the metadata should be gone")
	}
	if !exists(filepath.Join(root, "qw", "demos", "partida.mvd")) {
		t.Fatal("the demo was deleted")
	}
	if !strings.Contains(strings.Join(report.Lines, "\n"), "Permanent x86QW CLI removed") {
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
	write(t, filepath.Join(cache, "alheio.txt"), "not x86QW's")
	root := managedInstall(t)
	if _, err := Purge(root); err == nil || !strings.Contains(err.Error(), "do not belong to the installer") {
		t.Fatalf("a foreign cache should stop the purge: %v", err)
	}
	if !exists(filepath.Join(root, "id1", "pak0.pak")) || !exists(filepath.Join(cache, "alheio.txt")) {
		t.Fatal("the refused purge deleted files")
	}
}

func TestPurgeOfAMissingFolderStillClearsTheCache(t *testing.T) {
	cache := isolate(t)
	ownedCache(t, cache)
	missing := filepath.Join(t.TempDir(), "nunca-instalado")
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
	first := filepath.Join(root, "primeiro")
	write(t, filepath.Join(first, "a.txt"), "a")
	missing := filepath.Join(root, "nao-existe")
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
