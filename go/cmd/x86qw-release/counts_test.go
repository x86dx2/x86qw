package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"testing"
)

// The page's counts are what product.json says; site.js does not fill them.
func TestPageCountsFollowTheProduct(t *testing.T) {
	repo := filepath.Join("..", "..", "..")
	payload, err := os.ReadFile(filepath.Join(repo, "site", "public", "api", "v1", "product.json"))
	if err != nil {
		t.Fatal(err)
	}
	var product struct {
		Packages   int `json:"package_count"`
		Components int `json:"component_count"`
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
}
