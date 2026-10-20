package content

import (
	"bytes"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"

	clicatalog "github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
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

// TestAddMovesOneComponentToAnotherNQuakeRevision moves only
// x86qw-client-bootstrap to a new nQuake revision through 'add' on a clone:
// its files go to the new revision directory and its package is rebuilt
// with a new version, while every other component keeps its revision, its
// files and its package bytes.
func TestAddMovesOneComponentToAnotherNQuakeRevision(t *testing.T) {
	if os.Getenv("X86QW_CONTENT_PARITY_BUILD") != "1" {
		t.Skip("defina X86QW_CONTENT_PARITY_BUILD=1 para exercitar add completo")
	}
	root := repoRoot(t)
	requireHydrated(t, root)
	clone := cloneCheckout(t, root)
	const moved = "x86qw-client-bootstrap"
	const revision = "2222222222222222222222222222222222222222"

	project := NewProject(clone)
	releases, err := project.Releases()
	if err != nil {
		t.Fatal(err)
	}
	previous := ReferenceRevisionOf(releases, moved)
	manifest, err := LoadManifest(project.Path(ManifestPath))
	if err != nil {
		t.Fatal(err)
	}
	// Like TestUpdateAppliesWorkspace, start from the CLI and client entries
	// and compare against what the clone itself builds before the change.
	catalog, err := LoadJSONObject(project.Path(CatalogPath))
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
	if err := WriteJSON(project.Path(CatalogPath), catalog); err != nil {
		t.Fatal(err)
	}
	built, err := project.BuildPackages(project.Path(DistDir), t.TempDir(), BuildOptions{})
	if err != nil {
		t.Fatal(err)
	}
	before := map[string]string{}
	for _, pkg := range objects(built.Value("packages")) {
		before[pyjson.Repr(pkg.Value("filename"))] = pyjson.Repr(pkg.Value("sha256"))
	}
	bodies := map[string]string{}
	var files []any
	for _, relative := range manifest.Value("files").(*Object).Keys() {
		metadata := manifest.Value("files").(*Object).Value(relative).(*Object)
		parts, ok := nquakeSnapshotParts(relative)
		if !ok || metadata.Value("package") != moved {
			continue
		}
		upstream := strings.Join(parts[3:], "/")
		url := "https://raw.githubusercontent.com/nQuake/distfiles/" + revision + "/" + quotePath(upstream)
		bodies[url] = filepath.Join(clone, DistDir, filepath.FromSlash(relative))
		files = append(files, object("destination", "dist/distributions/nquake/"+revision+"/"+upstream, "url", url,
			"size", metadata.Value("size"), "sha256", metadata.Value("sha256"), "managed", true,
			"distribution_component", metadata.Value("component"), "consumer", metadata.Value("consumer"), "package", moved))
	}
	transition := pyjson.NewObject()
	transition.Set(moved, previous)
	definition := object("format", pyjson.Number("1"), "project", "x86qw", "kind", "distribution-change",
		"reference", object("repository", "https://github.com/nQuake/distfiles", "revision", revision, "components", transition),
		"files", files)
	path := filepath.Join(t.TempDir(), "definition.json")
	if err := WriteJSON(path, definition); err != nil {
		t.Fatal(err)
	}
	web := &fakeWeb{handler: func(w http.ResponseWriter, r *http.Request) {
		source, ok := bodies["https://"+r.URL.Host+r.URL.EscapedPath()]
		if !ok {
			http.NotFound(w, r)
			return
		}
		payload, _ := os.ReadFile(source)
		w.Write(payload)
	}}
	project.HTTP = web.client()
	project.Run = (&recorder{}).run
	var out bytes.Buffer
	if err := project.Add(&out, path, AddOptions{Yes: true}); err != nil {
		t.Fatalf("add: %v\n%s", err, out.String())
	}

	// The catalog changed, so TUF would need a new signature; check the
	// distribution itself.
	fresh := NewProject(clone)
	updatedReleases, err := fresh.Releases()
	if err != nil {
		t.Fatal(err)
	}
	components, _ := fresh.Components()
	sources, err := LoadSourceContext(fresh.Path(DistDir), components, updatedReleases)
	if err != nil {
		t.Fatalf("dist depois do add: %v", err)
	}
	if revisions := sources.Revisions(); len(revisions) != 2 || !contains(revisions, revision) || !contains(revisions, previous) {
		t.Fatalf("revisoes nQuake no dist: %v", revisions)
	}
	updatedManifest, _ := LoadManifest(fresh.Path(ManifestPath))
	if _, err := fresh.VerifyDistribution(fresh.Path(DistDir), updatedManifest); err != nil {
		t.Fatalf("dist depois do add nao verifica: %v", err)
	}
	for identifier, pinned := range ReferenceRevisions(updatedReleases) {
		want := previous
		if identifier == moved {
			want = revision
		}
		if pinned != want {
			t.Errorf("%s fixa %s, esperado %s", identifier, pinned, want)
		}
	}
	if version := pyjson.Get(updatedReleases, "components", moved, "version"); version != "222222222222+x86qw.4" {
		t.Errorf("versao do bootstrap: %v", version)
	}
	after, _ := LoadJSONObject(fresh.Path(CatalogPath))
	movedFile := moved + "-222222222222+x86qw.4.zip"
	found := false
	for _, pkg := range objects(after.Value("packages")) {
		filename := pyjson.Repr(pkg.Value("filename"))
		switch {
		case pkg.Value("channel") != "content" || filename == "x86qw-core-id1-0.1.0.zip":
		case filename == movedFile:
			found = strings.Contains(pyjson.Repr(pkg.Value("origin_url")), "/releases/download/nquake-"+revision+"/") &&
				pkg.Value("source_commit") == revision
		case before[filename] != pyjson.Repr(pkg.Value("sha256")):
			t.Errorf("pacote de outro componente mudou: %s", filename)
		}
	}
	if !found {
		t.Fatalf("catalogo nao registrou %s na release nquake-%s", movedFile, revision)
	}

	// The installed CLI reads this mixed-revision catalog package by package.
	raw, err := os.ReadFile(fresh.Path(CatalogPath))
	if err != nil {
		t.Fatal(err)
	}
	parsed, err := clicatalog.Parse(raw)
	if err != nil {
		t.Fatalf("a CLI recusa o catalogo: %v", err)
	}
	for _, pkg := range objects(after.Value("packages")) {
		if pkg.Value("channel") != "content" {
			continue
		}
		id := pyjson.Repr(getOr(pkg, "package", pkg.Value("component")))
		current, err := parsed.ComponentPackage(id, []string{pyjson.Repr(pkg.Value("component"))})
		if err != nil {
			t.Fatalf("a CLI recusa o pacote %s: %v", id, err)
		}
		if id == moved && current.Revision() != revision {
			t.Fatalf("a CLI le %s na revisao %s", id, current.Revision())
		}
	}
	if _, err := os.Stat(filepath.Join(clone, DistDir, "distributions/nquake", previous, "non-gpl/qw/maps")); err != nil {
		t.Errorf("arquivos dos outros componentes sairam da revisao anterior: %v", err)
	}
}
