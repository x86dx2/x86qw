package main

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"
)

// TestCatalogRoundTripIsByteIdentical rewrites the checked-in catalog with
// the CLI binaries it already lists and requires the exact same bytes.
func TestCatalogRoundTripIsByteIdentical(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", filepath.FromSlash(catalogFile)))
	if err != nil {
		t.Fatal(err)
	}
	p, err := catalogPins(raw)
	if err != nil {
		t.Fatal(err)
	}
	out, err := withCLIPackages(raw, p.Version, p, "")
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(out, raw) {
		t.Fatal("the catalog round trip changed bytes")
	}
	stamped, err := withCLIPackages(raw, p.Version, p, "2030-01-02T03:04:05Z")
	if err != nil || !bytes.Contains(stamped, []byte(`"generated_at": "2030-01-02T03:04:05Z"`)) {
		t.Fatalf("generated_at was not updated: %v", err)
	}
}
