package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"testing"
)

// The page's counts and game versions are what product.json says; site.js
// does not fill them.
func TestPageCountsFollowTheProduct(t *testing.T) {
	repo := filepath.Join("..", "..", "..")
	payload, err := os.ReadFile(filepath.Join(repo, "site", "public", "api", "v1", "product.json"))
	if err != nil {
		t.Fatal(err)
	}
	var product struct {
		Packages   int `json:"package_count"`
		Components int `json:"component_count"`
		Games      []struct {
			Label   string `json:"label"`
			Version string `json:"version"`
		} `json:"games"`
	}
	if err := json.Unmarshal(payload, &product); err != nil {
		t.Fatal(err)
	}
	page, err := os.ReadFile(filepath.Join(repo, filepath.FromSlash(siteDir), "index.html"))
	if err != nil {
		t.Fatal(err)
	}
	for class, want := range map[string]int{"release-package-count": product.Packages, "release-component-count": product.Components} {
		matches := regexp.MustCompile(`<span class="`+class+`">([0-9]+)</span>`).FindAllSubmatch(page, -1)
		if len(matches) == 0 {
			t.Errorf("index.html does not show %s", class)
		}
		for _, match := range matches {
			if string(match[1]) != strconv.Itoa(want) {
				t.Errorf("index.html shows %s = %s; product.json says %d", class, match[1], want)
			}
		}
	}
	versions := map[string]string{}
	for _, game := range product.Games {
		versions[game.Label] = game.Version
	}
	// The hero and the game list each name every game with its version.
	for _, shown := range []string{`<li>([^<]+) <span>([^<]+)</span></li>`, `<p class="game-name">([^<]+) <span>([^<]+)</span></p>`} {
		matches := regexp.MustCompile(shown).FindAllSubmatch(page, -1)
		if len(matches) != len(product.Games) {
			t.Errorf("index.html shows %d games as %s; product.json has %d", len(matches), shown, len(product.Games))
		}
		for _, match := range matches {
			if want := versions[string(match[1])]; string(match[2]) != want {
				t.Errorf("index.html shows %s %s; product.json says %q", match[1], match[2], want)
			}
		}
	}
}
