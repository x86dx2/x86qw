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
		t.Fatalf("catálogo: %d %v", count, err)
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
		t.Fatalf("receitas: %v %v", recipes, err)
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
		t.Fatal("product.json regenerado diverge do publicado")
	}
}

func TestPortablePaths(t *testing.T) {
	for _, value := range []string{"a//b", "a/./b", "/a", "a/..", "CON.txt", "com¹", "a:b", "a\u0000b", "trailing.", "trailing ", "é"} {
		if _, err := ValidatePortableRelativePath(value, "path"); err == nil {
			t.Errorf("caminho não portável aceito: %q", value)
		}
	}
	for _, value := range []string{"qw/maps/dm4.bsp", "ação/é.cfg", "a.b.c"} {
		if _, err := ValidatePortableRelativePath(value, "path"); err != nil {
			t.Errorf("caminho portável rejeitado: %q: %v", value, err)
		}
	}
	// _safe_path keeps PurePosixPath semantics: "//" and "." collapse.
	if _, err := safePath("a//./b", "path"); err != nil {
		t.Errorf("safePath diverge de PurePosixPath: %v", err)
	}
	if _, err := safePath("a/../b", "path"); err == nil {
		t.Error("safePath aceitou ..")
	}
}

func TestHTTPSURLPolicy(t *testing.T) {
	for _, value := range []string{"http://x.test/a", "https://user@x.test/a", "https://x.test/a#f", "https:///a", "https://x.test:99999/a", "https://[::1/a", "https://x .test/a"} {
		if _, err := validateHTTPSURL(value, "URL"); err == nil {
			t.Errorf("URL inválida aceita: %s", value)
		}
	}
	parsed, err := validateHTTPSURL("HTTPS://Example.TEST:443/a%20b.zip?q=1", "URL")
	if err != nil || parsed.Hostname() != "example.test" || parsed.Query != "q=1" || unquote(parsed.Path) != "/a b.zip" {
		t.Fatalf("URL válida mal interpretada: %+v %v", parsed, err)
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
		t.Error("catálogo legado rejeitado")
	}
	if validateCatalogVersions(decode(`{"format": 1}`), false) == nil {
		t.Error("catálogo sem min_cli_version aceito sem legado")
	}
	if validateCatalogVersions(decode(`{"format": 1, "catalog_version": 1}`), true) == nil {
		t.Error("versão explícita sem min_cli_version aceita")
	}
	if validateCatalogVersions(decode(`{"catalog_version": 1, "min_cli_version": "0.7.0", "max_cli_version": "0.6.0"}`), false) == nil {
		t.Error("faixa de CLI invertida aceita")
	}
	if validateCatalogVersions(decode(`{"catalog_version": 1, "state_version": 2, "min_cli_version": "0.7.0"}`), false) == nil {
		t.Error("campo de schema alheio aceito")
	}
}
