package cli

import (
	"os"
	"path/filepath"
	"testing"
)

func TestReplacesNeverDowngrades(t *testing.T) {
	root := t.TempDir()
	if !Replaces("2.3.1", root, false) {
		t.Fatal("sem CLI instalada, uma release se instala")
	}
	os.MkdirAll(filepath.Join(root, filepath.FromSlash(cliDir)), 0o755)
	os.WriteFile(filepath.Join(root, filepath.FromSlash(cliDir), "receipt"), Receipt("2.3.1"), 0o644)
	for _, c := range []struct {
		version string
		same    bool
		want    bool
	}{
		{"2.3.2", false, true}, {"2.3.1", false, false}, {"2.3.1", true, true},
		{"2.3.0", true, false}, {"0.0.0-dev", true, false}, {"lixo", true, false},
	} {
		if got := Replaces(c.version, root, c.same); got != c.want {
			t.Fatalf("%s (mesma=%v): %v", c.version, c.same, got)
		}
	}
}
