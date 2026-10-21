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
	"github.com/x86dx2/x86qw/go/internal/cli"
	"github.com/x86dx2/x86qw/go/internal/download"
	"github.com/x86dx2/x86qw/go/internal/lock"
	"github.com/x86dx2/x86qw/go/internal/receipt"
)

// Ported from manager.py uninstall, purge and remove_empty_metadata_transaction.

var legacyComponents = []string{"clan-arena", "nquake-bootstrap", "nquake-ktx", "nquake-sounds"}

// Report collects user-facing messages of a maintenance operation.
type Report struct {
	// Lines keeps info ("· ") and success ("✓ ") messages in emission order.
	Lines    []string
	Warnings []string
}

func (r *Report) info(format string, args ...any) {
	r.Lines = append(r.Lines, "· "+fmt.Sprintf(format, args...))
}
func (r *Report) success(format string, args ...any) {
	r.Lines = append(r.Lines, "✓ "+fmt.Sprintf(format, args...))
}
func (r *Report) warn(format string, args ...any) {
	r.Warnings = append(r.Warnings, fmt.Sprintf(format, args...))
}

func cliPaths(target string) []string {
	return []string{
		filepath.Join(target, ".x86qw", "cli"),
		filepath.Join(target, filepath.FromSlash(legacyCLIReceipt)),
		filepath.Join(target, "x86qw.sh"),
		filepath.Join(target, "x86qw.cmd"),
	}
}

func lexists(path string) bool {
	_, err := os.Lstat(path)
	return err == nil
}

// removeShellIntegration drops ~/.local/bin/x86qw and the Linux desktop
// entry (or, on Windows, the shortcuts and the PATH entry), and the qw://
// and demo handlers, when they point at this installation.
func removeShellIntegration(target string) {
	cli.SetHandlers(target, false, false)
	if runtime.GOOS == "windows" {
		cli.RemoveShortcuts(target)
		cli.RemoveUserPath(target)
		return
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return
	}
	if runtime.GOOS == "linux" {
		cli.RemoveDesktopEntry(target, home)
	}
	command := filepath.Join(home, ".local", "bin", "x86qw")
	info, err := os.Lstat(command)
	if err != nil || info.Mode()&os.ModeSymlink == 0 {
		return
	}
	link, err := os.Readlink(command)
	if err != nil {
		return
	}
	if !filepath.IsAbs(link) {
		link = filepath.Join(filepath.Dir(command), link)
	}
	if resolve(link) == resolve(filepath.Join(target, "x86qw.sh")) {
		os.Remove(command)
	}
}

// removeComponent deletes unchanged files and the metadata of a component.
func removeComponent(target, id string, report *Report) (int, error) {
	dir := filepath.Join(target, filepath.FromSlash(componentsRelative), id)
	record, err := receipt.Load(dir)
	if errors.Is(err, os.ErrNotExist) {
		return 0, nil
	}
	if err != nil {
		return 0, err
	}
	removed := 0
	for _, entry := range record.Inventory.Entries {
		path := filepath.Join(target, filepath.FromSlash(entry.Path))
		info, err := os.Lstat(path)
		if err != nil {
			continue
		}
		if !info.Mode().IsRegular() {
			return removed, fmt.Errorf("Caminho gerenciado inválido: %s", path)
		}
		if digest, _ := fileSHA256(path); digest == entry.SHA256 {
			if err := os.Remove(path); err != nil {
				return removed, err
			}
			removed++
		} else {
			report.warn("Arquivo modificado preservado: %s", path)
		}
	}
	return removed, os.RemoveAll(dir)
}

// Uninstall removes runtimes, metadata and unchanged managed files while
// preserving the registered PAKs and personal files.
func Uninstall(target string) (*Report, error) {
	report := &Report{}
	if err := RequireManagedIdentity(target, "uninstall"); err != nil {
		return nil, err
	}
	if lexists(filepath.Join(target, filepath.FromSlash(nquakeReceipt))) || legacyLayout(target) {
		return nil, errors.New("esta instalação usa metadados anteriores ao 1.0 que esta versão não desinstala; reinstale antes")
	}
	held, err := lock.Acquire(target, "uninstall")
	if err != nil {
		return nil, err
	}
	released := false
	defer func() {
		if !released {
			held.Release()
		}
	}()
	// RequireManagedIdentity found the state or a receipt, so there is
	// always metadata to remove from here on.
	for _, id := range componentRecordIDs(target) {
		if _, err := receipt.Load(filepath.Join(target, filepath.FromSlash(componentsRelative), id)); err != nil {
			return nil, err
		}
	}
	preserved := map[string]string{}
	for _, relative := range []string{"id1/pak0.pak", "id1/pak1.pak", "ezquake/configs/config.cfg"} {
		if path := filepath.Join(target, filepath.FromSlash(relative)); regular(path) {
			preserved[relative], _ = fileSHA256(path)
		}
	}
	var removal []string
	for _, spec := range catalog.ClientSpecs {
		for _, channel := range catalog.Channels {
			receiptPath := filepath.Join(target, filepath.FromSlash(spec.Receipt(channel)))
			payload, err := os.ReadFile(receiptPath)
			if errors.Is(err, os.ErrNotExist) {
				continue
			}
			if err != nil {
				return nil, err
			}
			if _, err := receipt.ParseClient(payload, spec.Context(channel)); err != nil {
				return nil, fmt.Errorf("%w: %s", err, receiptPath)
			}
			removal = append(removal, filepath.Join(target, spec.Runtime(channel)), receiptPath)
		}
	}
	removal = append(removal,
		filepath.Join(target, filepath.FromSlash(nquakeReceipt)),
		filepath.Join(target, filepath.FromSlash(nquakeInventory)),
		filepath.Join(target, filepath.FromSlash(stateRelative)))
	removal = append(removal, cliPaths(target)...)
	cliPresent := false
	for _, path := range cliPaths(target) {
		cliPresent = cliPresent || lexists(path)
	}
	var existing []string
	for _, path := range removal {
		if lexists(path) {
			existing = append(existing, path)
		}
	}
	if err := quarantineRemove(minimalRemovalPaths(existing)); err != nil {
		return nil, err
	}
	inventory, err := catalog.LoadInventory()
	if err != nil {
		return nil, err
	}
	order := []string{"package-order", "play-support", "presets", "maps"}
	ids := inventory.IDs()
	for index := len(ids) - 1; index >= 0; index-- {
		order = append(order, ids[index])
	}
	order = append(order, legacyComponents...)
	for _, id := range order {
		if _, err := removeComponent(target, id, report); err != nil {
			return nil, err
		}
	}
	for relative, expected := range preserved {
		if digest, _ := fileSHA256(filepath.Join(target, filepath.FromSlash(relative))); digest != expected {
			return nil, fmt.Errorf("%s changed during uninstall", relative)
		}
	}
	removeEmptyMetadata(target, false)
	removeShellIntegration(target)
	held.Release()
	released = true
	// After the lock is gone the sessions directory may be empty too.
	removeEmptyMetadata(target, false)
	if cliPresent {
		report.success("CLI permanente x86QW removida.")
	}
	report.success("Componentes gerenciados removidos de %s.", target)
	report.info("PAKs registrados e arquivos pessoais foram preservados.")
	return report, nil
}

// legacyLayout reports flat .x86qw/<id>.receipt pairs from pre-1.0 installers.
func legacyLayout(target string) bool {
	entries, _ := os.ReadDir(filepath.Join(target, metadataDirectory))
	for _, entry := range entries {
		name := entry.Name()
		if entry.Type().IsRegular() && (strings.HasSuffix(name, ".receipt") || strings.HasSuffix(name, ".inventory")) &&
			name != "cli.receipt" {
			return true
		}
	}
	return false
}

// removeEmptyMetadata removes .x86qw when it holds only empty directories
// (and the target too when includeTarget and nothing else remains).
func removeEmptyMetadata(target string, includeTarget bool) {
	root := filepath.Join(target, metadataDirectory)
	if info, err := os.Lstat(root); err == nil && info.IsDir() {
		hasFiles := false
		var dirs []string
		filepath.WalkDir(root, func(path string, entry fs.DirEntry, err error) error {
			if err != nil {
				hasFiles = true
				return err
			}
			if entry.IsDir() {
				dirs = append(dirs, path)
			} else {
				hasFiles = true
			}
			return nil
		})
		if hasFiles {
			return
		}
		sort.Slice(dirs, func(a, b int) bool { return len(dirs[a]) > len(dirs[b]) })
		for _, dir := range dirs {
			if os.Remove(dir) != nil {
				return
			}
		}
	}
	if includeTarget {
		if entries, err := os.ReadDir(target); err == nil && len(entries) == 0 {
			os.Remove(target)
		}
	}
}

// Purge removes the whole installation and the installer caches.
func Purge(target string) (*Report, error) {
	report := &Report{}
	caches, err := ownedCaches()
	if err != nil {
		return nil, err
	}
	exists := lexists(target)
	var held *lock.Lock
	var candidates []string
	if exists {
		if err := RequireManagedIdentity(target, "uninstall --purge"); err != nil {
			return nil, err
		}
		if held, err = lock.Acquire(target, "uninstall"); err != nil {
			return nil, err
		}
		if cwd, err := os.Getwd(); err == nil {
			if resolved := resolve(cwd); resolved == target || inside(resolved, target) {
				os.Chdir(filepath.Dir(target))
			}
		}
		metadata := filepath.Join(target, metadataDirectory)
		sessions := filepath.Join(metadata, "sessions")
		children, _ := os.ReadDir(target)
		for _, child := range children {
			if path := filepath.Join(target, child.Name()); path != metadata {
				candidates = append(candidates, path)
			}
		}
		metadataChildren, _ := os.ReadDir(metadata)
		for _, child := range metadataChildren {
			if path := filepath.Join(metadata, child.Name()); path != sessions {
				candidates = append(candidates, path)
			}
		}
		sessionChildren, _ := os.ReadDir(sessions)
		for _, child := range sessionChildren {
			if child.Name() != "active.lock" {
				candidates = append(candidates, filepath.Join(sessions, child.Name()))
			}
		}
	} else {
		report.info("Nenhum diretório de instalação foi encontrado em %s.", target)
	}
	candidates = append(candidates, caches...)
	if err := quarantineRemove(minimalRemovalPaths(candidates)); err != nil {
		if held != nil {
			held.Release()
		}
		return nil, err
	}
	removeShellIntegration(target)
	if held != nil {
		report.info("Conteúdo da instalação removido; o diretório do lock será finalizado ao encerrar a operação.")
	}
	for _, root := range caches {
		if !lexists(root) {
			report.success("Cache removido: %s", root)
		}
	}
	if len(caches) == 0 {
		root, _ := download.CacheDir()
		report.info("Nenhum cache do instalador foi encontrado em %s.", root)
	}
	report.success("Remoção total concluída; nenhum dado gerenciado pelo x86QW foi preservado.")
	// Like the Python CLI, the directory is reported once the lock is gone.
	if held != nil {
		if err := held.Release(); err != nil {
			return nil, err
		}
		removeEmptyMetadata(target, true)
		if lexists(target) {
			return nil, errors.New("A topologia final da instalação não está vazia e foi preservada.")
		}
		report.success("Diretório da instalação removido: %s", target)
	}
	return report, nil
}
