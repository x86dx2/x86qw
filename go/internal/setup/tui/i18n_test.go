package tui

import (
	"os"
	"path/filepath"
	"regexp"
	"testing"

	"github.com/x86dx2/x86qw/go/internal/setup"
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
				t.Errorf("%s: text without a translation: %q", file, match[1])
			}
		}
	}
}

// TestEnglishIsTheDefault: without a language the interface speaks
// English; Portuguese is the one ctrl+l switches to.
func TestEnglishIsTheDefault(t *testing.T) {
	if T("", "host") != "Host" || T(PT, "host") != "Hospedar" {
		t.Fatalf("%q · %q", T("", "host"), T(PT, "host"))
	}
	m := New(Config{Backend: &setup.Demo{}, Target: t.TempDir()})
	if m.lang != EN {
		t.Fatalf("the interface opens in %q", m.lang)
	}
	m.Update(key("ctrl+l"))
	if m.lang != PT {
		t.Fatalf("ctrl+l switches to %q", m.lang)
	}
}
