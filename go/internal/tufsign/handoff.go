package tufsign

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"time"

	"github.com/sigstore/sigstore/pkg/signature"
	"github.com/theupdateframework/go-tuf/v2/metadata"

	"github.com/x86dx2/x86qw/go/internal/pyjson"
	"github.com/x86dx2/x86qw/go/internal/trust"
)

// Un-published renewal handoffs (ports of tuf_timestamp_renewal.py and
// tuf_snapshot_renewal.py): a protected workflow renews the online roles of
// an existing repository into a new directory plus a JSON report, and a
// separate ceremony verifies and publishes them. Only the role keys being
// renewed are ever loaded.

const handoffMaxFile = 2 << 20

// RoleExpiryDays mirrors generate_trust_metadata.ROLE_EXPIRY_DAYS.
var RoleExpiryDays = map[string]int{"root": 365, "targets": 90, "snapshot": 90, "timestamp": 30}

// RoleKey is a private key file and the root key id it must match.
type RoleKey struct {
	Path string
	ID   string
}

// TimestampHandoff renews metadata/timestamp.json only.
type TimestampHandoff struct {
	Repository, Root, Catalog, Output, Report string
	Key                                       RoleKey
	LeaseHours                                int
	Now                                       time.Time
}

// SnapshotHandoff adds metadata/<N+1>.snapshot.json and rewrites the timestamp.
type SnapshotHandoff struct {
	Repository, Root, Catalog, Output, Report string
	Snapshot, Timestamp                       RoleKey
	SnapshotLeaseDays, TimestampLeaseDays     int
	Now                                       time.Time
}

func regularFile(path, label string, maximum int64) ([]byte, error) {
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() {
		return nil, fmt.Errorf("%s ausente ou inseguro: %s", label, path)
	}
	payload, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("%s não pôde ser lido: %s", label, path)
	}
	if len(payload) == 0 || int64(len(payload)) > maximum {
		return nil, fmt.Errorf("%s excede o limite: %s", label, path)
	}
	return payload, nil
}

// treeFiles maps slash-separated relative paths to files, refusing
// symlinks, special files and oversized files.
func treeFiles(root string) (map[string]string, error) {
	if info, err := os.Lstat(root); err != nil || !info.IsDir() {
		return nil, fmt.Errorf("repositório TUF ausente ou inseguro: %s", root)
	}
	files := map[string]string{}
	err := filepath.WalkDir(root, func(path string, entry fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if path == root {
			return nil
		}
		switch {
		case entry.Type()&fs.ModeSymlink != 0:
			return fmt.Errorf("repositório TUF contém symlink: %s", path)
		case entry.Type().IsRegular():
			info, err := entry.Info()
			if err != nil {
				return err
			}
			if info.Size() > handoffMaxFile {
				return fmt.Errorf("arquivo TUF excede o limite: %s", path)
			}
			relative, _ := filepath.Rel(root, path)
			files[filepath.ToSlash(relative)] = path
		case !entry.IsDir():
			return fmt.Errorf("repositório TUF contém tipo especial: %s", path)
		}
		return nil
	})
	return files, err
}

func readRoot(path string) ([]byte, *metadata.Metadata[metadata.RootType], error) {
	payload, err := regularFile(path, "root TUF", 512*1024)
	if err != nil {
		return nil, nil, err
	}
	if err := trust.ValidateRootPolicy(payload); err != nil {
		return nil, nil, fmt.Errorf("root TUF inválida: %v", err)
	}
	root, err := metadata.Root().FromBytes(payload)
	if err != nil {
		return nil, nil, fmt.Errorf("root TUF inválida: %v", err)
	}
	return payload, root, nil
}

// targetIdentity proves the repository's single catalog target is catalog.
func targetIdentity(repository, catalog string) (map[string]any, error) {
	catalogBytes, err := regularFile(catalog, "catálogo", handoffMaxFile)
	if err != nil {
		return nil, err
	}
	targetRoot := filepath.Join(repository, "targets")
	var candidates []string
	filepath.WalkDir(targetRoot, func(path string, entry fs.DirEntry, err error) error {
		if err == nil && entry.Type().IsRegular() && strings.HasSuffix(entry.Name(), ".catalog.json") {
			candidates = append(candidates, path)
		}
		return nil
	})
	if len(candidates) != 1 {
		return nil, errors.New("repositório TUF precisa conter exatamente um target catalog")
	}
	targetBytes, err := regularFile(candidates[0], "target catalog", handoffMaxFile)
	if err != nil {
		return nil, err
	}
	if !bytes.Equal(targetBytes, catalogBytes) {
		return nil, errors.New("target TUF corrente diverge do catálogo fornecido")
	}
	relative, _ := filepath.Rel(targetRoot, candidates[0])
	return map[string]any{"path": filepath.ToSlash(relative), "size": len(targetBytes), "sha256": sha256Hex(targetBytes)}, nil
}

func sha256Hex(payload []byte) string {
	digest := sha256.Sum256(payload)
	return hex.EncodeToString(digest[:])
}

func loadRoleSigner(key RoleKey, root *metadata.RootType, role string) (signature.Signer, error) {
	authorized := false
	if declared, ok := root.Roles[role]; ok {
		for _, id := range declared.KeyIDs {
			authorized = authorized || id == key.ID
		}
	}
	if !authorized {
		return nil, fmt.Errorf("a chave fornecida não pertence à role %s; root/targets não são aceitas", role)
	}
	if _, ok := root.Keys[key.ID]; !ok {
		return nil, fmt.Errorf("key id %s não existe na root incorporada", role)
	}
	if _, err := regularFile(key.Path, "chave privada "+role, 16*1024); err != nil {
		return nil, err
	}
	if info, err := os.Stat(key.Path); err != nil || (runtime.GOOS != "windows" && info.Mode().Perm()&0o077 != 0) {
		return nil, fmt.Errorf("chave privada %s precisa ser 0600", role)
	}
	signer, id, err := LoadKey(key.Path)
	if err == nil && id != key.ID {
		err = fmt.Errorf("chave privada não corresponde à pública: %s", key.Path)
	}
	if err != nil {
		return nil, fmt.Errorf("chave privada %s não corresponde ao key id: %v", role, err)
	}
	return signer, nil
}

// resolve is Path.resolve(strict=False): symlinks of the existing prefix
// are resolved and the missing remainder is appended.
func resolve(path string) string {
	absolute, err := filepath.Abs(path)
	if err != nil {
		return filepath.Clean(path)
	}
	existing, rest := absolute, ""
	for {
		if resolved, err := filepath.EvalSymlinks(existing); err == nil {
			return filepath.Join(resolved, rest)
		}
		parent := filepath.Dir(existing)
		if parent == existing {
			return absolute
		}
		rest = filepath.Join(filepath.Base(existing), rest)
		existing = parent
	}
}

func inside(path, parent string) bool {
	relative, err := filepath.Rel(resolve(parent), resolve(path))
	return err == nil && relative != ".." && !strings.HasPrefix(relative, ".."+string(filepath.Separator))
}

func exists(path string) bool {
	_, err := os.Lstat(path)
	return err == nil
}

func checkDestinations(repository, output, report string) error {
	switch {
	case inside(output, repository):
		return errors.New("destino de saída não pode ficar dentro do repositório-fonte")
	case inside(report, repository):
		return errors.New("relatório não pode ficar dentro do repositório-fonte")
	case inside(report, output):
		return errors.New("relatório não pode ficar dentro do destino TUF")
	case exists(output):
		return fmt.Errorf("destino TUF já existe: %s", output)
	case exists(report):
		return fmt.Errorf("relatório já existe: %s", report)
	}
	return nil
}

// extendExpiry is now+lease at second precision, but always past current.
func extendExpiry(current, now time.Time, lease time.Duration) time.Time {
	expires := now.Add(lease).UTC().Truncate(time.Second)
	if !expires.After(current) {
		expires = current.UTC().Truncate(time.Second).Add(time.Second)
	}
	return expires
}

// isoUTC is datetime.isoformat() in UTC with a Z suffix.
func isoUTC(value time.Time) string {
	value = value.UTC()
	if value.Nanosecond()/1000 == 0 {
		return value.Format("2006-01-02T15:04:05Z")
	}
	return value.Truncate(time.Microsecond).Format("2006-01-02T15:04:05.000000Z")
}

func copyTree(files map[string]string, destination string) error {
	if err := os.Mkdir(destination, 0o700); err != nil {
		return err
	}
	for relative, source := range files {
		payload, err := os.ReadFile(source)
		if err != nil {
			return err
		}
		target := filepath.Join(destination, filepath.FromSlash(relative))
		if err := os.MkdirAll(filepath.Dir(target), 0o700); err != nil {
			return err
		}
		if err := os.WriteFile(target, payload, 0o644); err != nil {
			return err
		}
		if err := os.Chmod(target, 0o644); err != nil {
			return err
		}
	}
	return nil
}

// treeDiff lists the paths added to and changed in renewed against source.
func treeDiff(source, renewed map[string]string) (added, changed []string, err error) {
	for relative, path := range renewed {
		original, ok := source[relative]
		if !ok {
			added = append(added, relative)
			continue
		}
		before, err := os.ReadFile(original)
		if err != nil {
			return nil, nil, err
		}
		after, err := os.ReadFile(path)
		if err != nil {
			return nil, nil, err
		}
		if !bytes.Equal(before, after) {
			changed = append(changed, relative)
		}
	}
	for relative := range source {
		if _, ok := renewed[relative]; !ok {
			changed = append(changed, relative)
		}
	}
	sort.Strings(added)
	sort.Strings(changed)
	return added, changed, nil
}

// dirFetcher serves a repository directory to the TUF updater.
type dirFetcher struct{ root string }

const localTrustURL = "https://local.x86qw.invalid/trust/"

func (f dirFetcher) DownloadFile(url string, maxLength int64, _ time.Duration) ([]byte, error) {
	relative, ok := strings.CutPrefix(url, localTrustURL)
	if !ok || strings.Contains(relative, "..") {
		return nil, &metadata.ErrDownloadHTTP{StatusCode: 404, URL: url}
	}
	path := filepath.Join(f.root, filepath.FromSlash(relative))
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() {
		return nil, &metadata.ErrDownloadHTTP{StatusCode: 404, URL: url}
	}
	if info.Size() > maxLength {
		return nil, &metadata.ErrDownloadLengthMismatch{Msg: "arquivo excede o limite: " + relative}
	}
	return os.ReadFile(path)
}

// VerifyRepository authenticates catalog through repository's metadata from
// the root anchor, like publish_tuf_metadata.stage_tuf_metadata.
func VerifyRepository(repository, rootPath, catalogPath string) error {
	metadataFiles, err := treeFiles(filepath.Join(repository, "metadata"))
	if err != nil {
		return err
	}
	if _, err := treeFiles(filepath.Join(repository, "targets")); err != nil {
		return err
	}
	rootBytes, err := os.ReadFile(rootPath)
	if err != nil {
		return err
	}
	if err := trust.ValidateRootPolicy(rootBytes); err != nil {
		return fmt.Errorf("root TUF inválida: %v", err)
	}
	anchored := false
	for relative, path := range metadataFiles {
		version, found := strings.CutSuffix(relative, ".root.json")
		if !found || version == "" || strings.Trim(version, "0123456789") != "" || strings.Trim(version, "0") == "" {
			continue
		}
		payload, err := os.ReadFile(path)
		if err == nil && bytes.Equal(payload, rootBytes) {
			anchored = true
		}
	}
	if !anchored {
		return errors.New("metadata TUF precisa incluir a root versionada correspondente à âncora")
	}
	catalog, err := os.ReadFile(catalogPath)
	if err != nil {
		return err
	}
	verified, err := trust.LoadCatalog(trust.Options{BaseURL: localTrustURL, Root: rootBytes, Fetcher: dirFetcher{root: repository}})
	if err != nil {
		return fmt.Errorf("metadata TUF não autentica o catálogo final: %v", err)
	}
	if !bytes.Equal(verified.Raw, catalog) {
		return errors.New("target TUF autenticado diverge do catálogo final")
	}
	return nil
}

func readTimestamp(repository string) (string, []byte, *metadata.Metadata[metadata.TimestampType], error) {
	path := filepath.Join(repository, "metadata", "timestamp.json")
	payload, err := regularFile(path, "timestamp TUF", handoffMaxFile)
	if err != nil {
		return "", nil, nil, err
	}
	current, err := metadata.Timestamp().FromBytes(payload)
	if err != nil {
		return "", nil, nil, fmt.Errorf("timestamp TUF inválido: %v", err)
	}
	return path, payload, current, nil
}

// stage copies source into a temporary sibling of output, applies write,
// checks the expected added/changed sets, authenticates the result and
// moves it to output.
func stage(sourceFiles map[string]string, root, catalog, output string, write func(staged string) error, check func(added, changed []string) error) error {
	parent := filepath.Dir(output)
	if err := os.MkdirAll(parent, 0o700); err != nil {
		return err
	}
	temporary, err := os.MkdirTemp(parent, "."+filepath.Base(output)+"-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(temporary)
	staged := filepath.Join(temporary, "repository")
	if err := copyTree(sourceFiles, staged); err != nil {
		return err
	}
	if err := write(staged); err != nil {
		return err
	}
	stagedFiles, err := treeFiles(staged)
	if err != nil {
		return err
	}
	added, changed, err := treeDiff(sourceFiles, stagedFiles)
	if err != nil {
		return err
	}
	if err := check(added, changed); err != nil {
		return err
	}
	if err := VerifyRepository(staged, root, catalog); err != nil {
		return err
	}
	return os.Rename(staged, output)
}

func replaceFile(path string, payload []byte) error {
	if err := os.Remove(path); err != nil {
		return err
	}
	return writeNew(path, payload)
}

func writeReport(path string, report map[string]any) (string, error) {
	document, err := pyjson.Dumps(report, 2)
	if err != nil {
		return "", err
	}
	if err := writeNew(path, []byte(document+"\n")); err != nil {
		return "", err
	}
	return pyjson.Dumps(report, -1)
}

// RenewTimestampHandoff writes a repository differing from the source only
// in metadata/timestamp.json, plus its report. It returns the report as
// Python's single-line json.dumps.
func RenewTimestampHandoff(h TimestampHandoff) (string, error) {
	if h.LeaseHours < 1 || h.LeaseHours > 8760 {
		return "", errors.New("lease_hours deve estar entre 1 e 8760")
	}
	if err := checkDestinations(h.Repository, h.Output, h.Report); err != nil {
		return "", err
	}
	sourceFiles, err := treeFiles(h.Repository)
	if err != nil {
		return "", err
	}
	rootBytes, root, err := readRoot(h.Root)
	if err != nil {
		return "", err
	}
	if _, err := targetIdentity(h.Repository, h.Catalog); err != nil {
		return "", err
	}
	signer, err := loadRoleSigner(h.Key, &root.Signed, metadata.TIMESTAMP)
	if err != nil {
		return "", err
	}
	_, currentBytes, current, err := readTimestamp(h.Repository)
	if err != nil {
		return "", err
	}
	now := h.Now.UTC()
	renewed := metadata.Timestamp(extendExpiry(current.Signed.Expires, now, time.Duration(h.LeaseHours)*time.Hour))
	renewed.Signed.Version = current.Signed.Version + 1
	renewed.Signed.Meta = current.Signed.Meta
	renewedBytes, err := sign(renewed, []signature.Signer{signer})
	if err != nil {
		return "", err
	}
	err = stage(sourceFiles, h.Root, h.Catalog, h.Output,
		func(staged string) error {
			return replaceFile(filepath.Join(staged, "metadata", "timestamp.json"), renewedBytes)
		},
		func(added, changed []string) error {
			if len(added) != 0 || len(changed) != 1 || changed[0] != "metadata/timestamp.json" {
				return fmt.Errorf("renovação alterou arquivos além de timestamp.json: %s", pyList(append(added, changed...)))
			}
			return nil
		})
	if err != nil {
		return "", err
	}
	target, err := targetIdentity(h.Output, h.Catalog)
	if err != nil {
		return "", err
	}
	return writeReport(h.Report, map[string]any{
		"format":    1,
		"project":   "x86qw",
		"status":    "timestamp-renewed",
		"mode":      "timestamp-only",
		"key_id":    h.Key.ID,
		"key_scope": "timestamp-only",
		"source": map[string]any{
			"timestamp_version": current.Signed.Version,
			"expires":           isoUTC(current.Signed.Expires),
			"sha256":            sha256Hex(currentBytes),
		},
		"renewed": map[string]any{
			"timestamp_version": renewed.Signed.Version,
			"expires":           isoUTC(renewed.Signed.Expires),
			"sha256":            sha256Hex(renewedBytes),
		},
		"changed_files": []string{"metadata/timestamp.json"},
		"root_sha256":   sha256Hex(rootBytes),
		"target":        target,
		"published":     false,
		"checked_at":    isoUTC(now),
	})
}

// RenewSnapshotHandoff writes a repository with a new versioned snapshot and
// a timestamp pointing at it, plus its report.
func RenewSnapshotHandoff(h SnapshotHandoff) (string, error) {
	if h.SnapshotLeaseDays < 1 || h.SnapshotLeaseDays > 3650 {
		return "", errors.New("snapshot_lease_days deve estar entre 1 e 3650")
	}
	if h.TimestampLeaseDays < 1 || h.TimestampLeaseDays > 3650 {
		return "", errors.New("timestamp_lease_days deve estar entre 1 e 3650")
	}
	if err := checkDestinations(h.Repository, h.Output, h.Report); err != nil {
		return "", err
	}
	sourceFiles, err := treeFiles(h.Repository)
	if err != nil {
		return "", err
	}
	rootBytes, root, err := readRoot(h.Root)
	if err != nil {
		return "", err
	}
	if _, err := targetIdentity(h.Repository, h.Catalog); err != nil {
		return "", err
	}
	if h.Snapshot.ID == h.Timestamp.ID {
		return "", errors.New("snapshot e timestamp não podem usar o mesmo key id")
	}
	snapshotSigner, err := loadRoleSigner(h.Snapshot, &root.Signed, metadata.SNAPSHOT)
	if err != nil {
		return "", err
	}
	timestampSigner, err := loadRoleSigner(h.Timestamp, &root.Signed, metadata.TIMESTAMP)
	if err != nil {
		return "", err
	}
	_, currentTimestampBytes, currentTimestamp, err := readTimestamp(h.Repository)
	if err != nil {
		return "", err
	}
	snapshotMeta := currentTimestamp.Signed.Meta["snapshot.json"]
	if snapshotMeta == nil || snapshotMeta.Version < 1 {
		return "", errors.New("timestamp não aponta para uma snapshot versionada")
	}
	snapshotName := fmt.Sprintf("%d.snapshot.json", snapshotMeta.Version)
	currentSnapshotBytes, err := regularFile(filepath.Join(h.Repository, "metadata", snapshotName), "snapshot TUF", handoffMaxFile)
	if err != nil {
		return "", err
	}
	currentSnapshot, err := metadata.Snapshot().FromBytes(currentSnapshotBytes)
	if err != nil {
		return "", fmt.Errorf("snapshot TUF inválida: %v", err)
	}
	if currentSnapshot.Signed.Version != snapshotMeta.Version {
		return "", errors.New("versão da snapshot diverge do timestamp")
	}
	now := h.Now.UTC()
	day := 24 * time.Hour
	snapshotExpires := extendExpiry(currentSnapshot.Signed.Expires, now, time.Duration(h.SnapshotLeaseDays)*day)
	timestampExpires := extendExpiry(currentTimestamp.Signed.Expires, now, time.Duration(h.TimestampLeaseDays)*day)
	if !timestampExpires.Before(snapshotExpires) {
		return "", errors.New("timestamp não pode expirar no mesmo instante ou depois do snapshot")
	}
	renewedSnapshot := metadata.Snapshot(snapshotExpires)
	renewedSnapshot.Signed.Version = currentSnapshot.Signed.Version + 1
	for name, file := range currentSnapshot.Signed.Meta {
		renewedSnapshot.Signed.Meta[name] = file
	}
	snapshotBytes, err := sign(renewedSnapshot, []signature.Signer{snapshotSigner})
	if err != nil {
		return "", err
	}
	renewedTimestamp := metadata.Timestamp(timestampExpires)
	renewedTimestamp.Signed.Version = currentTimestamp.Signed.Version + 1
	renewedTimestamp.Signed.Meta["snapshot.json"] = metaFile(renewedSnapshot.Signed.Version, snapshotBytes)
	timestampBytes, err := sign(renewedTimestamp, []signature.Signer{timestampSigner})
	if err != nil {
		return "", err
	}
	snapshotRelative := fmt.Sprintf("metadata/%d.snapshot.json", renewedSnapshot.Signed.Version)
	var changedFiles []string
	err = stage(sourceFiles, h.Root, h.Catalog, h.Output,
		func(staged string) error {
			if err := writeNew(filepath.Join(staged, filepath.FromSlash(snapshotRelative)), snapshotBytes); err != nil {
				return err
			}
			return replaceFile(filepath.Join(staged, "metadata", "timestamp.json"), timestampBytes)
		},
		func(added, changed []string) error {
			if len(added) != 1 || added[0] != snapshotRelative || len(changed) != 1 || changed[0] != "metadata/timestamp.json" {
				return fmt.Errorf("renovação alterou o conjunto errado: added=%s changed=%s", pyList(added), pyList(changed))
			}
			changedFiles = append(append([]string{}, added...), changed...)
			sort.Strings(changedFiles)
			return nil
		})
	if err != nil {
		return "", err
	}
	target, err := targetIdentity(h.Output, h.Catalog)
	if err != nil {
		return "", err
	}
	expiry := map[string]any{}
	for role, days := range RoleExpiryDays {
		expiry[role] = days
	}
	return writeReport(h.Report, map[string]any{
		"format":           1,
		"project":          "x86qw",
		"status":           "snapshot-renewed",
		"mode":             "snapshot-timestamp",
		"snapshot_key_id":  h.Snapshot.ID,
		"timestamp_key_id": h.Timestamp.ID,
		"key_scope":        "snapshot-and-timestamp",
		"source": map[string]any{
			"snapshot_version":  currentSnapshot.Signed.Version,
			"snapshot_expires":  isoUTC(currentSnapshot.Signed.Expires),
			"snapshot_sha256":   sha256Hex(currentSnapshotBytes),
			"timestamp_version": currentTimestamp.Signed.Version,
			"timestamp_expires": isoUTC(currentTimestamp.Signed.Expires),
			"timestamp_sha256":  sha256Hex(currentTimestampBytes),
		},
		"renewed": map[string]any{
			"snapshot_version":  renewedSnapshot.Signed.Version,
			"snapshot_expires":  isoUTC(renewedSnapshot.Signed.Expires),
			"snapshot_sha256":   sha256Hex(snapshotBytes),
			"timestamp_version": renewedTimestamp.Signed.Version,
			"timestamp_expires": isoUTC(renewedTimestamp.Signed.Expires),
			"timestamp_sha256":  sha256Hex(timestampBytes),
		},
		"changed_files": changedFiles,
		"root_sha256":   sha256Hex(rootBytes),
		"target":        target,
		"published":     false,
		"checked_at":    isoUTC(now),
		"policy": map[string]any{
			"snapshot_lease_days":  h.SnapshotLeaseDays,
			"timestamp_lease_days": h.TimestampLeaseDays,
			"role_expiry_days":     expiry,
		},
	})
}

// pyList renders a string list like Python's repr.
func pyList(values []string) string {
	quoted := make([]string, len(values))
	for index, value := range values {
		quoted[index] = "'" + value + "'"
	}
	return "[" + strings.Join(quoted, ", ") + "]"
}
