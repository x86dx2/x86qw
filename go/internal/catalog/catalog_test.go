package catalog

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func repoFile(t *testing.T, parts ...string) []byte {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(append([]string{"..", "..", ".."}, parts...)...))
	if err != nil {
		t.Fatal(err)
	}
	return data
}

func TestEmbeddedInventoryMatchesMaintenance(t *testing.T) {
	if !bytes.Equal(ComponentsJSON, repoFile(t, "maintenance", "inventory", "components.json")) {
		t.Fatal("go/internal/catalog/components.json diverge de maintenance/inventory/components.json")
	}
}

func TestProfilesResolveToTheirComponents(t *testing.T) {
	inventory, err := LoadInventory()
	if err != nil {
		t.Fatal(err)
	}
	essential, err := inventory.Desired("essential", nil)
	if err != nil {
		t.Fatal(err)
	}
	if got := strings.Join(essential, ","); got != "x86qw-client-bootstrap,nquake-visual-core,ktx" {
		t.Fatalf("essential: %s", got)
	}
	complete, _ := inventory.Desired("complete", nil)
	if len(complete) != 21 {
		t.Fatalf("complete: %d", len(complete))
	}
	custom, _ := inventory.Desired("custom", []string{"qrp-hires"})
	if got := strings.Join(custom, ","); got != "x86qw-client-bootstrap,nquake-visual-core,qrp-hires" {
		t.Fatalf("custom: %s", got)
	}
}

func TestCatalogSelectsComponentAndClientPackages(t *testing.T) {
	inventory, _ := LoadInventory()
	cat, err := Parse(repoFile(t, "site", "public", "api", "v1", "catalog.json"))
	if err != nil {
		t.Fatal(err)
	}
	for _, id := range inventory.IDs() {
		pkg, err := cat.ComponentPackage(id, inventory.ContentNamespaces)
		if err != nil {
			t.Fatalf("%s: %v", id, err)
		}
		if pkg.Size <= 0 {
			t.Fatalf("%s sem tamanho", id)
		}
	}
	for _, spec := range ClientSpecs {
		for _, channel := range []string{"stable", "nightly"} {
			releases, err := cat.ClientReleases(spec, channel)
			if err != nil {
				t.Fatalf("%s %s: %v", spec.Platform, channel, err)
			}
			for index := 1; index < len(releases); index++ {
				if ReleaseIsNewer(releases[index].Version, releases[index-1].Version, channel) {
					t.Fatalf("%s %s fora de ordem", spec.Platform, channel)
				}
			}
		}
	}
}

func TestResolveRejectsCycles(t *testing.T) {
	_, err := ParseInventory([]byte(`{"components":[{"id":"a","requires":["b"]},{"id":"b","requires":["a"]}]}`))
	if err == nil {
		t.Fatal("ciclo aceito")
	}
}
