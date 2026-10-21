package content

import (
	"os"
	"testing"

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
