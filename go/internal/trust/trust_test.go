package trust

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/theupdateframework/go-tuf/v2/metadata"
	"github.com/x86dx2/x86qw/go/internal/tufsign"
)

func repoPath(t *testing.T, parts ...string) string {
	t.Helper()
	return filepath.Join(append([]string{"..", "..", ".."}, parts...)...)
}

func TestEmbeddedRootMatchesMaintenanceRoot(t *testing.T) {
	want, err := os.ReadFile(repoPath(t, "maintenance", "trust", "root.json"))
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(RootJSON, want) {
		t.Fatal("go/internal/trust/root.json diverge de maintenance/trust/root.json")
	}
	if err := ValidateRootPolicy(RootJSON); err != nil {
		t.Fatalf("root de produção rejeitada: %v", err)
	}
}

func TestRootPolicyRejectsDeviations(t *testing.T) {
	repo := newTestRepo(t)
	if err := ValidateRootPolicy(repo.root); err != nil {
		t.Fatalf("root conforme rejeitada: %v", err)
	}
	cases := map[string]func(*metadata.RootType){
		"no consistent snapshots":  func(r *metadata.RootType) { r.ConsistentSnapshot = false },
		"weaker targets threshold": func(r *metadata.RootType) { r.Roles[metadata.TARGETS].Threshold = 1 },
		"shared key": func(r *metadata.RootType) {
			r.Roles[metadata.TIMESTAMP].KeyIDs[0] = r.Roles[metadata.SNAPSHOT].KeyIDs[0]
		},
		"extra role": func(r *metadata.RootType) {
			r.Roles["mirrors"] = &metadata.Role{KeyIDs: []string{}, Threshold: 1}
		},
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			if err := ValidateRootPolicy(repo.buildRoot(mutate)); err == nil {
				t.Fatal("root fora da política foi aceita")
			}
		})
	}
}

// TestParityWithCheckedOutProjection authenticates the same projection that
// `maintenance/manage.py verify` authenticates with the Python client.
func TestParityWithCheckedOutProjection(t *testing.T) {
	projection := repoPath(t, "site", "public", "api", "v1", "trust")
	// The checkout is checked at its own expiry, not today: CI must not
	// fail because a week passed. The monitor watches the live lease.
	generation, err := tufsign.ReadGeneration(projection)
	if err != nil {
		t.Fatal(err)
	}
	files := map[string][]byte{}
	for _, dir := range []string{"metadata", "targets"} {
		base := filepath.Join(projection, dir)
		err := filepath.WalkDir(base, func(path string, entry os.DirEntry, err error) error {
			if err != nil || entry.IsDir() {
				return err
			}
			data, err := os.ReadFile(path)
			if err != nil {
				return err
			}
			rel, _ := filepath.Rel(base, path)
			files[filepath.ToSlash(rel)] = data
			return nil
		})
		if err != nil {
			t.Fatal(err)
		}
	}
	catalog, err := LoadCatalog(Options{
		BaseURL: "https://local.x86qw.invalid/trust/",
		Fetcher: mapFetcher{
			files:          files,
			metadataPrefix: "https://local.x86qw.invalid/trust/metadata/",
			targetsPrefix:  "https://local.x86qw.invalid/trust/targets/",
		},
		RefTime: generation.Expires.Add(-time.Second),
	})
	if err != nil {
		t.Fatalf("projeção do checkout não autenticou: %v", err)
	}
	want, err := os.ReadFile(repoPath(t, "site", "public", "api", "v1", "catalog.json"))
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(catalog.Raw, want) {
		t.Fatal("catálogo autenticado diverge de site/public/api/v1/catalog.json")
	}
	if catalog.Packages == 0 {
		t.Fatal("catálogo autenticado sem pacotes")
	}
}

func TestLoadCatalogFromGeneratedRepo(t *testing.T) {
	repo := newTestRepo(t)
	repo.publish(publishSpec{version: 3})
	catalog, err := LoadCatalog(repo.options(t.TempDir()))
	if err != nil {
		t.Fatal(err)
	}
	if catalog.Packages != 2 || catalog.Roles[metadata.TIMESTAMP].Version != 3 {
		t.Fatalf("catálogo inesperado: %+v", catalog)
	}
}

func TestRejectsExpiredTimestamp(t *testing.T) {
	repo := newTestRepo(t)
	repo.publish(publishSpec{version: 3, timestampExpires: time.Now().Add(-time.Hour)})
	_, err := LoadCatalog(repo.options(""))
	if err == nil || !strings.Contains(err.Error(), "timestamp.json is expired") {
		t.Fatalf("timestamp vencido deveria falhar: %v", err)
	}
	if !IsTrustError(err) {
		t.Fatalf("erro deveria ser de trust: %T", err)
	}
}

// RefTime is the clock x86qw-content verify uses for a checked-out chain.
func TestRefTimeReplacesTheClock(t *testing.T) {
	repo := newTestRepo(t)
	expired := time.Now().Add(-time.Hour)
	repo.publish(publishSpec{version: 3, timestampExpires: expired})
	options := repo.options("")
	options.RefTime = expired.Add(-time.Second)
	if _, err := LoadCatalog(options); err != nil {
		t.Fatalf("antes de vencer o timestamp vale: %v", err)
	}
}

func TestRejectsTamperedTimestamp(t *testing.T) {
	repo := newTestRepo(t)
	repo.publish(publishSpec{version: 3})
	repo.files["timestamp.json"] = bytes.Replace(repo.files["timestamp.json"], []byte(`"version":3`), []byte(`"version":4`), 1)
	if _, err := LoadCatalog(repo.options("")); err == nil {
		t.Fatal("timestamp adulterado foi aceito")
	}
}

func TestRejectsRollback(t *testing.T) {
	repo := newTestRepo(t)
	cache := t.TempDir()
	repo.publish(publishSpec{version: 5})
	if _, err := LoadCatalog(repo.options(cache)); err != nil {
		t.Fatal(err)
	}
	repo.publish(publishSpec{version: 4})
	if _, err := LoadCatalog(repo.options(cache)); err == nil {
		t.Fatal("rollback de v5 para v4 foi aceito")
	}
}

func TestRejectsTimestampEquivocation(t *testing.T) {
	repo := newTestRepo(t)
	cache := t.TempDir()
	repo.publish(publishSpec{version: 5})
	if _, err := LoadCatalog(repo.options(cache)); err != nil {
		t.Fatal(err)
	}
	// Same timestamp version, different signed bytes.
	repo.publish(publishSpec{version: 5, timestampExpires: time.Now().Add(20 * 24 * time.Hour)})
	_, err := LoadCatalog(repo.options(cache))
	if err == nil || !strings.Contains(err.Error(), "equivocação") {
		t.Fatalf("equivocação deveria falhar: %v", err)
	}
}

func TestRejectsForeignCatalogIdentity(t *testing.T) {
	repo := newTestRepo(t)
	repo.publish(publishSpec{version: 3, catalog: []byte(`{"format":1,"project":"other","packages":[]}`)})
	if _, err := LoadCatalog(repo.options("")); err == nil {
		t.Fatal("catálogo de outro projeto foi aceito")
	}
}

func TestRejectsSwappedCatalogBytes(t *testing.T) {
	repo := newTestRepo(t)
	repo.publish(publishSpec{version: 3})
	for name := range repo.files {
		if strings.HasPrefix(name, "catalog/") {
			repo.files[name] = []byte(`{"format":1,"project":"x86qw","packages":[{"id":"evil"},{"id":"b"}]}`)
		}
	}
	if _, err := LoadCatalog(repo.options("")); err == nil {
		t.Fatal("catálogo com bytes trocados foi aceito")
	}
}

func TestRejectsPlainHTTP(t *testing.T) {
	if _, err := LoadCatalog(Options{BaseURL: "http://qw.x86.com.br/api/v1/trust/"}); err == nil {
		t.Fatal("URL HTTP foi aceita")
	}
}
