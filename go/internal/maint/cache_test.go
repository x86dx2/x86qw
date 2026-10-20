package maint

import (
	"os"
	"path/filepath"
	"testing"
)

// TestOwnedCachesAdoptsTheCLITrustMetadata: cleanup and uninstall treat a
// cache root that only holds the CLI's TUF metadata as the CLI's own.
func TestOwnedCachesAdoptsTheCLITrustMetadata(t *testing.T) {
	root := filepath.Join(t.TempDir(), "x86qw")
	if err := os.MkdirAll(filepath.Join(root, "tuf"), 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("X86QW_CACHE_DIR", root)
	owned, err := ownedCaches()
	if err != nil || len(owned) != 1 || owned[0] != root {
		t.Fatalf("cache só com metadata TUF deveria pertencer à CLI: %v %v", owned, err)
	}
	if err := os.WriteFile(filepath.Join(root, "notes.txt"), []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := ownedCaches(); err == nil {
		t.Fatal("cache com arquivos alheios deveria ser preservado")
	}
}
