package install

import (
	"io/fs"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/receipt"
)

// tree writes files under a new folder, the shape of a prepared payload.
func tree(t *testing.T, files map[string]string) string {
	t.Helper()
	dir := t.TempDir()
	for relative, body := range files {
		path := filepath.Join(dir, filepath.FromSlash(relative))
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return dir
}

// overlay installs files as component id in its own transaction, left open.
func overlay(t *testing.T, root, id string, files map[string]string) (*Transaction, error) {
	t.Helper()
	tx, err := NewTransaction(root, ".test.")
	if err != nil {
		t.Fatal(err)
	}
	_, err = tx.InstallOverlay(id, tree(t, files), "v", "test", ValidateManagedPath)
	return tx, err
}

func inventory(t *testing.T, root, id string) map[string]string {
	t.Helper()
	_, entries, err := loadRecord(root, id)
	if err != nil {
		t.Fatal(err)
	}
	return entries
}

// A rollback that fails keeps the stage, which holds the only copy of the
// previous files, and says where it is.
func TestAFailedRollbackKeepsTheStage(t *testing.T) {
	if runtime.GOOS == "windows" || os.Getuid() == 0 {
		t.Skip("needs a folder the user cannot write to")
	}
	root := t.TempDir()
	first, err := overlay(t, root, "comp", map[string]string{"qw/a.cfg": "v1"})
	if err != nil {
		t.Fatal(err)
	}
	first.Close()
	tx, err := overlay(t, root, "comp", map[string]string{"qw/a.cfg": "v2"})
	if err != nil {
		t.Fatal(err)
	}
	qw := filepath.Join(root, "qw")
	os.Chmod(qw, 0o555)
	t.Cleanup(func() { os.Chmod(qw, 0o755) })
	err = tx.Rollback()
	tx.Close()
	if err == nil || !strings.Contains(err.Error(), tx.Stage) {
		t.Fatalf("the error should name the stage %s: %v", tx.Stage, err)
	}
	previous := ""
	filepath.WalkDir(tx.Stage, func(path string, entry fs.DirEntry, err error) error {
		if err == nil && entry.Name() == "a.cfg" {
			data, _ := os.ReadFile(path)
			previous = string(data)
		}
		return nil
	})
	if previous != "v1" {
		t.Fatalf("the previous copy of qw/a.cfg is gone (%q)", previous)
	}
}

// A run killed after it copied files but before it wrote the receipt left
// the new bytes behind: the next run takes them as its own.
func TestInstallOverlayTakesWhatAnInterruptedRunCopied(t *testing.T) {
	root := t.TempDir()
	// A fresh install killed after the copy: the files, no receipt.
	for relative, body := range map[string]string{"qw/a.cfg": "v1 a", "qw/b.cfg": "v1 b"} {
		path := filepath.Join(root, filepath.FromSlash(relative))
		os.MkdirAll(filepath.Dir(path), 0o755)
		os.WriteFile(path, []byte(body), 0o644)
	}
	tx, err := overlay(t, root, "comp", map[string]string{"qw/a.cfg": "v1 a", "qw/b.cfg": "v1 b"})
	if err != nil || len(tx.Warnings) != 0 {
		t.Fatalf("rerun of the killed install: %v %v", err, tx.Warnings)
	}
	tx.Close()
	// An update killed halfway: a is already v2, b still v1.
	os.WriteFile(filepath.Join(root, "qw", "a.cfg"), []byte("v2 a"), 0o644)
	tx, err = overlay(t, root, "comp", map[string]string{"qw/a.cfg": "v2 a", "qw/b.cfg": "v2 b"})
	if err != nil || len(tx.Warnings) != 0 {
		t.Fatalf("rerun of the killed update: %v %v", err, tx.Warnings)
	}
	tx.Close()
	if got := inventory(t, root, "comp"); len(got) != 2 || got["qw/a.cfg"] != digestOf([]byte("v2 a")) {
		t.Fatalf("inventory: %v", got)
	}
}

// stageHolds reports whether a file under stage holds body.
func stageHolds(stage, body string) bool {
	found := false
	filepath.WalkDir(stage, func(path string, entry fs.DirEntry, err error) error {
		if err == nil && entry.Type().IsRegular() {
			data, _ := os.ReadFile(path)
			found = found || string(data) == body
		}
		return nil
	})
	return found
}

// A runtime swap that fails, and then cannot put the previous runtime back,
// keeps the stage that holds it and says where it is.
func TestAFailedRuntimeRestoreKeepsTheStage(t *testing.T) {
	if runtime.GOOS == "windows" || os.Getuid() == 0 {
		t.Skip("needs folders the user cannot write to")
	}
	root := t.TempDir()
	spec := catalog.ClientSpecs[1]
	runtimePath := filepath.Join(root, spec.Runtime("nightly"))
	if err := os.WriteFile(runtimePath, []byte("previous runtime"), 0o755); err != nil {
		t.Fatal(err)
	}
	tx, err := NewTransaction(root, ".test.")
	if err != nil {
		t.Fatal(err)
	}
	// The receipt cannot be written, so the swap fails once the new runtime is in.
	receiptDir := filepath.Join(root, filepath.Dir(filepath.FromSlash(spec.Receipt("nightly"))))
	os.MkdirAll(receiptDir, 0o700)
	os.Chmod(receiptDir, 0o500)
	// The new runtime holds a folder that cannot be emptied, so undoing it fails too.
	fresh := filepath.Join(t.TempDir(), "new")
	locked := filepath.Join(fresh, "locked")
	os.MkdirAll(locked, 0o755)
	os.WriteFile(filepath.Join(locked, "file"), nil, 0o644)
	os.Chmod(locked, 0o500)
	t.Cleanup(func() {
		os.Chmod(receiptDir, 0o700)
		os.Chmod(locked, 0o755)
		os.Chmod(filepath.Join(runtimePath, "locked"), 0o755)
	})
	err = tx.commitRuntime(spec, "nightly", &preparedClient{path: fresh, receipt: &receipt.Receipt{Keys: []string{"format"}, Values: map[string]string{"format": "1"}}})
	tx.Close()
	if err == nil || !strings.Contains(err.Error(), tx.Stage) {
		t.Fatalf("the error should name the stage %s: %v", tx.Stage, err)
	}
	if !stageHolds(tx.Stage, "previous runtime") {
		t.Fatal("the previous runtime is gone")
	}
}

// On a volume that ignores case, the default on macOS and Windows, a file
// whose name changes only in case between versions is one file: the update
// keeps it.
func TestACaseOnlyRenameKeepsTheFile(t *testing.T) {
	root := t.TempDir()
	os.WriteFile(filepath.Join(root, "probe"), nil, 0o644)
	if _, err := os.Stat(filepath.Join(root, "PROBE")); err != nil {
		t.Skip("case-sensitive volume")
	}
	first, err := overlay(t, root, "comp", map[string]string{"qw/Foo.cfg": "same", "qw/keep.cfg": "v1"})
	if err != nil {
		t.Fatal(err)
	}
	first.Close()
	tx, err := overlay(t, root, "comp", map[string]string{"qw/foo.cfg": "same", "qw/keep.cfg": "v2"})
	if err != nil {
		t.Fatal(err)
	}
	tx.Close()
	record, _, err := loadRecord(root, "comp")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := verifyRecord(root, record); err != nil {
		t.Fatal(err)
	}
}

// A managed folder the user turned into a link to elsewhere leads out of the
// installation: a file a new version drops is not deleted there.
func TestAnUpdateDeletesNothingOutsideTheInstallation(t *testing.T) {
	root := t.TempDir()
	first, err := overlay(t, root, "comp", map[string]string{"qw/maps/a.bsp": "a", "qw/maps/b.bsp": "b"})
	if err != nil {
		t.Fatal(err)
	}
	first.Close()
	shared := filepath.Join(t.TempDir(), "shared-maps")
	if err := os.Rename(filepath.Join(root, "qw", "maps"), shared); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(shared, filepath.Join(root, "qw", "maps")); err != nil {
		t.Skip(err)
	}
	tx, err := overlay(t, root, "comp", map[string]string{"qw/maps/a.bsp": "a"})
	if err != nil {
		t.Fatal(err)
	}
	tx.Close()
	if _, err := os.Stat(filepath.Join(shared, "b.bsp")); err != nil {
		t.Fatal("the update deleted a file outside the installation")
	}
}
