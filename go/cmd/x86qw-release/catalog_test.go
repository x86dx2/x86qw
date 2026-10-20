package main

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// TestCatalogRoundTripIsByteIdentical rewrites the checked-in catalog with
// the CLI binaries it already lists and requires the exact same bytes.
func TestCatalogRoundTripIsByteIdentical(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "site", "public", "api", "v1", "catalog.json"))
	if err != nil {
		t.Fatal(err)
	}
	var document struct {
		Packages []struct {
			Package, Version, Platform, Architecture, SHA256 string
			Size                                             int64
		} `json:"packages"`
	}
	if err := json.Unmarshal(raw, &document); err != nil {
		t.Fatal(err)
	}
	p := &pins{SHA256: map[string]string{}, Size: map[string]int64{}}
	for _, pkg := range document.Packages {
		if pkg.Package != "x86qw-cli" {
			continue
		}
		p.Version = pkg.Version
		for target, coordinates := range cliCoordinates {
			if coordinates[0] == pkg.Platform && coordinates[1] == pkg.Architecture {
				p.SHA256[target], p.Size[target] = pkg.SHA256, pkg.Size
			}
		}
	}
	if len(p.SHA256) != len(Targets) {
		t.Fatalf("catálogo sem os %d binários da CLI: %v", len(Targets), p.SHA256)
	}
	out, err := withCLIPackages(raw, p.Version, p)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(out, raw) {
		t.Fatal("round-trip do catálogo alterou bytes")
	}
}
