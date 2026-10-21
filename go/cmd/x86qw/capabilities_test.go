package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"testing"
)

// The public command list (capabilities.json, and so product.json) is the
// one `x86qw help` shows, in the same order.
func TestCapabilitiesListTheCommands(t *testing.T) {
	payload, err := os.ReadFile(filepath.Join("..", "..", "..", "maintenance", "inventory", "capabilities.json"))
	if err != nil {
		t.Fatal(err)
	}
	var capabilities struct {
		Commands []string `json:"commands"`
	}
	if err := json.Unmarshal(payload, &capabilities); err != nil {
		t.Fatal(err)
	}
	if want := publicCommandNames(); !slices.Equal(capabilities.Commands, want) {
		t.Fatalf("capabilities.json lista %v; x86qw help lista %v", capabilities.Commands, want)
	}
}
