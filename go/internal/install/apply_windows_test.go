package install

import (
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

// A managed folder the user turned into a junction leads out of the
// installation, though links are not followed through it: a file a new
// version drops is not deleted there.
func TestAnUpdateDeletesNothingBehindAJunction(t *testing.T) {
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
	if output, err := exec.Command("cmd", "/c", "mklink", "/J", filepath.Join(root, "qw", "maps"), shared).CombinedOutput(); err != nil {
		t.Fatalf("mklink /J: %v %s", err, output)
	}
	tx, err := overlay(t, root, "comp", map[string]string{"qw/maps/a.bsp": "a"})
	if err != nil {
		t.Fatal(err)
	}
	tx.Close()
	if _, err := os.Stat(filepath.Join(shared, "b.bsp")); err != nil {
		t.Fatal("the update deleted a file behind the junction")
	}
}
