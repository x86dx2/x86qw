package content

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
)

// TestAddDryRun validates distribution-change definitions offline; each
// must be accepted or rejected exactly as testdata/add-dry-run.json
// records.
func TestAddDryRun(t *testing.T) {
	root := repoRoot(t)
	requireHydrated(t, root)
	manifest, err := LoadManifest(filepath.Join(root, ManifestPath))
	if err != nil {
		t.Fatal(err)
	}
	const relative = "mods/ktx/1.47/source/ktx-1.47.tar.gz"
	metadata, ok := manifest.Value("files").(*Object).Value(relative).(*Object)
	if !ok {
		t.Skip("artefato KTX ausente do manifesto")
	}
	entry := func(mutate func(*Object)) *Object {
		file := object("destination", "dist/"+relative, "url", metadata.Value("url"), "size", metadata.Value("size"),
			"sha256", metadata.Value("sha256"), "managed", true, "distribution_component", metadata.Value("component"),
			"consumer", metadata.Value("consumer"), "package", metadata.Value("package"))
		if mutate != nil {
			mutate(file)
		}
		return object("format", pyjson.Number("1"), "project", "x86qw", "kind", "distribution-change", "files", []any{file})
	}
	cases := map[string]*Object{
		"identical-managed-file": entry(nil),
		"digest-mismatch":        entry(func(file *Object) { file.Set("sha256", strings.Repeat("0", 64)) }),
		"unknown-consumer":       entry(func(file *Object) { file.Set("consumer", "install:nobody") }),
		"wrong-package":          entry(func(file *Object) { file.Set("package", "pro-x") }),
		"unmanaged-remote":       entry(func(file *Object) { file.Set("managed", false) }),
		"outside-dist":           entry(func(file *Object) { file.Set("destination", "maintenance/x.bin") }),
		"wrong-kind":             object("format", pyjson.Number("1"), "project", "x86qw", "kind", "other", "files", []any{}),
	}
	payload, err := os.ReadFile(filepath.Join("testdata", "add-dry-run.json"))
	if err != nil {
		t.Fatal(err)
	}
	var want map[string]struct{ Error, Plan string }
	if err := json.Unmarshal(payload, &want); err != nil {
		t.Fatal(err)
	}
	for name, definition := range cases {
		t.Run(name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "definition.json")
			if err := WriteJSON(path, definition); err != nil {
				t.Fatal(err)
			}
			var out bytes.Buffer
			goErr := NewProject(root).Add(&out, path, AddOptions{DryRun: true})
			expected := want[name]
			switch {
			case expected.Error != "" && (goErr == nil || "[ERRO] "+goErr.Error() != expected.Error):
				t.Errorf("want %s, got %v", expected.Error, goErr)
			case expected.Error == "" && (goErr != nil || out.String() != expected.Plan):
				t.Errorf("plano diverge (%v):\nwant:\n%s\ngot:\n%s", goErr, expected.Plan, out.String())
			}
		})
	}
}
