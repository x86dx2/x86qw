package maint

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/download"
	"github.com/x86dx2/x86qw/go/internal/receipt"
	"github.com/x86dx2/x86qw/go/internal/state"
)

// Ported from manager.py cleanup_data, purge and their helpers.

const (
	cacheMarker        = ".x86qw-cache"
	cacheMarkerValue   = "x86qw-cache-v1"
	legacyCacheName    = "x86-qw"
	legacyCacheMarker  = ".x86-qw-cache"
	legacyMarkerValue  = "x86-qw-cache-v1"
	cliReceipt         = ".x86qw/cli/receipt"
	legacyCLIReceipt   = ".x86qw/cli.receipt"
	nquakeReceipt      = ".x86qw/nquake.receipt"
	nquakeInventory    = ".x86qw/nquake.inventory"
	personalComponent  = "personal"
	stateRelative      = ".x86qw/state.json"
	metadataDirectory  = ".x86qw"
	componentsRelative = ".x86qw/components"
)

// ValidateTarget mirrors Installer.validate_target for maintenance actions.
func ValidateTarget(target string, mustExist bool) (string, error) {
	info, err := os.Lstat(target)
	exists := err == nil
	if exists && info.Mode()&os.ModeSymlink != 0 {
		return "", fmt.Errorf("O diretório de destino não pode ser um link simbólico: %s", target)
	}
	if exists && !info.IsDir() {
		return "", fmt.Errorf("O destino não é um diretório: %s", target)
	}
	if !exists && mustExist {
		return "", fmt.Errorf("O diretório de destino não existe: %s", target)
	}
	resolved := resolve(target)
	if resolved == filepath.Dir(resolved) {
		return "", errors.New("A raiz do sistema de arquivos não pode ser usada como destino.")
	}
	return resolved, nil
}

// ManagedIdentity mirrors managed_installation_identity: persisted evidence
// that the target is an x86QW installation.
func ManagedIdentity(target string) ([]string, error) {
	if _, err := os.Lstat(target); err != nil {
		return nil, nil
	}
	if info, err := os.Lstat(filepath.Join(target, metadataDirectory)); err == nil {
		if !info.IsDir() {
			return nil, fmt.Errorf("Metadados da instalação inválidos: %s", filepath.Join(target, metadataDirectory))
		}
	}
	var evidence []string
	statePath := filepath.Join(target, filepath.FromSlash(stateRelative))
	if info, err := os.Lstat(statePath); err == nil {
		if !info.Mode().IsRegular() {
			return nil, fmt.Errorf("Estado da instalação inválido: %s", statePath)
		}
		if _, err := state.Read(target); err != nil {
			return nil, fmt.Errorf("Estado da instalação inválido: %s", statePath)
		}
		evidence = append(evidence, "state")
	}
	if regular(filepath.Join(target, filepath.FromSlash(cliReceipt))) || regular(filepath.Join(target, filepath.FromSlash(legacyCLIReceipt))) {
		evidence = append(evidence, "cli")
	}
	if regular(filepath.Join(target, filepath.FromSlash(nquakeReceipt))) {
		evidence = append(evidence, "nquake")
	}
	for _, id := range componentRecordIDs(target) {
		if _, err := receipt.Load(filepath.Join(target, filepath.FromSlash(componentsRelative), id)); err == nil {
			evidence = append(evidence, "component:"+id)
		}
	}
	for _, spec := range catalog.ClientSpecs {
		for _, channel := range catalog.Channels {
			if regular(filepath.Join(target, filepath.FromSlash(spec.Receipt(channel)))) {
				evidence = append(evidence, "client:"+spec.Platform+":"+channel)
			}
		}
	}
	return evidence, nil
}

// RequireManagedIdentity refuses targets without x86QW evidence.
func RequireManagedIdentity(target, action string) error {
	evidence, err := ManagedIdentity(target)
	if err != nil {
		return err
	}
	if len(evidence) == 0 {
		return fmt.Errorf("A operação %s recusou um destino sem identidade gerenciada x86QW validada: %s", action, target)
	}
	return nil
}

func componentRecordIDs(target string) []string {
	entries, _ := os.ReadDir(filepath.Join(target, filepath.FromSlash(componentsRelative)))
	var ids []string
	for _, entry := range entries {
		if componentID.MatchString(entry.Name()) {
			ids = append(ids, entry.Name())
		}
	}
	return ids
}

// cacheRoot is the shared installer cache (X86QW_CACHE_DIR or per-user).
type cacheRoot struct{ path, marker, value string }

func ownedCaches() ([]string, error) {
	current, err := download.CacheDir()
	if err != nil {
		return nil, err
	}
	candidates := []cacheRoot{
		{current, cacheMarker, cacheMarkerValue},
		{filepath.Join(filepath.Dir(current), legacyCacheName), legacyCacheMarker, legacyMarkerValue},
	}
	var owned []string
	for _, candidate := range candidates {
		info, err := os.Lstat(candidate.path)
		if err != nil {
			continue
		}
		if info.Mode()&os.ModeSymlink != 0 {
			return nil, fmt.Errorf("a pasta de cache é um symlink: %s", candidate.path)
		}
		if !info.IsDir() {
			return nil, fmt.Errorf("O caminho reservado ao cache não é um diretório: %s", candidate.path)
		}
		markerPath := filepath.Join(candidate.path, candidate.marker)
		marker, err := os.ReadFile(markerPath)
		if errors.Is(err, fs.ErrNotExist) && candidate.marker == cacheMarker && download.AdoptableCache(candidate.path) {
			// Only the CLI's TUF metadata, written before any install.
			owned = append(owned, candidate.path)
			continue
		}
		if err != nil || strings.TrimRight(string(marker), "\n") != candidate.value {
			return nil, fmt.Errorf("O diretório de cache contém arquivos que não pertencem ao instalador e foi preservado: %s", candidate.path)
		}
		// Like private_fs.validate_private_file: owner-only regular file
		// (Windows relies on ACLs, which the mode bits do not reflect).
		if info, err := os.Lstat(markerPath); err != nil || !info.Mode().IsRegular() || (runtime.GOOS != "windows" && info.Mode().Perm()&0o077 != 0) {
			return nil, fmt.Errorf("O marcador de propriedade do cache não pôde ser protegido: %s", markerPath)
		}
		owned = append(owned, candidate.path)
	}
	return owned, nil
}

// minimalRemovalPaths drops descendants of already selected paths.
func minimalRemovalPaths(paths []string) []string {
	unique := map[string]bool{}
	for _, path := range paths {
		unique[path] = true
	}
	var sorted []string
	for path := range unique {
		sorted = append(sorted, path)
	}
	depth := func(path string) int { return len(strings.Split(path, string(filepath.Separator))) }
	sort.Slice(sorted, func(a, b int) bool {
		if depth(sorted[a]) != depth(sorted[b]) {
			return depth(sorted[a]) < depth(sorted[b])
		}
		return sorted[a] < sorted[b]
	})
	var selected []string
	for _, path := range sorted {
		covered := false
		for _, parent := range selected {
			covered = covered || path == parent || strings.HasPrefix(path, parent+string(filepath.Separator))
		}
		if !covered {
			selected = append(selected, path)
		}
	}
	return selected
}

// quarantineRemove moves every path aside first (so a failure restores all),
// then deletes them.
func quarantineRemove(paths []string) error {
	type moved struct{ original, aside string }
	var done []moved
	for _, path := range paths {
		if deferLauncher(path) {
			continue
		}
		aside := filepath.Join(filepath.Dir(path), fmt.Sprintf(".x86qw-%s-quarantine.%d", filepath.Base(path), os.Getpid()))
		if err := os.Rename(path, aside); err != nil {
			for index := len(done) - 1; index >= 0; index-- {
				os.Rename(done[index].aside, done[index].original)
			}
			return fmt.Errorf("Recolher %s falhou; os domínios anteriores foram restaurados: %w", path, err)
		}
		done = append(done, moved{path, aside})
	}
	var errs []error
	for _, item := range done {
		if err := os.RemoveAll(item.aside); err != nil && !removeAfterExit(item.original, item.aside) {
			errs = append(errs, err)
		}
	}
	return errors.Join(errs...)
}

// managedRuntimePaths lists every file owned by a component inventory.
func managedRuntimePaths(target string) map[string]bool {
	managed := map[string]bool{}
	for _, id := range componentRecordIDs(target) {
		record, err := receipt.Load(filepath.Join(target, filepath.FromSlash(componentsRelative), id))
		if err != nil {
			continue
		}
		for _, entry := range record.Inventory.Entries {
			managed[entry.Path] = true
		}
	}
	return managed
}

// CleanupSelection mirrors _runtime_cleanup_selection.
func CleanupSelection(target string, downloads, personalData bool) ([]string, []string) {
	if info, err := os.Lstat(target); err != nil || !info.IsDir() {
		return nil, nil
	}
	var cachePaths, personalPaths []string
	for _, relative := range []string{"ezquake/sb/cache", "ezquake/temp"} {
		path := filepath.Join(target, filepath.FromSlash(relative))
		if _, err := os.Lstat(path); err == nil {
			cachePaths = append(cachePaths, path)
		}
	}
	fortress := filepath.Join(target, "fortress")
	fortressDir := false
	if info, err := os.Lstat(fortress); err == nil && info.IsDir() {
		fortressDir = true
		filepath.WalkDir(fortress, func(path string, entry fs.DirEntry, err error) error {
			if err == nil && entry.Type().IsRegular() && strings.HasSuffix(entry.Name(), ".tmp") {
				cachePaths = append(cachePaths, path)
			}
			return nil
		})
	}
	demos := filepath.Join(target, "td2", "demos")
	if info, err := os.Lstat(demos); err == nil && info.IsDir() {
		entries, _ := os.ReadDir(demos)
		for _, entry := range entries {
			if info, err := entry.Info(); err == nil && entry.Type().IsRegular() && info.Size() == 0 {
				cachePaths = append(cachePaths, filepath.Join(demos, entry.Name()))
			}
		}
	}
	if downloads && fortressDir {
		managed := managedRuntimePaths(target)
		for _, rootName := range []string{"fortress/progs", "fortress/sound"} {
			root := filepath.Join(target, filepath.FromSlash(rootName))
			if info, err := os.Lstat(root); err != nil || !info.IsDir() {
				continue
			}
			filepath.WalkDir(root, func(path string, entry fs.DirEntry, err error) error {
				if err != nil || !entry.Type().IsRegular() {
					return nil
				}
				relative, _ := filepath.Rel(target, path)
				if !managed[filepath.ToSlash(relative)] {
					cachePaths = append(cachePaths, path)
				}
				return nil
			})
		}
	}
	if personalData {
		for _, relative := range []string{"ezquake/.ezquake_history", "qw/qconsole.log", "logs", "td2/demos"} {
			path := filepath.Join(target, filepath.FromSlash(relative))
			if _, err := os.Lstat(path); err == nil {
				personalPaths = append(personalPaths, path)
			}
		}
	}
	selectedPersonal := minimalRemovalPaths(personalPaths)
	var selectedCache []string
	for _, path := range minimalRemovalPaths(cachePaths) {
		covered := false
		for _, parent := range selectedPersonal {
			covered = covered || path == parent || strings.HasPrefix(path, parent+string(filepath.Separator))
		}
		if !covered {
			selectedCache = append(selectedCache, path)
		}
	}
	return selectedCache, selectedPersonal
}

// CleanupResult reports what cleanup removed.
type CleanupResult struct {
	Cache, Personal int
	Caches          []string
}

// Cleanup removes regenerable data, optional personal data and the
// installer caches, like cleanup_data.
func Cleanup(target string, downloads, personalData bool) (*CleanupResult, error) {
	if err := RequireManagedIdentity(target, "cleanup"); err != nil {
		return nil, err
	}
	cachePaths, personalPaths := CleanupSelection(target, downloads, personalData)
	caches, err := ownedCaches()
	if err != nil {
		return nil, err
	}
	selected := minimalRemovalPaths(append(append(append([]string{}, caches...), cachePaths...), personalPaths...))
	if err := quarantineRemove(selected); err != nil {
		return nil, err
	}
	return &CleanupResult{Cache: len(cachePaths), Personal: len(personalPaths), Caches: caches}, nil
}
