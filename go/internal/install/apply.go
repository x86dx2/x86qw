package install

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/atomicfile"
	"github.com/x86dx2/x86qw/go/internal/privfs"
	"github.com/x86dx2/x86qw/go/internal/receipt"
)

// Transaction applies component changes to one installation and can undo
// every completed step in reverse order.
type Transaction struct {
	Root     string
	Stage    string
	Warnings []string
	undo     []func() error
	observe  *Observer
}

// NewTransaction creates a private staging directory under .x86qw/staging.
func NewTransaction(root, prefix string) (*Transaction, error) {
	staging := filepath.Join(root, ".x86qw", "staging")
	if err := privfs.MkdirAll(staging); err != nil {
		return nil, err
	}
	stage, err := os.MkdirTemp(staging, prefix)
	if err != nil {
		return nil, err
	}
	return &Transaction{Root: root, Stage: stage}, nil
}

func (t *Transaction) warn(format string, args ...any) {
	t.Warnings = append(t.Warnings, fmt.Sprintf(format, args...))
}

// Rollback undoes completed steps, newest first.
func (t *Transaction) Rollback() error {
	var errs []error
	for index := len(t.undo) - 1; index >= 0; index-- {
		errs = append(errs, t.undo[index]())
	}
	t.undo = nil
	return errors.Join(errs...)
}

// Close discards the stage (after commit or rollback).
func (t *Transaction) Close() error {
	err := os.RemoveAll(t.Stage)
	os.Remove(filepath.Dir(t.Stage))
	return err
}

func (t *Transaction) path(relative string) string {
	return filepath.Join(t.Root, filepath.FromSlash(relative))
}

func loadRecord(root, id string) (*receipt.Record, map[string]string, error) {
	record, err := receipt.Load(filepath.Join(root, receipt.ComponentsDir, id))
	if errors.Is(err, os.ErrNotExist) {
		return nil, map[string]string{}, nil
	}
	if err != nil {
		return nil, nil, err
	}
	entries := map[string]string{}
	for _, entry := range record.Inventory.Entries {
		entries[entry.Path] = entry.SHA256
	}
	return record, entries, nil
}

// InstallOverlay publishes managed (a tree mapped onto the root) as the new
// payload of component id, like manager.py install_component_overlay_transaction:
// existing files that are not owned with a matching hash are kept and dropped
// from the payload; files removed between versions are deleted only when
// their hash still matches the old inventory.
func (t *Transaction) InstallOverlay(id, managed, selection, source string, validate func(string) error) (int, error) {
	_, old, err := loadRecord(t.Root, id)
	if err != nil {
		return 0, err
	}
	files, err := regularFiles(managed)
	if err != nil {
		return 0, err
	}
	t.observer().files(id, len(files))
	var entries []receipt.Entry
	for _, relative := range files {
		if err := validate(relative); err != nil {
			return 0, err
		}
		destination := t.path(relative)
		if info, err := os.Lstat(destination); err == nil {
			if !info.Mode().IsRegular() {
				return 0, fmt.Errorf("caminho existente não é um arquivo regular: %s", destination)
			}
			current, err := fileSHA256(destination)
			if err != nil {
				return 0, err
			}
			if expected, owned := old[relative]; !owned || current != expected {
				t.warn("Arquivo pessoal ou modificado preservado: %s", destination)
				t.observer().file(id, relative, info.Size(), true)
				continue
			}
		}
		digest, err := fileSHA256(filepath.Join(managed, filepath.FromSlash(relative)))
		if err != nil {
			return 0, err
		}
		entries = append(entries, receipt.Entry{Path: relative, SHA256: digest})
	}
	if len(entries) == 0 {
		return 0, fmt.Errorf("nenhum arquivo novo do componente %s pôde ser instalado", id)
	}
	newNames := map[string]bool{}
	for _, entry := range entries {
		newNames[entry.Path] = true
	}
	var stale []string
	for name, digest := range old {
		if newNames[name] {
			continue
		}
		path := t.path(name)
		info, err := os.Lstat(path)
		if errors.Is(err, os.ErrNotExist) {
			continue
		}
		if err != nil || !info.Mode().IsRegular() {
			return 0, fmt.Errorf("caminho gerenciado inválido: %s", path)
		}
		if current, _ := fileSHA256(path); current == digest {
			stale = append(stale, name)
		} else {
			t.warn("Arquivo modificado preservado: %s", path)
		}
	}
	sort.Strings(stale)

	backup, err := os.MkdirTemp(t.Stage, "."+id+"-payload-old.")
	if err != nil {
		return 0, err
	}
	var backedUp, created, createdDirs []string
	restore := func() error {
		var errs []error
		for _, name := range created {
			errs = append(errs, ignoreMissing(os.Remove(t.path(name))))
		}
		for _, name := range backedUp {
			errs = append(errs, copyFile(filepath.Join(backup, filepath.FromSlash(name)), t.path(name), keepMode))
		}
		for index := len(createdDirs) - 1; index >= 0; index-- {
			os.Remove(createdDirs[index])
		}
		return errors.Join(errs...)
	}
	fail := func(err error) (int, error) {
		if undoErr := restore(); undoErr != nil {
			return 0, fmt.Errorf("falha ao instalar %s; rollback incompleto: %v (%w)", id, undoErr, err)
		}
		return 0, err
	}
	for _, name := range append(append([]string{}, stale...), namesOf(entries)...) {
		path := t.path(name)
		if !regular(path) {
			continue
		}
		if err := copyFile(path, filepath.Join(backup, filepath.FromSlash(name)), keepMode); err != nil {
			return fail(err)
		}
		backedUp = append(backedUp, name)
	}
	for _, entry := range entries {
		source := filepath.Join(managed, filepath.FromSlash(entry.Path))
		destination := t.path(entry.Path)
		existed := regular(destination)
		dirs, err := mkdirParents(t.Root, filepath.Dir(destination), 0o755)
		createdDirs = append(createdDirs, dirs...)
		if err != nil {
			return fail(err)
		}
		info, err := os.Stat(source)
		if err != nil {
			return fail(err)
		}
		mode := os.FileMode(0o644)
		if info.Mode().Perm()&0o111 != 0 {
			mode = 0o755
		}
		if err := copyFile(source, destination, mode); err != nil {
			return fail(err)
		}
		t.observer().file(id, entry.Path, info.Size(), false)
		if digest, _ := fileSHA256(destination); digest != entry.SHA256 {
			return fail(fmt.Errorf("arquivo copiado diverge do plano: %s", destination))
		}
		if !existed {
			created = append(created, entry.Path)
		}
	}
	for _, name := range stale {
		if err := os.Remove(t.path(name)); err != nil {
			return fail(err)
		}
	}
	undoMetadata, err := t.writeRecord(id, selection, source, entries)
	if err != nil {
		return fail(err)
	}
	t.undo = append(t.undo, restore, undoMetadata)
	return len(entries), nil
}

func namesOf(entries []receipt.Entry) []string {
	names := make([]string, len(entries))
	for index, entry := range entries {
		names[index] = entry.Path
	}
	return names
}

// writeRecord atomically replaces a component's receipt and inventory.
func (t *Transaction) writeRecord(id, selection, source string, entries []receipt.Entry) (func() error, error) {
	dir := filepath.Join(t.Root, receipt.ComponentsDir, id)
	inventory := receipt.MarshalInventory(entries)
	digest := sha256.Sum256(inventory)
	rec := &receipt.Receipt{
		Keys: []string{"format", "component", "selection", "source", "inventory_sha256"},
		Values: map[string]string{
			"format": "1", "component": id, "selection": selection, "source": source,
			"inventory_sha256": hex.EncodeToString(digest[:]),
		},
	}
	return t.replaceFiles(dir, map[string][]byte{"inventory": inventory, "receipt": rec.Marshal()})
}

// replaceFiles writes files into dir (created 0700) and returns an undo that
// restores the previous bytes or removes what did not exist.
func (t *Transaction) replaceFiles(dir string, files map[string][]byte) (func() error, error) {
	previous := map[string][]byte{}
	createdDir := false
	if _, err := os.Lstat(dir); errors.Is(err, os.ErrNotExist) {
		if err := privfs.MkdirAll(dir); err != nil {
			return nil, err
		}
		createdDir = true
	}
	names := make([]string, 0, len(files))
	for name := range files {
		names = append(names, name)
	}
	sort.Strings(names)
	undo := func() error {
		var errs []error
		for _, name := range names {
			path := filepath.Join(dir, name)
			if data, ok := previous[name]; ok {
				errs = append(errs, atomicfile.Write(path, data, 0o644))
			} else {
				errs = append(errs, ignoreMissing(os.Remove(path)))
			}
		}
		if createdDir {
			os.Remove(dir)
		}
		return errors.Join(errs...)
	}
	for _, name := range names {
		path := filepath.Join(dir, name)
		if data, err := os.ReadFile(path); err == nil {
			previous[name] = data
		}
		if err := atomicfile.Write(path, files[name], 0o644); err != nil {
			undo()
			return nil, err
		}
	}
	return undo, nil
}

// RemoveComponent deletes a generated component's unchanged files and records.
func (t *Transaction) RemoveComponent(id string) error {
	record, entries, err := loadRecord(t.Root, id)
	if err != nil || record == nil {
		return err
	}
	backup, err := os.MkdirTemp(t.Stage, "."+id+"-remove.")
	if err != nil {
		return err
	}
	var removed []string
	for name, digest := range entries {
		path := t.path(name)
		if current, err := fileSHA256(path); err == nil && current == digest {
			if err := copyFile(path, filepath.Join(backup, filepath.FromSlash(name)), keepMode); err != nil {
				return err
			}
			if err := os.Remove(path); err != nil {
				return err
			}
			removed = append(removed, name)
		}
	}
	dir := filepath.Join(t.Root, receipt.ComponentsDir, id)
	metadata := filepath.Join(backup, ".metadata")
	if err := os.Rename(dir, metadata); err != nil {
		return err
	}
	t.undo = append(t.undo, func() error {
		var errs []error
		errs = append(errs, os.Rename(metadata, dir))
		for _, name := range removed {
			errs = append(errs, copyFile(filepath.Join(backup, filepath.FromSlash(name)), t.path(name), keepMode))
		}
		return errors.Join(errs...)
	})
	return nil
}

// InstallDefault creates a personal default if missing and records it in the
// personal baseline (install_component_default_transaction).
func (t *Transaction) InstallDefault(source, relative string) error {
	digest, err := fileSHA256(source)
	if err != nil {
		return err
	}
	destination := t.path(relative)
	exists := false
	if info, err := os.Lstat(destination); err == nil {
		exists = true
		current, _ := fileSHA256(destination)
		if !info.Mode().IsRegular() || current != digest {
			return nil
		}
		if _, recorded := t.personalBaseline()[relative]; recorded {
			return nil
		}
	}
	var createdDirs []string
	if !exists {
		dirs, err := mkdirParents(t.Root, filepath.Dir(destination), 0o700)
		createdDirs = dirs
		if err != nil {
			return err
		}
		file, err := os.OpenFile(destination, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
		if err != nil {
			return fmt.Errorf("configuração pessoal apareceu durante a instalação: %s", destination)
		}
		in, err := os.Open(source)
		if err == nil {
			_, err = io.Copy(file, in)
			in.Close()
		}
		file.Close()
		if err == nil {
			err = os.Chmod(destination, 0o644)
		}
		if err != nil {
			os.Remove(destination)
			return err
		}
		t.undo = append(t.undo, func() error {
			err := ignoreMissing(os.Remove(destination))
			for index := len(createdDirs) - 1; index >= 0; index-- {
				os.Remove(createdDirs[index])
			}
			return err
		})
	}
	entries := t.personalBaseline()
	entries[relative] = digest
	var list []receipt.Entry
	for name, value := range entries {
		list = append(list, receipt.Entry{Path: name, SHA256: value})
	}
	inventory := receipt.MarshalInventory(list)
	sum := sha256.Sum256(inventory)
	rec := &receipt.Receipt{
		Keys: []string{"format", "component", "selection", "source", "inventory_sha256"},
		Values: map[string]string{
			"format": "1", "component": "personal", "selection": "1", "source": "x86QW personal defaults",
			"inventory_sha256": hex.EncodeToString(sum[:]),
		},
	}
	undo, err := t.replaceFiles(filepath.Join(t.Root, receipt.PersonalDir), map[string][]byte{"inventory": inventory, "receipt": rec.Marshal()})
	if err != nil {
		return err
	}
	t.undo = append(t.undo, undo)
	return nil
}

func (t *Transaction) personalBaseline() map[string]string {
	entries := map[string]string{}
	if record, err := receipt.Load(filepath.Join(t.Root, receipt.PersonalDir)); err == nil {
		for _, entry := range record.Inventory.Entries {
			entries[entry.Path] = entry.SHA256
		}
	}
	return entries
}

// RefreshPackageOrder regenerates qw/pak.lst as the package-order component.
func (t *Transaction) RefreshPackageOrder() error {
	packages, err := ExpectedPackageOrder(t.Root)
	if err != nil {
		return err
	}
	record, _, err := loadRecord(t.Root, "package-order")
	if err != nil {
		return err
	}
	if len(packages) == 0 {
		if record != nil {
			return t.RemoveComponent("package-order")
		}
		return nil
	}
	payload := []byte(strings.Join(packages, "\n") + "\n")
	if record != nil {
		if current, err := os.ReadFile(t.path("qw/pak.lst")); err == nil && string(current) == string(payload) {
			if verifyRecordQuiet(t.Root, record) == nil {
				return nil
			}
		}
	}
	managed := filepath.Join(t.Stage, "package-order-managed")
	os.RemoveAll(managed)
	if err := os.MkdirAll(filepath.Join(managed, "qw"), 0o755); err != nil {
		return err
	}
	if err := os.WriteFile(filepath.Join(managed, "qw", "pak.lst"), payload, 0o644); err != nil {
		return err
	}
	_, err = t.InstallOverlay("package-order", managed, "1", "x86QW deterministic PK3 order", ValidateManagedPath)
	return err
}

func verifyRecordQuiet(root string, record *receipt.Record) error {
	_, err := verifyRecord(root, record)
	return err
}

// ReconcilePlaySupport regenerates derived gamecode and missing per-game
// personal configs when they diverge (reconcile_play_support_transaction).
func (t *Transaction) ReconcilePlaySupport() error {
	games, err := LoadGames()
	if err != nil {
		return err
	}
	available := AvailableGames(t.Root, games)
	issues, expected, err := PlaySupportIssues(t.Root, available)
	if err != nil || len(issues) == 0 {
		return err
	}
	record, old, err := loadRecord(t.Root, "play-support")
	if err != nil {
		return err
	}
	if len(available) == 0 {
		if record != nil {
			return t.RemoveComponent("play-support")
		}
		return nil
	}
	managed := filepath.Join(t.Stage, "managed")
	os.RemoveAll(managed)
	prepared := 0
	relatives := make([]string, 0, len(expected))
	for relative := range expected {
		relatives = append(relatives, relative)
	}
	sort.Strings(relatives)
	for _, relative := range relatives {
		payload := expected[relative]
		destination := t.path(relative)
		if info, err := os.Lstat(destination); err == nil {
			if !info.Mode().IsRegular() {
				return fmt.Errorf("suporte local inválido: %s", destination)
			}
			current, _ := fileSHA256(destination)
			sum := sha256.Sum256(payload)
			if current != hex.EncodeToString(sum[:]) && old[relative] != current {
				t.warn("Arquivo pessoal preservado: %s", destination)
				continue
			}
		}
		candidate := filepath.Join(managed, filepath.FromSlash(relative))
		if err := os.MkdirAll(filepath.Dir(candidate), 0o755); err != nil {
			return err
		}
		if err := os.WriteFile(candidate, payload, 0o644); err != nil {
			return err
		}
		prepared++
	}
	if prepared > 0 {
		if _, err := t.InstallOverlay("play-support", managed, playSupportVersion, "x86QW local-play layer", receipt.SafeRelative); err != nil {
			return err
		}
	} else if record != nil {
		if err := t.RemoveComponent("play-support"); err != nil {
			return err
		}
	}
	for _, game := range available {
		destination := t.path(game.PersonalConfig)
		if _, err := os.Lstat(destination); err == nil {
			continue
		}
		dirs, err := mkdirParents(t.Root, filepath.Dir(destination), 0o755)
		if err != nil {
			return err
		}
		payload := []byte("// x86QW: personalizações locais de " + game.Label + "\n")
		file, err := os.OpenFile(destination, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o644)
		if err != nil {
			return err
		}
		_, err = file.Write(payload)
		file.Close()
		if err != nil {
			return err
		}
		t.undo = append(t.undo, func() error {
			err := ignoreMissing(os.Remove(destination))
			for index := len(dirs) - 1; index >= 0; index-- {
				os.Remove(dirs[index])
			}
			return err
		})
	}
	if issues, _, err := PlaySupportIssues(t.Root, available); err != nil || len(issues) > 0 {
		if err == nil {
			err = fmt.Errorf("suporte de execução incompleto: %s", strings.Join(issues, "; "))
		}
		return err
	}
	return nil
}

// WriteState publishes state.json last, with undo.
func (t *Transaction) WriteState(payload []byte) error {
	undo, err := t.replaceFiles(filepath.Join(t.Root, ".x86qw"), map[string][]byte{"state.json": payload})
	if err != nil {
		return err
	}
	t.undo = append(t.undo, undo)
	return nil
}

func mkdirParents(root, dir string, mode os.FileMode) ([]string, error) {
	var missing []string
	for current := dir; current != root && current != filepath.Dir(current); current = filepath.Dir(current) {
		if _, err := os.Lstat(current); err == nil {
			break
		}
		missing = append(missing, current)
	}
	var created []string
	for index := len(missing) - 1; index >= 0; index-- {
		if err := os.Mkdir(missing[index], mode); err != nil && !errors.Is(err, os.ErrExist) {
			return created, err
		}
		os.Chmod(missing[index], mode)
		created = append(created, missing[index])
	}
	return created, nil
}

// keepMode makes copyFile preserve the source permissions.
const keepMode = os.ModeSticky

// copyFile copies src to dst atomically with mode (or src's, for keepMode).
func copyFile(src, dst string, mode os.FileMode) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	if mode == keepMode {
		info, err := in.Stat()
		if err != nil {
			return err
		}
		mode = info.Mode().Perm()
	}
	if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
		return err
	}
	data, err := io.ReadAll(in)
	if err != nil {
		return err
	}
	return atomicfile.Write(dst, data, mode)
}

func ignoreMissing(err error) error {
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	return err
}
