package install

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// TestPrepareCacheAdoptsTheCLITrustMetadata reproduces a fresh Windows or
// Linux install: the CLI writes its TUF metadata to <cache>/tuf before the
// installer marks the cache, and the install must go on.
func TestPrepareCacheAdoptsTheCLITrustMetadata(t *testing.T) {
	root := filepath.Join(t.TempDir(), "x86qw")
	if err := os.MkdirAll(filepath.Join(root, "tuf"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "tuf", "root.json"), []byte("{}"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := prepareCache(root); err != nil {
		t.Fatalf("cache com metadata TUF da CLI foi recusado: %v", err)
	}
	if marker, err := os.ReadFile(filepath.Join(root, cacheMarker)); err != nil || string(marker) != "x86qw-cache-v1\n" {
		t.Fatalf("marcador do cache ausente: %q %v", marker, err)
	}

	foreign := filepath.Join(t.TempDir(), "x86qw")
	if err := os.MkdirAll(foreign, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(foreign, "notes.txt"), []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := prepareCache(foreign); err == nil || !strings.Contains(err.Error(), "não pertencem ao instalador") {
		t.Fatalf("cache com arquivos alheios deveria ser preservado: %v", err)
	}
}
