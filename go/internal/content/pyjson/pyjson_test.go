package pyjson

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func repoRoot(t *testing.T) string {
	t.Helper()
	dir, _ := os.Getwd()
	for {
		if _, err := os.Stat(filepath.Join(dir, "maintenance", "inventory", "components.json")); err == nil {
			return dir
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			t.Skip("repository checkout unavailable")
		}
		dir = parent
	}
}

// contentFiles lists every JSON document the maintenance writers touch.
func contentFiles(t *testing.T, root string) []string {
	var files []string
	for _, dir := range []string{"maintenance/inventory", "maintenance/recipes"} {
		filepath.WalkDir(filepath.Join(root, dir), func(path string, entry os.DirEntry, err error) error {
			if err == nil && !entry.IsDir() && strings.HasSuffix(path, ".json") {
				files = append(files, path)
			}
			return nil
		})
	}
	for _, name := range []string{"dist/manifest.json", "site/public/api/v1/catalog.json", "site/public/api/v1/product.json"} {
		files = append(files, filepath.Join(root, name))
	}
	if len(files) < 10 {
		t.Fatalf("too few documents found: %d", len(files))
	}
	return files
}

func TestRoundTripIsCanonical(t *testing.T) {
	root := repoRoot(t)
	canonical := 0
	for _, path := range contentFiles(t, root) {
		raw, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		value, err := Decode(raw, LastWins)
		if err != nil {
			t.Fatalf("%s: %v", path, err)
		}
		for _, sortKeys := range []bool{false, true} {
			encoded, err := EncodeFile(value, sortKeys)
			if err != nil {
				t.Fatalf("%s: %v", path, err)
			}
			if bytes.Equal(encoded, raw) {
				canonical++
			}
		}
	}
	// Files written by the tooling (catalog, manifest, product, releases,
	// policy, recipes) are already canonical and must survive unchanged.
	for _, name := range []struct {
		path     string
		sortKeys bool
	}{
		{"site/public/api/v1/catalog.json", false},
		{"site/public/api/v1/product.json", true},
		{"dist/manifest.json", true},
		{"maintenance/inventory/component-releases.json", false},
		{"maintenance/inventory/component-policy.json", false},
		{"maintenance/recipes/ezquake/3.6.9/linux-x86_64.json", false},
	} {
		raw, err := os.ReadFile(filepath.Join(root, name.path))
		if err != nil {
			t.Fatal(err)
		}
		value, _ := Decode(raw, LastWins)
		encoded, _ := EncodeFile(value, name.sortKeys)
		if !bytes.Equal(encoded, raw) {
			t.Errorf("%s does not survive the byte-for-byte round trip", name.path)
		}
	}
	if canonical == 0 {
		t.Fatal("no canonical document found")
	}
}

func TestEncodeEdgeCases(t *testing.T) {
	document := `{"b": [], "a": {}, "s": "ação   \u0001 \" \\ / \t <>&", "n": [0, -0, 12345678901234567890, 1.5, 1e20, 1e-7, 0.0001, true, false, null], "x": {"z": 1, "y": [[], {}]}}`
	value, err := Decode([]byte(document), LastWins)
	if err != nil {
		t.Fatal(err)
	}
	encoded, err := Encode(value, false)
	if err != nil {
		t.Fatal(err)
	}
	expected := "{\n  \"b\": [],\n  \"a\": {},\n  \"s\": \"ação   \\u0001 \\\" \\\\ / \\t <>&\",\n  \"n\": [\n    0,\n    0,\n    12345678901234567890,\n    1.5,\n    1e+20,\n    1e-07,\n    0.0001,\n    true,\n    false,\n    null\n  ],\n  \"x\": {\n    \"z\": 1,\n    \"y\": [\n      [],\n      {}\n    ]\n  }\n}"
	if string(encoded) != expected {
		t.Fatalf("got\n%s\nwant\n%s", encoded, expected)
	}
	sorted, _ := Encode(value, true)
	if !strings.HasPrefix(string(sorted), "{\n  \"a\": {},\n  \"b\": [],") {
		t.Fatalf("wrong sort_keys: %s", sorted)
	}
}

func TestDuplicateKeys(t *testing.T) {
	value, err := Decode([]byte(`{"a": 1, "b": 2, "a": 3}`), LastWins)
	if err != nil {
		t.Fatal(err)
	}
	encoded, _ := Encode(value, false)
	if string(encoded) != "{\n  \"a\": 3,\n  \"b\": 2\n}" {
		t.Fatalf("wrong last-wins: %s", encoded)
	}
	if _, err := Decode([]byte(`{"a": 1, "a": 1}`), RejectDuplicates); err == nil {
		t.Fatal("duplicate key accepted")
	}
}

func TestNumericEquality(t *testing.T) {
	if !Eq(true, Number("1")) || !Eq(Number("1.0"), Number("1")) || Eq("1", Number("1")) {
		t.Fatal("numeric equality differs from Python")
	}
	if IsExactInt(true) || !IsInstanceInt(true) || IsExactInt(Number("1.0")) {
		t.Fatal("int predicates differ from Python")
	}
}
