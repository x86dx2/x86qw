package tufsign

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/x509"
	"encoding/pem"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/theupdateframework/go-tuf/v2/metadata"
	"github.com/x86dx2/x86qw/go/internal/trust"
)

// throwawayKeys writes fresh Ed25519 keys as <role>-<n>.pem and a root.json
// that authorizes them with the production policy, except for the timestamp
// threshold.
func throwawayKeys(t *testing.T, timestampThreshold int) (keys, rootPath string) {
	t.Helper()
	dir := t.TempDir()
	keys = filepath.Join(dir, "keys")
	if err := os.Mkdir(keys, 0o700); err != nil {
		t.Fatal(err)
	}
	root := metadata.Root(time.Now().Add(365 * 24 * time.Hour))
	root.Signed.ConsistentSnapshot = true
	policy := map[string][2]int{metadata.ROOT: {3, 2}, metadata.TARGETS: {3, 2}, metadata.SNAPSHOT: {2, 1}, metadata.TIMESTAMP: {2, timestampThreshold}}
	for role, counts := range policy {
		for index := 1; index <= counts[0]; index++ {
			public, private, err := ed25519.GenerateKey(rand.Reader)
			if err != nil {
				t.Fatal(err)
			}
			der, err := x509.MarshalPKCS8PrivateKey(private)
			if err != nil {
				t.Fatal(err)
			}
			path := filepath.Join(keys, fmt.Sprintf("%s-%d.pem", role, index))
			if err := os.WriteFile(path, pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: der}), 0o600); err != nil {
				t.Fatal(err)
			}
			key, err := metadata.KeyFromPublicKey(public)
			if err != nil {
				t.Fatal(err)
			}
			if err := root.Signed.AddKey(key, role); err != nil {
				t.Fatal(err)
			}
		}
		root.Signed.Roles[role].Threshold = counts[1]
	}
	signers, err := RoleSigners(keys, &root.Signed, metadata.ROOT)
	if err != nil {
		t.Fatal(err)
	}
	payload, err := sign(root, signers)
	if err != nil {
		t.Fatal(err)
	}
	rootPath = filepath.Join(dir, "root.json")
	if err := os.WriteFile(rootPath, payload, 0o644); err != nil {
		t.Fatal(err)
	}
	return keys, rootPath
}

// TestSignAndRenewAVerifiableRepository signs a throwaway repository for the
// checked-out catalog and authenticates it with the same client code the CLI
// uses: with the real keys on the maintainer's machine, with throwaway keys
// elsewhere, so CI runs what the weekly renewal runs.
func TestSignAndRenewAVerifiableRepository(t *testing.T) {
	repo, _ := filepath.Abs(filepath.Join("..", "..", ".."))
	keys := filepath.Join(repo, ".tuf-production", "keys")
	rootPath := filepath.Join(repo, "maintenance", "trust", "root.json")
	if _, err := os.Stat(keys); err != nil {
		keys, rootPath = throwawayKeys(t, 1)
	}
	rootBytes, err := os.ReadFile(rootPath)
	if err != nil {
		t.Fatal(err)
	}
	out := filepath.Join(t.TempDir(), "repo")
	catalogPath := filepath.Join(repo, "site", "public", "api", "v1", "catalog.json")
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
	catalog, err := trust.LoadCatalog(trust.Options{BaseURL: "https://local.invalid/", Root: rootBytes, Fetcher: fetch})
	if err != nil {
		t.Fatalf("the repository signed in Go did not authenticate: %v", err)
	}
	if catalog.Roles["timestamp"].Version != 999 {
		t.Fatalf("version: %+v", catalog.Roles)
	}
	renewed, err := RenewTimestamp(files["metadata/timestamp.json"], filepath.Join(keys, "timestamp-1.pem"), rootPath, 30*24*time.Hour, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	files["metadata/timestamp.json"] = renewed
	catalog, err = trust.LoadCatalog(trust.Options{BaseURL: "https://local.invalid/", Root: rootBytes, Fetcher: fetch})
	if err != nil || catalog.Roles["timestamp"].Version != 1000 {
		t.Fatalf("renewed timestamp: %v %+v", err, catalog)
	}
	if _, err := RenewTimestamp(renewed, filepath.Join(keys, "snapshot-1.pem"), rootPath, time.Hour, time.Now()); err == nil {
		t.Fatal("snapshot key accepted for timestamp")
	}
}

// TestRenewTimestampRefusesWhatTheRootRejects: renewed bytes the root does not
// accept (here, one online signature under a threshold of two) stop the
// renewal before the deploy, where every client would reject them.
func TestRenewTimestampRefusesWhatTheRootRejects(t *testing.T) {
	keys, rootPath := throwawayKeys(t, 2)
	catalogPath := filepath.Join(t.TempDir(), "catalog.json")
	if err := os.WriteFile(catalogPath, []byte("{}\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	out := filepath.Join(t.TempDir(), "repo")
	if err := Generate(keys, rootPath, catalogPath, out, 1, time.Now()); err != nil {
		t.Fatal(err)
	}
	current, err := os.ReadFile(filepath.Join(out, "metadata", "timestamp.json"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := RenewTimestamp(current, filepath.Join(keys, "timestamp-1.pem"), rootPath, time.Hour, time.Now()); err == nil {
		t.Fatal("renewed a timestamp the root does not accept")
	}
}
