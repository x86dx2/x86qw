package content

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// TestUpdateAppliesWorkspace runs the full update write path offline on a
// clone: fake upstreams serve the release artifacts that dist/manifest.json
// does not mirror yet, the workspace is synced, validated, rebuilt,
// registered and swapped into place. It rebuilds every package, so it runs
// only with X86QW_CONTENT_PARITY_BUILD=1.
func TestUpdateAppliesWorkspace(t *testing.T) {
	if os.Getenv("X86QW_CONTENT_PARITY_BUILD") != "1" {
		t.Skip("defina X86QW_CONTENT_PARITY_BUILD=1 para exercitar update completo")
	}
	root := repoRoot(t)
	requireHydrated(t, root)
	clone := cloneCheckout(t, root)

	// Published content entries the current tree no longer reproduces would
	// make register_packages refuse; start from the CLI and client entries only.
	catalogPath := filepath.Join(clone, CatalogPath)
	catalog, err := LoadJSONObject(catalogPath)
	if err != nil {
		t.Fatal(err)
	}
	var kept []any
	for _, pkg := range objects(catalog.Value("packages")) {
		if pkg.Value("channel") != "content" {
			kept = append(kept, pkg)
		}
	}
	catalog.Set("packages", kept)
	if err := WriteJSON(catalogPath, catalog); err != nil {
		t.Fatal(err)
	}

	project := NewProject(clone)
	unmirrored := unmirroredReleaseArtifacts(t, project)
	project.HTTP = currentUpstreams(t, clone, "").client()
	git := &recorder{}
	project.Run = git.run
	var out bytes.Buffer
	if err := project.Update(&out, UpdateOptions{Workers: 4, Yes: true}); err != nil {
		t.Fatalf("update: %v\n%s", err, out.String())
	}
	if len(git.commands) != 1 || git.commands[0] != "git status --porcelain" {
		t.Fatalf("update executou git além da verificação: %v", git.commands)
	}

	fresh := NewProject(clone)
	manifest, err := LoadManifest(fresh.Path(ManifestPath))
	if err != nil {
		t.Fatal(err)
	}
	files := manifest.Value("files").(*Object)
	for _, path := range unmirrored {
		if !files.Has(path) {
			t.Errorf("manifesto não incorporou %s", path)
		}
	}
	if _, err := fresh.VerifyDistribution(fresh.Path(DistDir), manifest); err != nil {
		t.Fatalf("dist atualizado não verifica: %v", err)
	}
	updated, err := LoadJSONObject(catalogPath)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := fresh.ValidateCatalog(updated); err != nil {
		t.Fatal(err)
	}
	content := 0
	for _, pkg := range objects(updated.Value("packages")) {
		if pkg.Value("channel") == "content" {
			content++
		}
	}
	zips, _ := filepath.Glob(filepath.Join(clone, BuildsDir, "*", "*.zip"))
	if content != len(zips) || content == 0 {
		t.Fatalf("catálogo registrou %d pacotes de conteúdo para %d builds", content, len(zips))
	}
	leftovers, _ := filepath.Glob(filepath.Join(clone, "maintenance/build/update-*"))
	if len(leftovers) != 0 {
		t.Fatalf("workspace de update não foi removido: %v", leftovers)
	}
	if !strings.Contains(out.String(), "[OK] Working tree atualizado: ezQuake 3.6.9/") {
		t.Fatalf("resumo inesperado:\n%s", out.String())
	}
}
