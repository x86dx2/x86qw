package content

import (
	"bytes"
	"net/http"
	"os"
	"path/filepath"
	"slices"
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
		t.Skip("set X86QW_CONTENT_PARITY_BUILD=1 to exercise a full update")
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
	if len(unmirrored) == 0 {
		// Everything is mirrored: forget one release artifact so the update
		// has to fetch, verify and register it again (the fake upstream
		// serves it from the clone).
		const relative = "servers/mvdsv/1.11/source/mvdsv-source-with-submodules-1.11.zip"
		editJSON(t, project.Path(ManifestPath), true, func(document *Object) {
			document.Value("files").(*Object).Delete(relative)
		})
		unmirrored = []string{relative}
	}
	project.HTTP = currentUpstreams(t, clone, "").client()
	git := &recorder{}
	project.Run = git.run
	var out bytes.Buffer
	if err := project.Update(&out, UpdateOptions{Workers: 4, Yes: true}); err != nil {
		t.Fatalf("update: %v\n%s", err, out.String())
	}
	if len(git.commands) != 1 || git.commands[0] != "git status --porcelain" {
		t.Fatalf("update ran git beyond the check: %v", git.commands)
	}

	fresh := NewProject(clone)
	manifest, err := LoadManifest(fresh.Path(ManifestPath))
	if err != nil {
		t.Fatal(err)
	}
	files := manifest.Value("files").(*Object)
	for _, path := range unmirrored {
		if !files.Has(path) {
			t.Errorf("manifest did not incorporate %s", path)
		}
	}
	if _, err := fresh.VerifyDistribution(fresh.Path(DistDir), manifest); err != nil {
		t.Fatalf("updated dist does not verify: %v", err)
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
		t.Fatalf("catalog recorded %d content packages for %d builds", content, len(zips))
	}
	leftovers, _ := filepath.Glob(filepath.Join(clone, "maintenance/build/update-*"))
	if len(leftovers) != 0 {
		t.Fatalf("update workspace was not removed: %v", leftovers)
	}
	if !strings.Contains(out.String(), "[OK] Working tree updated: ezQuake 3.6.9/") {
		t.Fatalf("unexpected summary:\n%s", out.String())
	}
}

// TestAddMovesOneComponentToAnotherNQuakeRevision moves only
// x86qw-client-bootstrap to a new nQuake revision through 'add' on a clone:
// its files go to the new revision directory and its package is rebuilt
// with a new version, while every other component keeps its revision, its
// files and its package bytes.
func TestAddMovesOneComponentToAnotherNQuakeRevision(t *testing.T) {
	if os.Getenv("X86QW_CONTENT_PARITY_BUILD") != "1" {
		t.Skip("set X86QW_CONTENT_PARITY_BUILD=1 to exercise a full add")
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
	pinnedBefore := ReferenceRevisions(releases)
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
		upstream, ok := nquakePath(relative)
		if !ok || metadata.Value("package") != moved {
			continue
		}
		url := "https://raw.githubusercontent.com/nQuake/distfiles/" + revision + "/" + quotePath(upstream)
		bodies[url] = filepath.Join(clone, DistDir, filepath.FromSlash(relative))
		files = append(files, object("destination", "dist/"+relative, "url", url,
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
		t.Fatalf("dist after add: %v", err)
	}
	// The dist holds exactly the revisions some component pins; the one
	// only the moved component used is gone.
	expected := map[string]bool{}
	for _, pinned := range ReferenceRevisions(updatedReleases) {
		expected[pinned] = true
	}
	if revisions := sources.Revisions(); !slices.Equal(revisions, sortedKeys(expected)) || !expected[revision] {
		t.Fatalf("nQuake revisions in dist: %v", revisions)
	}
	updatedManifest, _ := LoadManifest(fresh.Path(ManifestPath))
	if _, err := fresh.VerifyDistribution(fresh.Path(DistDir), updatedManifest); err != nil {
		t.Fatalf("dist after add does not verify: %v", err)
	}
	for identifier, pinned := range ReferenceRevisions(updatedReleases) {
		want := pinnedBefore[identifier]
		if identifier == moved {
			want = revision
		}
		if pinned != want {
			t.Errorf("%s pins %s, expected %s", identifier, pinned, want)
		}
	}
	if version := pyjson.Get(updatedReleases, "components", moved, "version"); version != "222222222222+x86qw.4" {
		t.Errorf("bootstrap version: %v", version)
	}
	after, _ := LoadJSONObject(fresh.Path(CatalogPath))
	movedFile := moved + "-222222222222+x86qw.4.zip"
	found := false
	for _, pkg := range objects(after.Value("packages")) {
		filename := pyjson.Repr(pkg.Value("filename"))
		switch {
		case pkg.Value("channel") != "content" || strings.HasPrefix(filename, "x86qw-core-id1-"):
		case filename == movedFile:
			found = strings.Contains(pyjson.Repr(pkg.Value("origin_url")), "/releases/download/nquake-"+revision+"/") &&
				pkg.Value("source_commit") == revision
		case before[filename] != pyjson.Repr(pkg.Value("sha256")):
			t.Errorf("another component's package changed: %s", filename)
		}
	}
	if !found {
		t.Fatalf("catalog did not record %s in release nquake-%s", movedFile, revision)
	}

	// The installed CLI reads this mixed-revision catalog package by package.
	raw, err := os.ReadFile(fresh.Path(CatalogPath))
	if err != nil {
		t.Fatal(err)
	}
	parsed, err := clicatalog.Parse(raw)
	if err != nil {
		t.Fatalf("the CLI refuses the catalog: %v", err)
	}
	for _, pkg := range objects(after.Value("packages")) {
		if pkg.Value("channel") != "content" {
			continue
		}
		id := pyjson.Repr(getOr(pkg, "package", pkg.Value("component")))
		current, err := parsed.ComponentPackage(id, []string{pyjson.Repr(pkg.Value("component"))})
		if err != nil {
			t.Fatalf("the CLI refuses package %s: %v", id, err)
		}
		if id == moved && current.Revision() != revision {
			t.Fatalf("the CLI reads %s at revision %s", id, current.Revision())
		}
	}
	if _, err := os.Stat(filepath.Join(clone, DistDir, "distributions/nquake/non-gpl/qw/maps")); err != nil {
		t.Errorf("other components' files left the previous revision: %v", err)
	}
}

// TestUpdateFollowsNQuakeOneComponentAtATime runs a real update on a clone
// after nQuake changed one file of x86qw-client-bootstrap and added an
// unused one: only the bootstrap moves to the new revision, its x86QW
// replacements are re-pinned, the inventory records the new revision and
// every other package keeps its bytes.
func TestUpdateFollowsNQuakeOneComponentAtATime(t *testing.T) {
	if os.Getenv("X86QW_CONTENT_PARITY_BUILD") != "1" {
		t.Skip("set X86QW_CONTENT_PARITY_BUILD=1 to exercise a full update")
	}
	root := repoRoot(t)
	requireHydrated(t, root)
	clone := cloneCheckout(t, root)
	const moved = "x86qw-client-bootstrap"

	project := NewProject(clone)
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
	releases, _ := project.Releases()
	pinnedBefore := ReferenceRevisions(releases)

	web := movedReference(currentUpstreams(t, clone, ""), appendComment)
	web.nquake.files["sv-maps/qw/maps/new.bsp"] = NQuakeFile{3, sha256Hex([]byte("bsp")), gitBlob([]byte("bsp"))}
	project.HTTP = web.client()
	project.Run = (&recorder{}).run
	var out bytes.Buffer
	if err := project.Update(&out, UpdateOptions{Workers: 4, Yes: true}); err != nil {
		t.Fatalf("update: %v\n%s", err, out.String())
	}

	fresh := NewProject(clone)
	updated, err := fresh.Releases()
	if err != nil {
		t.Fatal(err)
	}
	for identifier, pinned := range ReferenceRevisions(updated) {
		want := pinnedBefore[identifier]
		if identifier == moved {
			want = movedRevision
		}
		if pinned != want {
			t.Errorf("%s pins %s, expected %s", identifier, pinned, want)
		}
	}
	if version := pyjson.Get(updated, "components", moved, "version"); version != "222222222222+x86qw.4" {
		t.Errorf("bootstrap version: %v", version)
	}
	newFile, _ := web.nquake.body(changedFile)
	for _, replacement := range objects(pyjson.Get(updated, "components", moved, "text_replacements")) {
		if replacement.Value("source_sha256") != sha256Hex(newFile) {
			t.Errorf("replacement was not re-pinned on the new file: %v", replacement.Value("before"))
		}
	}
	inventory, err := LoadNQuakeInventory(fresh.Path(NQuakeInventoryPath))
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := inventory.Files["sv-maps/qw/maps/new.bsp"]; inventory.Revision != movedRevision || !ok {
		t.Errorf("nQuake inventory did not advance: %s", inventory.Revision)
	}
	components, _ := fresh.Components()
	if _, err := LoadSourceContext(fresh.Path(DistDir), components, updated); err != nil {
		t.Fatalf("dist after update: %v", err)
	}
	after, _ := LoadJSONObject(fresh.Path(CatalogPath))
	movedFile := moved + "-222222222222+x86qw.4.zip"
	found := false
	for _, pkg := range objects(after.Value("packages")) {
		filename := pyjson.Repr(pkg.Value("filename"))
		switch {
		case pkg.Value("channel") != "content" || strings.HasPrefix(filename, "x86qw-core-id1-"):
		case filename == movedFile:
			found = pkg.Value("source_commit") == movedRevision
		case before[filename] != pyjson.Repr(pkg.Value("sha256")):
			t.Errorf("another component's package changed: %s", filename)
		}
	}
	if !found {
		t.Fatalf("catalog did not record %s:\n%s", movedFile, out.String())
	}
}
