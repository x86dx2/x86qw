package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/x86dx2/x86qw/go/internal/tufsign"
)

func TestSiteShipsOneGenerationAndProvenance(t *testing.T) {
	source := filepath.Join("..", "..", "..", filepath.FromSlash(siteDir))
	out := filepath.Join(t.TempDir(), "public")
	commit := strings.Repeat("a", 40)
	if err := assembleSite(source, out, commit, 1, 2); err != nil {
		t.Fatal(err)
	}
	generation, err := tufsign.ReadGeneration(filepath.Join(out, "api", "v1", "trust"))
	if err != nil {
		t.Fatal(err)
	}
	var shipped []string
	filepath.WalkDir(filepath.Join(out, "api", "v1", "trust"), func(path string, entry os.DirEntry, err error) error {
		if err == nil && !entry.IsDir() {
			relative, _ := filepath.Rel(filepath.Join(out, "api", "v1", "trust"), path)
			shipped = append(shipped, filepath.ToSlash(relative))
		}
		return nil
	})
	want := generation.Files()
	slices.Sort(shipped)
	slices.Sort(want)
	if !slices.Equal(shipped, want) {
		t.Fatalf("published trust %v, generation %v", shipped, want)
	}
	for _, document := range []string{"version", "api/v1/version", "api/v1/health"} {
		var fields map[string]any
		payload, err := os.ReadFile(filepath.Join(out, filepath.FromSlash(document)))
		if err == nil {
			err = json.Unmarshal(payload, &fields)
		}
		if err != nil || fields["commit"] != commit {
			t.Fatalf("%s: %v %v", document, fields, err)
		}
	}
	headers, _ := os.ReadFile(filepath.Join(out, "_headers"))
	if !strings.Contains(string(headers), "/api/v1/product.json\n  Cache-Control: no-store") {
		t.Fatal("_headers without the product.json no-store")
	}
}
