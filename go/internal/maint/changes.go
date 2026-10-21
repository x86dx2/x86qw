package maint

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/atomicfile"
	"github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/receipt"
)

// Changes compares the files in an installation with what it installed.

// Change is one A/M/D difference.
type Change struct {
	Status, Path, Component string
}

type managedFile struct{ component, sha256 string }

var componentID = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}$`)

// changeIgnoredPaths are the operational paths a comparison never reports.
func changeIgnoredPaths() []string {
	paths := map[string]bool{
		".DS_Store": true, ".gitignore": true, ".x86qw": true, "LICENSE": true, "README-X86QW.txt": true,
		"ezquake/qconsole.log": true, "ezquake/temp": true, "id1/pak0.pak": true, "id1/pak1.pak": true,
		"qw/demos": true, "qw/screenshots": true, "x86qw": true, "x86qw.exe": true, "x86qw.cmd": true, "x86qw.sh": true,
	}
	for _, spec := range catalog.ClientSpecs {
		paths[spec.Runtime("stable")] = true
		paths[spec.Runtime("nightly")] = true
	}
	var sorted []string
	for path := range paths {
		sorted = append(sorted, path)
	}
	sort.Slice(sorted, func(a, b int) bool { return lessCodepoints(sorted[a], sorted[b]) })
	return sorted
}

// changeBaseline is every managed file with its recorded digest.
func changeBaseline(target string) (map[string]managedFile, error) {
	managed := map[string]managedFile{}
	add := func(component string, entries []receipt.Entry, personal bool) error {
		for _, entry := range entries {
			record := managedFile{component, entry.SHA256}
			if previous, ok := managed[entry.Path]; ok && previous != record {
				if personal {
					return fmt.Errorf("The managed and personal inventories disagree about a file: %s", entry.Path)
				}
				return fmt.Errorf("Component inventories disagree about a file: %s", entry.Path)
			}
			managed[entry.Path] = record
		}
		return nil
	}
	dir := filepath.Join(target, filepath.FromSlash(receipt.ComponentsDir))
	entries, err := os.ReadDir(dir)
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return nil, err
	}
	for _, entry := range entries {
		if !componentID.MatchString(entry.Name()) {
			continue
		}
		record, err := receipt.Load(filepath.Join(dir, entry.Name()))
		if errors.Is(err, os.ErrNotExist) {
			continue
		}
		if err != nil {
			return nil, err
		}
		if err := add(entry.Name(), record.Inventory.Entries, false); err != nil {
			return nil, err
		}
	}
	personal, err := receipt.Load(filepath.Join(target, filepath.FromSlash(receipt.PersonalDir)))
	if err == nil {
		if err := add("personal", personal.Inventory.Entries, true); err != nil {
			return nil, err
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return nil, err
	}
	return managed, nil
}

func fileSHA256(name string) (string, error) {
	file, err := os.Open(name)
	if err != nil {
		return "", err
	}
	defer file.Close()
	hash := sha256.New()
	if _, err := io.Copy(hash, file); err != nil {
		return "", err
	}
	return hex.EncodeToString(hash.Sum(nil)), nil
}

func ignored(path string, ignoredPaths []string) bool {
	for _, item := range ignoredPaths {
		if path == item || strings.HasPrefix(path, item+"/") {
			return true
		}
	}
	return false
}

// InspectChanges returns sorted A/M/D differences.
func InspectChanges(target string) ([]Change, error) {
	managed, err := changeBaseline(target)
	if err != nil {
		return nil, err
	}
	ignoredPaths := changeIgnoredPaths()
	var changes []Change
	for relative, record := range managed {
		path := filepath.Join(target, filepath.FromSlash(relative))
		if !regular(path) {
			changes = append(changes, Change{"D", relative, record.component})
		} else if digest, _ := fileSHA256(path); digest != record.sha256 {
			changes = append(changes, Change{"M", relative, record.component})
		}
	}
	err = filepath.WalkDir(target, func(path string, entry fs.DirEntry, err error) error {
		if err != nil || path == target {
			return err
		}
		relative, _ := filepath.Rel(target, path)
		relative = filepath.ToSlash(relative)
		if _, isManaged := managed[relative]; isManaged || ignored(relative, ignoredPaths) {
			return nil
		}
		if entry.Type().IsRegular() || entry.Type()&fs.ModeSymlink != 0 {
			changes = append(changes, Change{"A", relative, ""})
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	sort.Slice(changes, func(a, b int) bool { return lessCodepoints(changes[a].Path, changes[b].Path) })
	return changes, nil
}

var gitignoreSpecial = regexp.MustCompile(`([\\*?\[\]#! ])`)

// RenderGitignore is the installation's selective .gitignore: managed files
// and operational paths stay out.
func RenderGitignore(target string) (string, error) {
	managed, err := changeBaseline(target)
	if err != nil {
		return "", err
	}
	escape := func(path string) string { return gitignoreSpecial.ReplaceAllString(path, `\$1`) }
	operational := map[string]bool{}
	for _, path := range changeIgnoredPaths() {
		operational[escape(strings.TrimRight(path, "/"))] = true
	}
	managedSet := map[string]bool{}
	for path := range managed {
		managedSet[escape(path)] = true
	}
	lines := []string{
		"# Generated by x86QW from the installed inventories.",
		"# New files stay visible; use `x86qw changes` for M/D/A.",
	}
	for _, group := range []map[string]bool{operational, managedSet} {
		var sorted []string
		for path := range group {
			sorted = append(sorted, path)
		}
		sort.Slice(sorted, func(a, b int) bool { return lessCodepoints(sorted[a], sorted[b]) })
		for _, path := range sorted {
			lines = append(lines, "/"+path)
		}
	}
	return strings.Join(append(lines, ""), "\n"), nil
}

// SyncGitignore writes .gitignore at the installation root.
func SyncGitignore(target string) error {
	payload, err := RenderGitignore(target)
	if err != nil {
		return err
	}
	return atomicfile.Write(filepath.Join(target, ".gitignore"), []byte(payload), 0o644)
}
