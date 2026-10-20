package tui

import (
	"os"
	"path/filepath"
	"regexp"
	"testing"
)

// TestEveryTextIsTranslated catches a key used in the code but missing
// from the translations, which would show the raw key on screen.
func TestEveryTextIsTranslated(t *testing.T) {
	files, _ := filepath.Glob("*.go")
	use := regexp.MustCompile(`m\.t\("([^"]+)"\)`)
	for _, file := range files {
		source, err := os.ReadFile(file)
		if err != nil {
			t.Fatal(err)
		}
		for _, match := range use.FindAllStringSubmatch(string(source), -1) {
			if _, ok := texts[match[1]]; !ok {
				t.Errorf("%s: texto sem tradução: %q", file, match[1])
			}
		}
	}
}
