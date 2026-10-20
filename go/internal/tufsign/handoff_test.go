package tufsign

import (
	"crypto"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/x509"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/sigstore/sigstore/pkg/signature"
	"github.com/theupdateframework/go-tuf/v2/metadata"
)

// throwawayRepository builds a policy-compliant root with fresh keys and a
// version-1 repository for a minimal catalog. It returns the workspace and
// the snapshot and timestamp key ids.
func throwawayRepository(t *testing.T) (string, map[string][]string) {
	t.Helper()
	workspace := t.TempDir()
	keyDir := filepath.Join(workspace, "keys")
	os.MkdirAll(keyDir, 0o700)
	root := metadata.Root(time.Now().UTC().Add(365 * 24 * time.Hour).Truncate(time.Second))
	root.Signed.ConsistentSnapshot = true
	ids := map[string][]string{}
	var rootSigners []signature.Signer
	for role, count := range map[string]int{"root": 3, "targets": 3, "snapshot": 2, "timestamp": 2} {
		for index := 1; index <= count; index++ {
			public, private, _ := ed25519.GenerateKey(rand.Reader)
			der, _ := x509.MarshalPKCS8PrivateKey(private)
			path := filepath.Join(keyDir, fmt.Sprintf("%s-%d.pem", role, index))
			os.WriteFile(path, pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: der}), 0o600)
			key, _ := metadata.KeyFromPublicKey(public)
			if err := root.Signed.AddKey(key, role); err != nil {
				t.Fatal(err)
			}
			id, _ := key.ID()
			ids[role] = append(ids[role], id)
			if role == "root" {
				signer, _ := signature.LoadSigner(private, crypto.Hash(0))
				rootSigners = append(rootSigners, signer)
			}
		}
	}
	root.Signed.Roles["root"].Threshold = 2
	root.Signed.Roles["targets"].Threshold = 2
	rootBytes, err := sign(root, rootSigners)
	if err != nil {
		t.Fatal(err)
	}
	os.WriteFile(filepath.Join(workspace, "root.json"), rootBytes, 0o644)
	os.WriteFile(filepath.Join(workspace, "catalog.json"), []byte(`{"format":1,"project":"x86qw","packages":[]}`), 0o644)
	if err := Generate(keyDir, filepath.Join(workspace, "root.json"), filepath.Join(workspace, "catalog.json"), filepath.Join(workspace, "repo"), 1, time.Now()); err != nil {
		t.Fatal(err)
	}
	return workspace, ids
}

func TestRenewalHandoffs(t *testing.T) {
	w, ids := throwawayRepository(t)
	at := func(name string) string { return filepath.Join(w, name) }
	if err := VerifyRepository(at("repo"), at("root.json"), at("catalog.json")); err != nil {
		t.Fatalf("source repository: %v", err)
	}
	timestamp := RoleKey{Path: at("keys/timestamp-1.pem"), ID: ids["timestamp"][0]}
	snapshot := RoleKey{Path: at("keys/snapshot-1.pem"), ID: ids["snapshot"][0]}

	if _, err := RenewTimestampHandoff(TimestampHandoff{Repository: at("repo"), Root: at("root.json"), Catalog: at("catalog.json"),
		Output: at("ts"), Report: at("ts.json"), Key: timestamp, LeaseHours: 168, Now: time.Now()}); err != nil {
		t.Fatal(err)
	}
	if err := VerifyRepository(at("ts"), at("root.json"), at("catalog.json")); err != nil {
		t.Fatalf("renewed timestamp: %v", err)
	}
	var report map[string]any
	payload, _ := os.ReadFile(at("ts.json"))
	if err := json.Unmarshal(payload, &report); err != nil || report["status"] != "timestamp-renewed" || report["published"] != false {
		t.Fatalf("report: %v %s", err, payload)
	}

	if _, err := RenewSnapshotHandoff(SnapshotHandoff{Repository: at("ts"), Root: at("root.json"), Catalog: at("catalog.json"),
		Output: at("sn"), Report: at("sn.json"), Snapshot: snapshot, Timestamp: timestamp,
		SnapshotLeaseDays: 90, TimestampLeaseDays: 30, Now: time.Now()}); err != nil {
		t.Fatal(err)
	}
	if err := VerifyRepository(at("sn"), at("root.json"), at("catalog.json")); err != nil {
		t.Fatalf("renewed snapshot: %v", err)
	}
	if _, err := os.Stat(at("sn/metadata/2.snapshot.json")); err != nil {
		t.Fatal(err)
	}

	for name, h := range map[string]TimestampHandoff{
		"wrong role": {Key: RoleKey{Path: at("keys/snapshot-1.pem"), ID: ids["snapshot"][0]}},
		"wrong pem":  {Key: RoleKey{Path: at("keys/timestamp-2.pem"), ID: ids["timestamp"][0]}},
		"inside":     {Key: timestamp, Output: at("repo/x")},
		"exists":     {Key: timestamp, Output: at("ts")},
	} {
		h.Repository, h.Root, h.Catalog, h.LeaseHours, h.Now = at("repo"), at("root.json"), at("catalog.json"), 1, time.Now()
		if h.Output == "" {
			h.Output = at("refused")
		}
		h.Report = at("refused.json")
		if _, err := RenewTimestampHandoff(h); err == nil {
			t.Fatalf("%s: accepted", name)
		}
	}

	os.WriteFile(at("other.json"), []byte(`{"format":1,"project":"x86qw","packages":[1]}`), 0o644)
	if err := VerifyRepository(at("repo"), at("root.json"), at("other.json")); err == nil {
		t.Fatal("foreign catalog verified")
	}
	timestampPath := at("sn/metadata/timestamp.json")
	tampered, _ := os.ReadFile(timestampPath)
	os.WriteFile(timestampPath, []byte(strings.Replace(string(tampered), `"version":`, `"version":1`, 1)), 0o644)
	if err := VerifyRepository(at("sn"), at("root.json"), at("catalog.json")); err == nil {
		t.Fatal("tampered timestamp verified")
	}
}
