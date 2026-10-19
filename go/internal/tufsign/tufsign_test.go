package tufsign

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/x86dx2/x86qw/go/internal/trust"
)

// TestProductionKeysSignAVerifiableRepository runs only on the maintainer's
// machine (the offline keys never reach CI): it signs a throwaway repository
// for the checked-out catalog with the real keys and authenticates it with
// the same client code the CLI uses.
func TestProductionKeysSignAVerifiableRepository(t *testing.T) {
	repo, _ := filepath.Abs(filepath.Join("..", "..", ".."))
	keys := filepath.Join(repo, ".tuf-production", "keys")
	if _, err := os.Stat(keys); err != nil {
		t.Skip("chaves offline indisponíveis")
	}
	out := filepath.Join(t.TempDir(), "repo")
	catalogPath := filepath.Join(repo, "site", "public", "api", "v1", "catalog.json")
	rootPath := filepath.Join(repo, "maintenance", "trust", "root.json")
	if err := Generate(keys, rootPath, catalogPath, out, 999, time.Now()); err != nil {
		t.Fatal(err)
	}
	files := map[string][]byte{}
	filepath.WalkDir(out, func(path string, entry os.DirEntry, err error) error {
		if err == nil && !entry.IsDir() {
			rel, _ := filepath.Rel(out, path)
			rel = filepath.ToSlash(rel)
			data, _ := os.ReadFile(path)
			if len(rel) > 9 && rel[:9] == "metadata/" {
				files["metadata/"+filepath.Base(rel)] = data
			} else {
				files[rel] = data
			}
		}
		return nil
	})
	fetch := &localFetcher{files: files}
	catalog, err := trust.LoadCatalog(trust.Options{BaseURL: "https://local.invalid/", Fetcher: fetch})
	if err != nil {
		t.Fatalf("repositório assinado em Go não autenticou: %v", err)
	}
	if catalog.Roles["timestamp"].Version != 999 {
		t.Fatalf("versão: %+v", catalog.Roles)
	}
	renewed, err := RenewTimestamp(files["metadata/timestamp.json"], filepath.Join(keys, "timestamp-1.pem"), rootPath, 30*24*time.Hour, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	files["metadata/timestamp.json"] = renewed
	catalog, err = trust.LoadCatalog(trust.Options{BaseURL: "https://local.invalid/", Fetcher: fetch})
	if err != nil || catalog.Roles["timestamp"].Version != 1000 {
		t.Fatalf("timestamp renovado: %v %+v", err, catalog)
	}
	if _, err := RenewTimestamp(renewed, filepath.Join(keys, "snapshot-1.pem"), rootPath, time.Hour, time.Now()); err == nil {
		t.Fatal("chave snapshot aceita para timestamp")
	}
}
