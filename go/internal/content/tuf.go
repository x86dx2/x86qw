package content

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/theupdateframework/go-tuf/v2/metadata"
	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
	"github.com/x86dx2/x86qw/go/internal/trust"
)

// Local TUF authentication of the checked-out catalog
// (manage.verify_local_tuf_catalog).

// localTUFFetcher serves a checked-out public TUF projection by basename,
// like manage._LocalTufFetcher.
type localTUFFetcher struct{ root string }

func (f localTUFFetcher) DownloadFile(raw string, maxLength int64, _ time.Duration) ([]byte, error) {
	parsed, err := url.Parse(raw)
	if err != nil || parsed.Scheme != "https" || parsed.RawQuery != "" || parsed.Fragment != "" {
		return nil, errors.New("projeção local de TUF recebeu URL inválida")
	}
	name := path.Base(parsed.Path)
	if name == "" || name == "." || name == ".." || name == "/" {
		return nil, errors.New("projeção local de TUF recebeu caminho inválido")
	}
	var matches []string
	filepath.WalkDir(f.root, func(candidate string, entry os.DirEntry, err error) error {
		if err == nil && entry.Name() == name && entry.Type().IsRegular() {
			matches = append(matches, candidate)
		}
		return nil
	})
	if len(matches) == 0 {
		// The updater probes the next root version until it gets a 404.
		return nil, &metadata.ErrDownloadHTTP{StatusCode: 404, URL: raw}
	}
	if len(matches) > 1 {
		return nil, fmt.Errorf("projeção local de TUF não possui %s", name)
	}
	payload, err := os.ReadFile(matches[0])
	if err != nil {
		return nil, err
	}
	if int64(len(payload)) > maxLength {
		return nil, fmt.Errorf("metadata TUF local excede o limite: %s", name)
	}
	return payload, nil
}

func jsonFiles(dir string) []string {
	var files []string
	filepath.WalkDir(dir, func(candidate string, entry os.DirEntry, err error) error {
		if err == nil && candidate != dir && strings.HasSuffix(entry.Name(), ".json") {
			files = append(files, candidate)
		}
		return nil
	})
	return files
}

// VerifyLocalTUFCatalog mirrors verify_local_tuf_catalog and returns the
// status phrase printed by verify.
func (p *Project) VerifyLocalTUFCatalog() (string, error) {
	rootBytes, err := os.ReadFile(p.Path(TrustRootPath))
	if err == nil {
		err = trust.ValidateRootPolicy(rootBytes)
	}
	if err != nil {
		return "", fmt.Errorf("root TUF incorporada inválida: %v", err)
	}
	metadataDir, targetsDir := p.Path(TrustMetadataDir), p.Path(TrustTargetsDir)
	var metadataFiles, targetFiles []string
	if isDir(metadataDir) {
		metadataFiles = jsonFiles(metadataDir)
	}
	if isDir(targetsDir) {
		targetFiles = jsonFiles(targetsDir)
	}
	if len(metadataFiles) == 0 && len(targetFiles) == 0 {
		return "root validada; metadata pública aguardando publicação", nil
	}
	if !isDir(metadataDir) || !isDir(targetsDir) {
		return "", errors.New("projeção pública de TUF incompleta")
	}
	names := map[string]bool{}
	snapshot, targets := false, false
	for _, file := range metadataFiles {
		name := filepath.Base(file)
		if !isSymlink(file) {
			names[name] = true
		}
		snapshot = snapshot || strings.HasSuffix(name, ".snapshot.json")
		targets = targets || strings.HasSuffix(name, ".targets.json")
	}
	if !names["timestamp.json"] || !names["1.root.json"] || !snapshot || !targets {
		return "", errors.New("projeção pública de TUF não contém root/timestamp/snapshot/targets")
	}
	authenticated, err := trust.LoadCatalog(trust.Options{
		BaseURL: "https://local.x86qw.invalid/trust/",
		Root:    rootBytes,
		Fetcher: localTUFFetcher{root: p.Path("site/public/api/v1/trust")},
	})
	if err != nil {
		return "", err
	}
	localBytes, err := os.ReadFile(p.Path(CatalogPath))
	var local, remote any
	if err == nil {
		local, err = pyjson.Decode(localBytes, pyjson.LastWins)
	}
	if err != nil {
		return "", errors.New("catálogo local não pôde ser lido para comparação TUF")
	}
	if remote, err = pyjson.Decode(authenticated.Raw, pyjson.LastWins); err != nil {
		return "", err
	}
	if pyjson.Eq(local, remote) {
		return "autenticada localmente", nil
	}
	// The checkout may preserve a historical catalog while the deployment
	// target is newer; accept it only when a signed targets role pins it.
	root, err := metadata.Root().FromBytes(rootBytes)
	if err != nil {
		return "", errors.New("catálogo fonte não pôde ser autenticado contra targets históricos")
	}
	digest := sha256.Sum256(localBytes)
	localDigest := hex.EncodeToString(digest[:])
	candidates, _ := filepath.Glob(filepath.Join(metadataDir, "*.targets.json"))
	sort.Strings(candidates)
	for _, candidate := range candidates {
		raw, err := os.ReadFile(candidate)
		if err != nil {
			return "", errors.New("catálogo fonte não pôde ser autenticado contra targets históricos")
		}
		signed, err := metadata.Targets().FromBytes(raw)
		if err != nil {
			return "", errors.New("catálogo fonte não pôde ser autenticado contra targets históricos")
		}
		if root.VerifyDelegate(metadata.TARGETS, signed) != nil {
			continue
		}
		target, ok := signed.Signed.Targets[trust.CatalogTarget]
		if ok && target.Length == int64(len(localBytes)) && hex.EncodeToString(target.Hashes["sha256"]) == localDigest {
			return "deployment autenticado; catálogo fonte autenticado por targets histórico " + filepath.Base(candidate), nil
		}
	}
	return "", errors.New("catálogo local diverge do target TUF e não possui targets fonte autenticados")
}
