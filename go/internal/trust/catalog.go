package trust

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/theupdateframework/go-tuf/v2/metadata"
	"github.com/theupdateframework/go-tuf/v2/metadata/config"
	"github.com/theupdateframework/go-tuf/v2/metadata/fetcher"
	"github.com/theupdateframework/go-tuf/v2/metadata/updater"
)

const (
	// CatalogTarget is the only target the catalog role authorizes.
	CatalogTarget = "catalog/catalog.json"
	// DefaultBaseURL is the public TUF repository (metadata/ and targets/).
	DefaultBaseURL = "https://qw.x86.com.br/api/v1/trust/"
	// UserAgent is how the client identifies itself to the repository.
	UserAgent = "x86qw-go"

	catalogMaxBytes = 2 * 1024 * 1024
)

// Options configures one catalog refresh.
type Options struct {
	// BaseURL holds metadata/ and targets/. Defaults to DefaultBaseURL.
	BaseURL string
	// CacheDir persists trusted metadata between runs so rollback and
	// equivocation are detected. Empty disables the cache.
	CacheDir string
	// Root overrides the embedded trust anchor (tests only).
	Root []byte
	// Fetcher overrides the HTTP fetcher (tests only).
	Fetcher fetcher.Fetcher
	// RefTime replaces the clock that expiry is checked against. Only
	// x86qw-content verify sets it, so a checked-out chain does not fail
	// CI just because a week passed; the monitor watches the live expiry.
	RefTime time.Time
}

// RoleState is the verified version and expiry of a top-level role.
type RoleState struct {
	Version int64     `json:"version"`
	Expires time.Time `json:"expires"`
}

// Catalog is the authenticated catalog target and the chain that vouched for it.
type Catalog struct {
	Raw      []byte
	SHA256   string
	Packages int
	Roles    map[string]RoleState
}

// LoadCatalog refreshes the TUF chain and returns the authenticated catalog.
func LoadCatalog(opts Options) (*Catalog, error) {
	root := opts.Root
	if root == nil {
		root = RootJSON
	}
	if err := ValidateRootPolicy(root); err != nil {
		return nil, err
	}
	baseURL := opts.BaseURL
	if baseURL == "" {
		baseURL = DefaultBaseURL
	}
	if !strings.HasPrefix(baseURL, "https://") && !strings.HasPrefix(baseURL, "http://127.0.0.1") {
		return nil, errorf("URL de trust deve usar HTTPS: %s", baseURL)
	}
	baseURL = strings.TrimSuffix(baseURL, "/") + "/"

	cfg, err := config.New(baseURL+"metadata/", root)
	if err != nil {
		return nil, errorf("configuração TUF inválida: %v", err)
	}
	cfg.RemoteTargetsURL = baseURL + "targets/"
	// Same bounds as the Python client (x86qw_runtime/trust.py).
	cfg.RootMaxLength = 512 * 1024
	cfg.TimestampMaxLength = 64 * 1024
	cfg.SnapshotMaxLength = 2 * 1024 * 1024
	cfg.TargetsMaxLength = 2 * 1024 * 1024
	cfg.MaxRootRotations = 32
	cfg.MaxDelegations = 1
	cfg.PrefixTargetsWithHash = true

	var previousTimestamp []byte
	if opts.CacheDir == "" {
		cfg.DisableLocalCache = true
	} else {
		cfg.LocalMetadataDir = filepath.Join(opts.CacheDir, "metadata")
		cfg.LocalTargetsDir = filepath.Join(opts.CacheDir, "targets")
		previousTimestamp, _ = os.ReadFile(filepath.Join(cfg.LocalMetadataDir, "timestamp.json"))
	}
	base := opts.Fetcher
	if base == nil {
		df := fetcher.NewDefaultFetcher()
		df.SetHTTPUserAgent(UserAgent)
		base = df
	}
	observer := &observingFetcher{base: base}
	cfg.Fetcher = observer

	up, err := updater.New(cfg)
	if err != nil {
		return nil, errorf("falha de trust ao autenticar catálogo: %v", err)
	}
	if !opts.RefTime.IsZero() {
		up.UnsafeSetRefTime(opts.RefTime)
	}
	if err := up.Refresh(); err != nil {
		return nil, errorf("falha de trust ao autenticar catálogo: %v", err)
	}
	if err := checkEquivocation(previousTimestamp, observer.timestamp()); err != nil {
		return nil, err
	}
	info, err := up.GetTargetInfo(CatalogTarget)
	if err != nil {
		return nil, errorf("catálogo não foi autorizado por targets TUF: %v", err)
	}
	if info.Length > catalogMaxBytes {
		return nil, errorf("catálogo autenticado excede %d bytes", catalogMaxBytes)
	}
	targetPath := ""
	if opts.CacheDir != "" {
		targetPath = filepath.Join(cfg.LocalTargetsDir, "catalog.json")
	}
	_, payload, err := up.DownloadTarget(info, targetPath, "")
	if err != nil {
		return nil, errorf("falha de trust ao baixar catálogo: %v", err)
	}
	packages, err := checkCatalogIdentity(payload)
	if err != nil {
		return nil, err
	}

	trusted := up.GetTrustedMetadataSet()
	digest := sha256.Sum256(payload)
	return &Catalog{
		Raw:      payload,
		SHA256:   hex.EncodeToString(digest[:]),
		Packages: packages,
		Roles: map[string]RoleState{
			metadata.ROOT:      {trusted.Root.Signed.Version, trusted.Root.Signed.Expires},
			metadata.TIMESTAMP: {trusted.Timestamp.Signed.Version, trusted.Timestamp.Signed.Expires},
			metadata.SNAPSHOT:  {trusted.Snapshot.Signed.Version, trusted.Snapshot.Signed.Expires},
			metadata.TARGETS:   {trusted.Targets[metadata.TARGETS].Signed.Version, trusted.Targets[metadata.TARGETS].Signed.Expires},
		},
	}, nil
}

// checkEquivocation rejects a remote timestamp that reuses the cached
// version with different bytes. go-tuf silently keeps the cached copy in
// that case; a repository that does this is lying to someone.
func checkEquivocation(previous, observed []byte) error {
	if previous == nil || observed == nil {
		return nil
	}
	old, errOld := metadata.Timestamp().FromBytes(previous)
	cur, errCur := metadata.Timestamp().FromBytes(observed)
	if errOld != nil || errCur != nil {
		return nil
	}
	if old.Signed.Version == cur.Signed.Version && !bytes.Equal(previous, observed) {
		return errorf("equivocação TUF: timestamp diferente reutilizou a versão %d", cur.Signed.Version)
	}
	return nil
}

func checkCatalogIdentity(payload []byte) (int, error) {
	var catalog struct {
		Format   *int              `json:"format"`
		Project  string            `json:"project"`
		Packages []json.RawMessage `json:"packages"`
	}
	if err := json.Unmarshal(payload, &catalog); err != nil {
		return 0, errorf("catálogo autenticado não é JSON válido: %v", err)
	}
	if catalog.Format == nil || *catalog.Format != 1 || catalog.Project != "x86qw" || catalog.Packages == nil {
		return 0, errorf("catálogo autenticado usa identidade ou formato incompatível")
	}
	return len(catalog.Packages), nil
}

// observingFetcher records the remote timestamp bytes for the equivocation check.
type observingFetcher struct {
	base fetcher.Fetcher
	mu   sync.Mutex
	ts   []byte
}

func (f *observingFetcher) DownloadFile(url string, maxLength int64, timeout time.Duration) ([]byte, error) {
	data, err := f.base.DownloadFile(url, maxLength, timeout)
	if err == nil && strings.HasSuffix(url, "/timestamp.json") {
		f.mu.Lock()
		f.ts = data
		f.mu.Unlock()
	}
	return data, err
}

func (f *observingFetcher) timestamp() []byte {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.ts
}

// IsTrustError reports whether err came from this package.
func IsTrustError(err error) bool {
	var target *Error
	return errors.As(err, &target)
}
