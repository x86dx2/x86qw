package fspath

import (
	"os"
	"path/filepath"
	"testing"
)

// Only real folders lead from root to a file: a linked folder on the way
// leads elsewhere.
func TestRealFolders(t *testing.T) {
	root := t.TempDir()
	if err := os.MkdirAll(filepath.Join(root, "qw", "maps"), 0o755); err != nil {
		t.Fatal(err)
	}
	if !RealFolders(filepath.Join(root, "qw", "maps", "a.bsp"), root) {
		t.Fatal("real folders refused")
	}
	if RealFolders(filepath.Join(t.TempDir(), "a.bsp"), root) {
		t.Fatal("a path outside root accepted")
	}
	if err := os.Symlink(t.TempDir(), filepath.Join(root, "qw", "linked")); err != nil {
		t.Skip(err)
	}
	if RealFolders(filepath.Join(root, "qw", "linked", "a.bsp"), root) {
		t.Fatal("a linked folder accepted")
	}
}
