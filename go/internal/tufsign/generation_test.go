package tufsign

import (
	"os"
	"path/filepath"
	"slices"
	"testing"
)

func checkoutProjection(t *testing.T) string {
	t.Helper()
	dir, err := filepath.Abs(filepath.Join("..", "..", "..", "site", "public", "api", "v1", "trust"))
	if err != nil {
		t.Fatal(err)
	}
	return dir
}

// TestCheckoutKeepsOnlyItsGeneration: the checkout serves one generation
// and holds nothing else; git history keeps the old ones.
func TestCheckoutKeepsOnlyItsGeneration(t *testing.T) {
	dir := checkoutProjection(t)
	g, err := ReadGeneration(dir)
	if err != nil {
		t.Fatal(err)
	}
	if g.Timestamp < g.Snapshot || g.Snapshot < g.Targets || len(g.Catalog) != 64 || g.Expires.IsZero() {
		t.Fatalf("inconsistent generation: %+v", g)
	}
	for _, file := range g.Files() {
		if _, err := os.Stat(filepath.Join(dir, filepath.FromSlash(file))); err != nil {
			t.Fatal(err)
		}
	}
	if stale, err := Stale(dir); err != nil || len(stale) > 0 {
		t.Fatalf("the projection keeps old generations: %v %v", stale, err)
	}
}

func TestPruneDropsOnlyOldGenerations(t *testing.T) {
	source := checkoutProjection(t)
	dir := t.TempDir()
	g, err := ReadGeneration(source)
	if err != nil {
		t.Fatal(err)
	}
	for _, file := range g.Files() {
		data, err := os.ReadFile(filepath.Join(source, filepath.FromSlash(file)))
		if err != nil {
			t.Fatal(err)
		}
		path := filepath.Join(dir, filepath.FromSlash(file))
		os.MkdirAll(filepath.Dir(path), 0o755)
		if err := os.WriteFile(path, data, 0o644); err != nil {
			t.Fatal(err)
		}
	}
	old := []string{"metadata/3.snapshot.json", "metadata/3.targets.json", "targets/catalog/" + "00" + g.Catalog[2:] + ".catalog.json"}
	for _, file := range old {
		os.WriteFile(filepath.Join(dir, filepath.FromSlash(file)), []byte("{}"), 0o644)
	}
	removed, err := Prune(dir)
	slices.Sort(removed)
	slices.Sort(old)
	if err != nil || !slices.Equal(removed, old) {
		t.Fatalf("removed %v, expected %v: %v", removed, old, err)
	}
	if _, err := ReadGeneration(dir); err != nil {
		t.Fatalf("the current generation did not survive: %v", err)
	}
}
