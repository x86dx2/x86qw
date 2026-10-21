package content

import (
	"errors"
	"fmt"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"strings"
	"time"

	"github.com/theupdateframework/go-tuf/v2/metadata"
	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
	"github.com/x86dx2/x86qw/go/internal/trust"
	"github.com/x86dx2/x86qw/go/internal/tufsign"
)

// Local TUF authentication of the checked-out catalog
// (manage.verify_local_tuf_catalog).

// localTUFFetcher serves a checked-out public TUF projection by basename,
// like manage._LocalTufFetcher.
type localTUFFetcher struct{ root string }

func (f localTUFFetcher) DownloadFile(raw string, maxLength int64, _ time.Duration) ([]byte, error) {
	parsed, err := url.Parse(raw)
	if err != nil || parsed.Scheme != "https" || parsed.RawQuery != "" || parsed.Fragment != "" {
		return nil, errors.New("the local TUF projection got an invalid URL")
	}
	name := path.Base(parsed.Path)
	if name == "" || name == "." || name == ".." || name == "/" {
		return nil, errors.New("the local TUF projection got an invalid path")
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
		return nil, fmt.Errorf("the local TUF projection has no %s", name)
	}
	payload, err := os.ReadFile(matches[0])
	if err != nil {
		return nil, err
	}
	if int64(len(payload)) > maxLength {
		return nil, fmt.Errorf("local TUF metadata exceeds the limit: %s", name)
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

// VerifyLocalTUFCatalog authenticates the checked-out catalog against the
// checked-out TUF projection and returns the status phrase verify prints.
// Expiry is checked one second before the chain's earliest expiry, so the
// check does not depend on the date; the monitor watches the live lease.
func (p *Project) VerifyLocalTUFCatalog() (string, error) {
	rootBytes, err := os.ReadFile(p.Path(TrustRootPath))
	if err == nil {
		err = trust.ValidateRootPolicy(rootBytes)
	}
	if err != nil {
		return "", fmt.Errorf("invalid embedded TUF root: %v", err)
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
		return "root validated; public metadata awaiting publication", nil
	}
	if !isDir(metadataDir) || !isDir(targetsDir) {
		return "", errors.New("incomplete public TUF projection")
	}
	names := map[string]bool{}
	for _, file := range metadataFiles {
		if isSymlink(file) {
			return "", fmt.Errorf("TUF metadata cannot be a link: %s", filepath.Base(file))
		}
		names[filepath.Base(file)] = true
	}
	if !names["timestamp.json"] || !names["1.root.json"] {
		return "", errors.New("the public TUF projection lacks root/timestamp/snapshot/targets")
	}
	projection := p.Path(TrustDir)
	generation, err := tufsign.ReadGeneration(projection)
	if err != nil {
		return "", fmt.Errorf("incomplete public TUF projection: %v", err)
	}
	stale, err := tufsign.Stale(projection)
	if err != nil {
		return "", err
	}
	if len(stale) > 0 {
		return "", fmt.Errorf("the TUF projection keeps old generations (%s); run x86qw-release prune", strings.Join(stale, ", "))
	}
	authenticated, err := trust.LoadCatalog(trust.Options{
		BaseURL: "https://local.x86qw.invalid/trust/",
		Root:    rootBytes,
		Fetcher: localTUFFetcher{root: projection},
		RefTime: generation.Expires.Add(-time.Second),
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
		return "", errors.New("the local catalog could not be read for the TUF comparison")
	}
	if remote, err = pyjson.Decode(authenticated.Raw, pyjson.LastWins); err != nil {
		return "", err
	}
	if !pyjson.Eq(local, remote) {
		return "", errors.New("the local catalog differs from the TUF target; sign it with x86qw-release sign")
	}
	status := fmt.Sprintf("generation %d authenticated locally, valid until %s", generation.Timestamp, generation.Expires.UTC().Format(time.DateOnly))
	if !time.Now().Before(generation.Expires) {
		status += " (expired: renew)"
	}
	return status, nil
}
