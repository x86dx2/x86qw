package main

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"
)

// TestCheckoutNamesOneRelease: the bootstraps are exactly what the
// catalog's CLI pins render, and README.md and the page name only that
// release. product.json is checked against the catalog by x86qw-content.
func TestCheckoutNamesOneRelease(t *testing.T) {
	repo := filepath.Join("..", "..", "..")
	raw, err := os.ReadFile(filepath.Join(repo, filepath.FromSlash(catalogFile)))
	if err != nil {
		t.Fatal(err)
	}
	p, err := catalogPins(raw)
	if err != nil {
		t.Fatal(err)
	}
	for _, name := range bootstraps {
		want, err := renderBootstrap(p, name)
		if err != nil {
			t.Fatal(err)
		}
		got, err := os.ReadFile(filepath.Join(repo, filepath.FromSlash(siteDir), name))
		if err != nil {
			t.Fatal(err)
		}
		if !bytes.Equal(got, want) {
			t.Errorf("%s não fixa os binários da CLI %s do catálogo; rode x86qw-release prepare", name, p.Version)
		}
	}
	tag := []byte("x86qw-cli-" + p.Version)
	span := []byte(`<span class="release-product-version">` + p.Version + `</span>`)
	for _, document := range versionDocuments {
		payload, err := os.ReadFile(filepath.Join(repo, filepath.FromSlash(document)))
		if err != nil {
			t.Fatal(err)
		}
		found := 0
		for _, match := range append(releaseTag.FindAll(payload, -1), releaseSpan.FindAll(payload, -1)...) {
			if !bytes.Equal(match, tag) && !bytes.Equal(match, span) {
				t.Errorf("%s cita %s, mas o catálogo traz a CLI %s", document, match, p.Version)
			}
			found++
		}
		if found == 0 {
			t.Errorf("%s não cita a release da CLI", document)
		}
	}
}

func TestReleaseNamesFollowTheVersion(t *testing.T) {
	path := filepath.Join(t.TempDir(), "page.html")
	page := "release `x86qw-cli-2.3.2` e <span class=\"release-product-version\">2.3.2</span>, não x86qw-cli-2.3.2-rc.1"
	if err := os.WriteFile(path, []byte(page), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := nameRelease(path, "2.4.0"); err != nil {
		t.Fatal(err)
	}
	got, _ := os.ReadFile(path)
	if want := "release `x86qw-cli-2.4.0` e <span class=\"release-product-version\">2.4.0</span>, não x86qw-cli-2.4.0"; string(got) != want {
		t.Fatalf("%s", got)
	}
}
