package trust

import (
	"crypto"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/sigstore/sigstore/pkg/signature"
	"github.com/theupdateframework/go-tuf/v2/metadata"
)

// testRepo builds a signed TUF repository in memory following the x86QW
// policy, so client behaviour can be exercised without real keys.
type testRepo struct {
	t       *testing.T
	signers map[string][]signature.Signer
	keys    map[string][]*metadata.Key
	root    []byte
	files   map[string][]byte
}

func newTestRepo(t *testing.T) *testRepo {
	t.Helper()
	repo := &testRepo{
		t:       t,
		signers: map[string][]signature.Signer{},
		keys:    map[string][]*metadata.Key{},
		files:   map[string][]byte{},
	}
	for name, policy := range rolePolicy {
		for i := 0; i < policy.keys; i++ {
			public, private, err := ed25519.GenerateKey(rand.Reader)
			if err != nil {
				t.Fatal(err)
			}
			key, err := metadata.KeyFromPublicKey(public)
			if err != nil {
				t.Fatal(err)
			}
			signer, err := signature.LoadSigner(private, crypto.Hash(0))
			if err != nil {
				t.Fatal(err)
			}
			repo.keys[name] = append(repo.keys[name], key)
			repo.signers[name] = append(repo.signers[name], signer)
		}
	}
	repo.root = repo.buildRoot(func(*metadata.RootType) {})
	repo.files["1.root.json"] = repo.root
	return repo
}

// buildRoot signs a policy-conformant root, after letting mutate tweak it.
func (r *testRepo) buildRoot(mutate func(*metadata.RootType)) []byte {
	r.t.Helper()
	root := metadata.Root(time.Now().Add(365 * 24 * time.Hour))
	root.Signed.ConsistentSnapshot = true
	for name, keys := range r.keys {
		for _, key := range keys {
			if err := root.Signed.AddKey(key, name); err != nil {
				r.t.Fatal(err)
			}
		}
		root.Signed.Roles[name].Threshold = rolePolicy[name].threshold
	}
	mutate(&root.Signed)
	return r.sign(root, metadata.ROOT)
}

func (r *testRepo) sign(meta interface {
	Sign(signature.Signer) (*metadata.Signature, error)
	ToBytes(bool) ([]byte, error)
}, role string) []byte {
	r.t.Helper()
	for _, signer := range r.signers[role][:rolePolicy[role].threshold] {
		if _, err := meta.Sign(signer); err != nil {
			r.t.Fatal(err)
		}
	}
	data, err := meta.ToBytes(false)
	if err != nil {
		r.t.Fatal(err)
	}
	return data
}

type publishSpec struct {
	version          int64
	timestampVersion int64
	timestampExpires time.Time
	catalog          []byte
}

// publish writes targets/snapshot (version) and timestamp.json.
func (r *testRepo) publish(spec publishSpec) {
	r.t.Helper()
	if spec.timestampVersion == 0 {
		spec.timestampVersion = spec.version
	}
	if spec.timestampExpires.IsZero() {
		spec.timestampExpires = time.Now().Add(30 * 24 * time.Hour)
	}
	if spec.catalog == nil {
		spec.catalog = []byte(`{"format":1,"project":"x86qw","packages":[{"id":"a"},{"id":"b"}]}`)
	}
	far := time.Now().Add(90 * 24 * time.Hour)

	targets := metadata.Targets(far)
	targets.Signed.Version = spec.version
	file, err := metadata.TargetFile().FromBytes(CatalogTarget, spec.catalog, "sha256")
	if err != nil {
		r.t.Fatal(err)
	}
	targets.Signed.Targets[CatalogTarget] = file
	targetsBytes := r.sign(targets, metadata.TARGETS)

	snapshot := metadata.Snapshot(far)
	snapshot.Signed.Version = spec.version
	snapshot.Signed.Meta["targets.json"] = metaFile(spec.version, targetsBytes)
	snapshotBytes := r.sign(snapshot, metadata.SNAPSHOT)

	timestamp := metadata.Timestamp(spec.timestampExpires)
	timestamp.Signed.Version = spec.timestampVersion
	timestamp.Signed.Meta["snapshot.json"] = metaFile(spec.version, snapshotBytes)
	timestampBytes := r.sign(timestamp, metadata.TIMESTAMP)

	digest := sha256.Sum256(spec.catalog)
	r.files[fmt.Sprintf("%d.targets.json", spec.version)] = targetsBytes
	r.files[fmt.Sprintf("%d.snapshot.json", spec.version)] = snapshotBytes
	r.files["timestamp.json"] = timestampBytes
	r.files["catalog/"+hex.EncodeToString(digest[:])+".catalog.json"] = spec.catalog
}

func metaFile(version int64, data []byte) *metadata.MetaFiles {
	digest := sha256.Sum256(data)
	file := metadata.MetaFile(version)
	file.Length = int64(len(data))
	file.Hashes = metadata.Hashes{"sha256": digest[:]}
	return file
}

func (r *testRepo) options(cacheDir string) Options {
	return Options{
		BaseURL:  "https://repo.test/trust/",
		CacheDir: cacheDir,
		Root:     r.root,
		Fetcher:  mapFetcher{files: r.files, metadataPrefix: "https://repo.test/trust/metadata/", targetsPrefix: "https://repo.test/trust/targets/"},
	}
}

// mapFetcher serves a repository from memory and 404s everything else.
type mapFetcher struct {
	files          map[string][]byte
	metadataPrefix string
	targetsPrefix  string
}

func (f mapFetcher) DownloadFile(url string, maxLength int64, _ time.Duration) ([]byte, error) {
	name, ok := strings.CutPrefix(url, f.metadataPrefix)
	if !ok {
		name, ok = strings.CutPrefix(url, f.targetsPrefix)
	}
	data, found := f.files[name]
	if !ok || !found {
		return nil, &metadata.ErrDownloadHTTP{StatusCode: 404, URL: url}
	}
	if int64(len(data)) > maxLength {
		return nil, &metadata.ErrDownloadLengthMismatch{Msg: "too large"}
	}
	return data, nil
}
