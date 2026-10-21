package content

import (
	"bytes"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
)

// Verify corruptions. These tests clone the checkout with APFS/reflink
// copies, corrupt one fact per case and require the verifier to reject it
// with the message the retired manage.py verify printed for the same case
// (testdata/verify-corruptions.json). They need a hydrated dist/ and take
// minutes, so they only run with X86QW_CONTENT_CORRUPTIONS=1.

func repoRoot(t *testing.T) string {
	t.Helper()
	dir, _ := os.Getwd()
	for {
		if _, err := os.Stat(filepath.Join(dir, ComponentsPath)); err == nil {
			return dir
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			t.Skip("checkout do repositório indisponível")
		}
		dir = parent
	}
}

func requireHydrated(t *testing.T, root string) {
	t.Helper()
	if IsGitLFSPointer(filepath.Join(root, "dist/game-data/id1/pak0.pak")) {
		t.Skip("dist/ contém ponteiros Git LFS; execute git lfs checkout")
	}
}

// cloneCheckout copies the checkout cheaply and detaches it from Git.
func cloneCheckout(t *testing.T, root string) string {
	t.Helper()
	target := filepath.Join(t.TempDir(), "x86qw")
	if _, err := exec.Command("cp", "-cR", root, target).CombinedOutput(); err != nil {
		if output, err := exec.Command("cp", "-R", root, target).CombinedOutput(); err != nil {
			t.Fatalf("clone: %v: %s", err, output)
		}
	}
	// The worktree's .git file points at the real repository; never let a
	// command in the clone touch it.
	os.RemoveAll(filepath.Join(target, ".git"))
	os.RemoveAll(filepath.Join(target, "go"))
	return target
}

func runGoVerify(root string) (string, error) {
	var out bytes.Buffer
	err := NewProject(root).Verify(&out, VerifyOptions{SkipGit: true})
	return out.String(), err
}

func editJSON(t *testing.T, path string, sortKeys bool, edit func(document *Object)) {
	t.Helper()
	value, err := ReadJSON(path)
	if err != nil {
		t.Fatal(err)
	}
	edit(value.(*Object))
	payload, err := pyjson.EncodeFile(value, sortKeys)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, payload, 0o644); err != nil {
		t.Fatal(err)
	}
}

func firstObject(document *Object, key string) *Object {
	return document.Value(key).([]any)[0].(*Object)
}

func replaceFile(t *testing.T, path string, payload []byte) {
	t.Helper()
	// Clones share extents; replace rather than write through.
	os.Remove(path)
	if err := os.WriteFile(path, payload, 0o644); err != nil {
		t.Fatal(err)
	}
}

func firstManifestFile(t *testing.T, root string) string {
	t.Helper()
	manifest, err := LoadManifest(filepath.Join(root, ManifestPath))
	if err != nil {
		t.Fatal(err)
	}
	keys := manifest.Value("files").(*Object).Keys()
	return keys[0]
}

type mutation struct {
	name  string
	apply func(t *testing.T, root string)
}

var mutations = []mutation{
	{"catalog-package-size", func(t *testing.T, root string) {
		editJSON(t, filepath.Join(root, CatalogPath), false, func(d *Object) {
			firstObject(d, "packages").Set("size", pyjson.Number("0"))
		})
	}},
	{"catalog-diverges-from-tuf-target", func(t *testing.T, root string) {
		editJSON(t, filepath.Join(root, CatalogPath), false, func(d *Object) {
			d.Set("generated_at", "2000-01-01T00:00:00Z")
		})
	}},
	{"tuf-timestamp-missing", func(t *testing.T, root string) {
		os.Remove(filepath.Join(root, TrustMetadataDir, "timestamp.json"))
	}},
	{"tuf-stale-generation", func(t *testing.T, root string) {
		os.WriteFile(filepath.Join(root, TrustMetadataDir, "1.snapshot.json"), []byte("{}"), 0o644)
	}},
	{"tuf-root-policy", func(t *testing.T, root string) {
		editJSON(t, filepath.Join(root, TrustRootPath), false, func(d *Object) {
			d.Value("signed").(*Object).Set("consistent_snapshot", false)
		})
	}},
	{"component-kind", func(t *testing.T, root string) {
		editJSON(t, filepath.Join(root, ComponentsPath), false, func(d *Object) {
			firstObject(d, "components").Set("kind", "bogus")
		})
	}},
	{"component-contract", func(t *testing.T, root string) {
		editJSON(t, filepath.Join(root, ComponentsPath), false, func(d *Object) {
			d.Set("min_cli_version", "not-semver")
		})
	}},
	{"component-policy-consumers", func(t *testing.T, root string) {
		editJSON(t, filepath.Join(root, PolicyPath), false, func(d *Object) {
			components := d.Value("components").(*Object)
			components.Value(components.Keys()[0]).(*Object).Set("consumers", []any{})
		})
	}},
	{"runtime-missing-field", func(t *testing.T, root string) {
		editJSON(t, filepath.Join(root, InventoryDir, "runtimes.json"), false, func(d *Object) {
			firstObject(d, "runtimes").Delete("smoke_test")
		})
	}},
	{"capabilities-duplicate-key", func(t *testing.T, root string) {
		path := filepath.Join(root, InventoryDir, "capabilities.json")
		raw, _ := os.ReadFile(path)
		replaceFile(t, path, bytes.Replace(raw, []byte("{"), []byte(`{"format": 1, `), 1))
	}},
	{"game-unknown-capability", func(t *testing.T, root string) {
		editJSON(t, filepath.Join(root, InventoryDir, "games.json"), false, func(d *Object) {
			firstObject(d, "games").Set("required_capabilities", []any{"no-such-capability"})
		})
	}},
	{"compatibility-unknown-runtime", func(t *testing.T, root string) {
		editJSON(t, filepath.Join(root, InventoryDir, "compatibility.json"), false, func(d *Object) {
			firstObject(d, "compatibility").Set("runtime", "no-such-runtime")
		})
	}},
	{"ktx-mode-default-map", func(t *testing.T, root string) {
		editJSON(t, filepath.Join(root, ktxModesPath), false, func(d *Object) {
			mode := firstObject(d, "modes")
			mode.Set("default_map", "nosuchmap")
			mode.Set("suggested_maps", append(mode.Value("suggested_maps").([]any), "nosuchmap"))
		})
	}},
	{"ktx-source-missing", func(t *testing.T, root string) {
		os.Remove(filepath.Join(root, ktxSourcePath))
	}},
	{"product-stale", func(t *testing.T, root string) {
		path := filepath.Join(root, ProductPath)
		raw, _ := os.ReadFile(path)
		replaceFile(t, path, append(raw, '\n'))
	}},
	{"release-version", func(t *testing.T, root string) {
		editJSON(t, filepath.Join(root, ReleasesPath), false, func(d *Object) {
			components := d.Value("components").(*Object)
			components.Value(components.Keys()[0]).(*Object).Set("version", "bad version")
		})
	}},
	{"upstream-duplicate-id", func(t *testing.T, root string) {
		editJSON(t, filepath.Join(root, UpstreamsPath), false, func(d *Object) {
			entries := d.Value("upstreams").([]any)
			entries[1].(*Object).Set("id", entries[0].(*Object).Value("id"))
		})
	}},
	{"preserved-source-digest", func(t *testing.T, root string) {
		editJSON(t, filepath.Join(root, UpstreamsPath), false, func(d *Object) {
			for _, entry := range objects(d.Value("upstreams")) {
				if source := entry.Value("source").(*Object); source.Has("sha256") {
					source.Set("sha256", strings.Repeat("0", 64))
					return
				}
			}
		})
	}},
	{"recipe-review-status", func(t *testing.T, root string) {
		editJSON(t, filepath.Join(root, RecipesDir, "ezquake/3.6.9/linux-x86_64.json"), false, func(d *Object) {
			d.Value("review").(*Object).Set("status", "maybe")
		})
	}},
	{"recipe-package-digest", func(t *testing.T, root string) {
		editJSON(t, filepath.Join(root, RecipesDir, "ezquake/3.6.9/macos-universal.json"), false, func(d *Object) {
			d.Value("package").(*Object).Set("sha256", "zz")
		})
	}},
	{"tree-partition-unassigned", func(t *testing.T, root string) {
		replaceFile(t, filepath.Join(root, DistDir, filepath.FromSlash(nquakeDir), "zz-unassigned.txt"), []byte("x"))
	}},
	{"dist-integrity", func(t *testing.T, root string) {
		path := filepath.Join(root, DistDir, filepath.FromSlash(firstManifestFile(t, root)))
		raw, _ := os.ReadFile(path)
		replaceFile(t, path, append(raw, 'x'))
	}},
	{"dist-missing-file", func(t *testing.T, root string) {
		os.Remove(filepath.Join(root, DistDir, filepath.FromSlash(firstManifestFile(t, root))))
	}},
	{"dist-unregistered-file", func(t *testing.T, root string) {
		replaceFile(t, filepath.Join(root, DistDir, "mods/stray.txt"), []byte("x"))
	}},
	{"dist-lfs-pointer", func(t *testing.T, root string) {
		path := filepath.Join(root, DistDir, filepath.FromSlash(firstManifestFile(t, root)))
		replaceFile(t, path, append(append([]byte{}, GitLFSPointerPrefix...), "oid sha256:0\nsize 1\n"...))
	}},
	{"manifest-layout", func(t *testing.T, root string) {
		editJSON(t, filepath.Join(root, ManifestPath), true, func(d *Object) {
			d.Set("layout", "legacy")
		})
	}},
	{"manifest-url-query", func(t *testing.T, root string) {
		editJSON(t, filepath.Join(root, ManifestPath), true, func(d *Object) {
			files := d.Value("files").(*Object)
			metadata := files.Value(files.Keys()[0]).(*Object)
			metadata.Set("url", pyjson.Repr(metadata.Value("url"))+"?x=1")
		})
	}},
	{"misplaced-root-path", func(t *testing.T, root string) {
		os.Mkdir(filepath.Join(root, "tools"), 0o755)
	}},
}

func TestVerifyRejectsCorruptions(t *testing.T) {
	if os.Getenv("X86QW_CONTENT_CORRUPTIONS") != "1" {
		t.Skip("defina X86QW_CONTENT_CORRUPTIONS=1 para corromper clones do checkout")
	}
	root := repoRoot(t)
	requireHydrated(t, root)
	payload, err := os.ReadFile(filepath.Join("testdata", "verify-corruptions.json"))
	if err != nil {
		t.Fatal(err)
	}
	var want map[string]string
	if err := json.Unmarshal(payload, &want); err != nil {
		t.Fatal(err)
	}
	if _, err := runGoVerify(cloneCheckout(t, root)); err != nil {
		t.Fatalf("verify rejeita o checkout: %v", err)
	}
	for _, item := range mutations {
		t.Run(item.name, func(t *testing.T) {
			clone := cloneCheckout(t, root)
			item.apply(t, clone)
			_, err := runGoVerify(clone)
			if err == nil {
				t.Fatal("corrupção aceita")
			}
			got := strings.ReplaceAll("[ERRO] "+err.Error(), clone+"/", "<root>/")
			if got != want[item.name] {
				t.Errorf("mensagem diverge:\nwant: %s\ngot:  %s", want[item.name], got)
			}
		})
	}
}

// TestVerifyAcceptsCheckout runs the Go verifier on the real checkout.
func TestVerifyAcceptsCheckout(t *testing.T) {
	root := repoRoot(t)
	requireHydrated(t, root)
	output, err := runGoVerify(root)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(output, "[OK] Catalogo:") {
		t.Fatalf("resumo ausente: %s", output)
	}
}
