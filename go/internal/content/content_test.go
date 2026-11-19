package content

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
)

func TestCheckoutInventoriesValidate(t *testing.T) {
	project := NewProject(repoRoot(t))
	catalog, err := LoadJSONObject(project.Path(CatalogPath))
	if err != nil {
		t.Fatal(err)
	}
	if count, err := project.ValidateCatalog(catalog); err != nil || count == 0 {
		t.Fatalf("catalog: %d %v", count, err)
	}
	if _, err := project.Components(); err != nil {
		t.Fatal(err)
	}
	if _, err := project.Releases(); err != nil {
		t.Fatal(err)
	}
	if _, err := project.Upstreams(); err != nil {
		t.Fatal(err)
	}
	recipes, err := RecipePaths(project.Path(RecipesDir))
	if err != nil || len(recipes) == 0 {
		t.Fatalf("recipes: %v %v", recipes, err)
	}
	for _, path := range recipes {
		recipe, _ := LoadJSONObject(path)
		if _, err := project.ValidateRecipe(recipe, path); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := LoadManifest(project.Path(ManifestPath)); err != nil {
		t.Fatal(err)
	}
}

// The client refuses a stable ezQuake version with a leading zero.
func TestCatalogRefusesStableVersionsTheClientRejects(t *testing.T) {
	project := NewProject(repoRoot(t))
	catalog, err := LoadJSONObject(project.Path(CatalogPath))
	if err != nil {
		t.Fatal(err)
	}
	for _, pkg := range objects(catalog.Value("packages")) {
		if pyjson.Eq(pkg.Value("component"), "ezquake") && pyjson.Eq(pkg.Value("channel"), "stable") {
			version := pkg.Value("version").(string)
			pkg.Set("version", "0"+version)
			pkg.Set("distribution_path", strings.Replace(pkg.Value("distribution_path").(string), "/"+version+"/", "/0"+version+"/", 1))
			break
		}
	}
	if _, err := project.ValidateCatalog(catalog); err == nil || !strings.Contains(err.Error(), ".version is invalid for ezquake stable") {
		t.Fatalf("a stable version with a leading zero was not refused: %v", err)
	}
}

// A catalog entry served from dist must describe that file, or installs fail.
func TestCatalogPackagesMatchTheirDistributionFiles(t *testing.T) {
	manifest := object("files", object("clients/a.zip", object("size", pyjson.Number("3"), "sha256", strings.Repeat("a", 64))))
	catalog := func() *Object {
		return object("packages", []any{object("distribution_path", "clients/a.zip", "size", pyjson.Number("3"), "sha256", strings.Repeat("a", 64))})
	}
	if err := distributionPackagesMatch(catalog(), manifest); err != nil {
		t.Fatal(err)
	}
	for key, value := range map[string]any{"size": pyjson.Number("4"), "sha256": strings.Repeat("b", 64), "distribution_path": "clients/b.zip"} {
		document := catalog()
		firstObject(document, "packages").Set(key, value)
		if distributionPackagesMatch(document, manifest) == nil {
			t.Errorf("a catalog package with another %s was accepted", key)
		}
	}
}

func TestProductIsByteIdentical(t *testing.T) {
	root := repoRoot(t)
	requireHydrated(t, root)
	project := NewProject(root)
	expected, err := project.EncodedProductCatalog()
	if err != nil {
		t.Fatal(err)
	}
	actual, _ := os.ReadFile(project.Path(ProductPath))
	if string(expected) != string(actual) {
		t.Fatal("regenerated product.json differs from the published one")
	}
}

// build --register over packages the catalog already lists writes the same
// bytes, so the TUF-signed catalog stays valid.
func TestRegisteringUnchangedPackagesKeepsTheCatalog(t *testing.T) {
	project := NewProject(repoRoot(t))
	committed, err := os.ReadFile(project.Path(CatalogPath))
	if err != nil {
		t.Fatal(err)
	}
	catalog, err := LoadJSONObject(project.Path(CatalogPath))
	if err != nil {
		t.Fatal(err)
	}
	var built []any
	for _, pkg := range objects(catalog.Value("packages")) {
		if pyjson.Eq(pkg.Value("channel"), "content") {
			built = append(built, pkg)
		}
	}
	path := filepath.Join(t.TempDir(), "catalog.json")
	if err := os.WriteFile(path, committed, 0o644); err != nil {
		t.Fatal(err)
	}
	if err := project.RegisterPackages(path, object("packages", built), time.Now()); err != nil {
		t.Fatal(err)
	}
	registered, err := LoadJSONObject(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := project.NormalizeCatalogReleaseMetadata(registered); err != nil {
		t.Fatal(err)
	}
	if err := WriteJSON(path, registered); err != nil {
		t.Fatal(err)
	}
	if after, _ := os.ReadFile(path); !bytes.Equal(after, committed) {
		t.Fatal("registering unchanged packages rewrote the catalog")
	}
}

func TestPortablePaths(t *testing.T) {
	for _, value := range []string{"a//b", "a/./b", "/a", "a/..", "CON.txt", "com¹", "a:b", "a\u0000b", "trailing.", "trailing ", "é"} {
		if _, err := ValidatePortableRelativePath(value, "path"); err == nil {
			t.Errorf("non-portable path accepted: %q", value)
		}
	}
	for _, value := range []string{"qw/maps/dm4.bsp", "ação/é.cfg", "a.b.c"} {
		if _, err := ValidatePortableRelativePath(value, "path"); err != nil {
			t.Errorf("portable path rejected: %q: %v", value, err)
		}
	}
	// safePath collapses "//" and ".".
	if _, err := safePath("a//./b", "path"); err != nil {
		t.Errorf("safePath differs from PurePosixPath: %v", err)
	}
	if _, err := safePath("a/../b", "path"); err == nil {
		t.Error("safePath accepted ..")
	}
}

func TestHTTPSURLPolicy(t *testing.T) {
	for _, value := range []string{"http://x.test/a", "https://user@x.test/a", "https://x.test/a#f", "https:///a", "https://x.test:99999/a", "https://[::1/a", "https://x .test/a"} {
		if _, err := validateHTTPSURL(value, "URL"); err == nil {
			t.Errorf("invalid URL accepted: %s", value)
		}
	}
	parsed, err := validateHTTPSURL("HTTPS://Example.TEST:443/a%20b.zip?q=1", "URL")
	if err != nil || parsed.Hostname() != "example.test" || parsed.Query != "q=1" || unquote(parsed.Path) != "/a b.zip" {
		t.Fatalf("valid URL misread: %+v %v", parsed, err)
	}
}

func TestSchemaVersions(t *testing.T) {
	decode := func(text string) *Object {
		value, err := pyjson.Decode([]byte(text), pyjson.LastWins)
		if err != nil {
			t.Fatal(err)
		}
		return value.(*Object)
	}
	if validateCatalogVersions(decode(`{"format": 1}`), true) != nil {
		t.Error("legacy catalog rejected")
	}
	if validateCatalogVersions(decode(`{"format": 1}`), false) == nil {
		t.Error("catalog without min_cli_version accepted without legacy")
	}
	if validateCatalogVersions(decode(`{"format": 1, "catalog_version": 1}`), true) == nil {
		t.Error("explicit version without min_cli_version accepted")
	}
	if validateCatalogVersions(decode(`{"catalog_version": 1, "min_cli_version": "0.7.0", "max_cli_version": "0.6.0"}`), false) == nil {
		t.Error("inverted CLI range accepted")
	}
	if validateCatalogVersions(decode(`{"catalog_version": 1, "state_version": 2, "min_cli_version": "0.7.0"}`), false) == nil {
		t.Error("foreign schema field accepted")
	}
}

// A move whose destination another move of the same archive takes as its
// source would drop that member: a→b then b→c leaves only c.
func TestArchiveMovesRefuseAChain(t *testing.T) {
	move := func(target, source, destination string) any {
		return object("target_archive", target, "source", source, "destination", destination)
	}
	chained := object("archive_moves", []any{move("x.zip", "a", "b"), move("x.zip", "b", "c")})
	if err := validateArchiveEdits(chained, "upstream-package", "test"); err == nil {
		t.Error("chained archive moves accepted")
	}
	apart := object("archive_moves", []any{move("x.zip", "a", "b"), move("y.zip", "b", "c")})
	if err := validateArchiveEdits(apart, "upstream-package", "test"); err != nil {
		t.Errorf("moves in two archives refused: %v", err)
	}
}

// The launcher runs a mode's entry config from the event its usermode
// enters with (on_enter_ffa for instagib's ffa); an entry config that does
// not clear that event runs the mode command again on every map.
func TestKTXEntryConfigClearsItsEvent(t *testing.T) {
	project := NewProject(repoRoot(t))
	components, err := project.Components()
	if err != nil {
		t.Fatal(err)
	}
	if err := project.ValidateKTXModeCatalog(components, nil); err != nil {
		t.Fatalf("shipped mode catalog: %v", err)
	}
	modes, err := ReadJSON(project.Path(ktxModesPath))
	if err != nil {
		t.Fatal(err)
	}
	for _, mode := range objects(modes.(*Object).Value("modes")) {
		if mode.Value("id") == "instagib" {
			mode.Set("usermode", "1on1")
		}
	}
	if err := project.ValidateKTXModeCatalog(components, modes); err == nil {
		t.Fatal("instagib entered by on_enter while its config clears on_enter_ffa")
	}
}

// Mirror release titles and notes are public, so they are in English.
func TestMirrorMetadataIsEnglish(t *testing.T) {
	root := repoRoot(t)
	requireHydrated(t, root)
	project := NewProject(root)
	if os.Getenv("X86QW_CONTENT_PARITY_BUILD") == "1" {
		built, err := project.BuildPackages(project.Path(DistDir), t.TempDir(), BuildOptions{})
		if err != nil {
			t.Fatal(err)
		}
		for _, pkg := range objects(built.Value("packages")) {
			if notes := pkg.Value("mirror_notes"); notes != "Versioned content packages of the x86QW distribution." {
				t.Errorf("%s notes: %v", pkg.Value("package"), notes)
			}
		}
	}
	core, err := project.BuildCorePackage(project.Path(DistDir), t.TempDir(), "")
	if err != nil {
		t.Fatal(err)
	}
	if title, notes := core.Value("mirror_title"), core.Value("mirror_notes"); title != "x86QW Content · Base game data "+coreVersion ||
		notes != "Base game data the x86QW installer requires." {
		t.Errorf("core: %v / %v", title, notes)
	}
	catalog, err := LoadJSONObject(project.Path(CatalogPath))
	if err != nil {
		t.Fatal(err)
	}
	for _, pkg := range objects(catalog.Value("packages")) {
		pkg.Delete("mirror_notes")
	}
	if err := project.NormalizeCatalogReleaseMetadata(catalog); err != nil {
		t.Fatal(err)
	}
	for _, pkg := range objects(catalog.Value("packages")) {
		if pkg.Value("component") == "ezquake" {
			want := "ezQuake " + pyjson.Repr(pkg.Value("channel")) + " client " + pyjson.Repr(pkg.Value("version")) + ", preserved by the x86QW distribution."
			if pkg.Value("mirror_notes") != want {
				t.Errorf("ezQuake notes: %v", pkg.Value("mirror_notes"))
			}
		}
	}
}

// The core package holds id Software's registered game data: its license
// link leads to the provenance record, not to x86QW's MIT license.
func TestCorePackageLinksItsProvenance(t *testing.T) {
	root := repoRoot(t)
	requireHydrated(t, root)
	project := NewProject(root)
	core, err := project.BuildCorePackage(project.Path(DistDir), t.TempDir(), "")
	if err != nil {
		t.Fatal(err)
	}
	want := "https://github.com/x86dx2/x86qw/blob/" + coreReleaseTag + "/maintenance/docs/provenance.md"
	if got := core.Value("license_url"); got != want {
		t.Fatalf("core license_url %v, want %s", got, want)
	}
}
