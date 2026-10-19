package main

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"
)

func TestCatalogRoundTripIsByteIdentical(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "site", "public", "api", "v1", "catalog.json"))
	if err != nil {
		t.Fatal(err)
	}
	out, err := withCLIPackages(raw, "", nil)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(out, raw) {
		t.Fatal("round-trip do catálogo alterou bytes")
	}
}
