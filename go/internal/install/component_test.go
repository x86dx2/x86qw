package install

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/x86dx2/x86qw/go/internal/archive"
	"github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/download"
	"github.com/x86dx2/x86qw/go/internal/receipt"
)

func componentCacheDir(t *testing.T) string {
	t.Helper()
	root, err := download.CacheDir()
	if err != nil {
		t.Skip("no component cache")
	}
	dir := filepath.Join(root, "components")
	if _, err := os.Stat(dir); err != nil {
		t.Skip("no component cache")
	}
	return dir
}

// TestPreparedPackagesMatchInstalledInventories prepares every cached real
// package and requires the payload to equal what the installation recorded.
func TestPreparedPackagesMatchInstalledInventories(t *testing.T) {
	root := realRoot(t)
	cache := componentCacheDir(t)
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "site", "public", "api", "v1", "catalog.json"))
	if err != nil {
		t.Fatal(err)
	}
	cat, _ := catalog.Parse(raw)
	inventory, _ := catalog.LoadInventory()
	variant, err := HostVariant()
	if err != nil {
		t.Skip(err)
	}
	for _, component := range inventory.Components {
		pkg, err := cat.ComponentPackage(component.ID, inventory.ContentNamespaces)
		if err != nil {
			t.Fatal(err)
		}
		artifact := filepath.Join(cache, pkg.Filename)
		if _, err := os.Stat(artifact); err != nil {
			continue
		}
		record, err := receipt.Load(filepath.Join(root, receipt.ComponentsDir, component.ID))
		if err != nil || record.Receipt.Get("selection") != pkg.Version {
			continue
		}
		t.Run(component.ID, func(t *testing.T) {
			prepared, err := PreparePackage(artifact, pkg, t.TempDir())
			if err != nil {
				t.Fatal(err)
			}
			if err := NormalizePlatform(component, prepared.Managed, variant); err != nil {
				t.Fatal(err)
			}
			files, err := regularFiles(prepared.Managed)
			if err != nil {
				t.Fatal(err)
			}
			var entries []receipt.Entry
			for _, name := range files {
				if err := ValidateManagedPath(name); err != nil {
					t.Fatal(err)
				}
				digest, _ := archive.FileSHA256(filepath.Join(prepared.Managed, filepath.FromSlash(name)))
				entries = append(entries, receipt.Entry{Path: name, SHA256: digest})
			}
			if got := string(receipt.MarshalInventory(entries)); got != string(record.Inventory.Raw) {
				t.Fatalf("prepared payload differs from the installed inventory (%d vs %d lines)",
					len(entries), len(record.Inventory.Entries))
			}
		})
	}
}
