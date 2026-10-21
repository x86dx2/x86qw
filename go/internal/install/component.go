package install

import (
	"encoding/json"
	"fmt"
	"io/fs"
	"os"
	"path"
	"path/filepath"
	"runtime"
	"sort"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/archive"
	"github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/receipt"
)

// mutableDefaults are payload files treated as create-only personal defaults.
var mutableDefaults = map[string][]string{"clan-arena": {"prox/configs/config.cfg"}}

// Default is a create-only personal file shipped by a component.
type Default struct {
	Source      string
	Destination string // relative to the installation root
}

// Prepared is an extracted, verified component package.
type Prepared struct {
	Managed  string // directory whose tree maps onto the installation root
	Defaults []Default
}

type packageManifest struct {
	Format         int    `json:"format"`
	Project        string `json:"project"`
	Package        string `json:"package"`
	SourceRevision string `json:"source_revision"`
	SourceCommit   string `json:"source_commit"`
	Version        string `json:"version"`
	Members        []struct {
		Path   string `json:"path"`
		SHA256 string `json:"sha256"`
	} `json:"members"`
}

// PreparePackage extracts a component ZIP into stage and checks it against
// its internal manifest and the catalog entry.
func PreparePackage(artifact string, pkg catalog.Package, stage string) (*Prepared, error) {
	id := pkg.Package
	plan, err := archive.Open(artifact, archive.DefaultLimits)
	if err != nil {
		return nil, fmt.Errorf("package %s: %w", id, err)
	}
	root := filepath.Join(stage, "package-"+id)
	err = plan.Extract(root)
	plan.Close()
	if err != nil {
		return nil, fmt.Errorf("package %s: %w", id, err)
	}
	payload, err := os.ReadFile(filepath.Join(root, "_x86qw", "component.json"))
	if err != nil {
		return nil, fmt.Errorf("invalid internal metadata in package %s", id)
	}
	var manifest packageManifest
	if err := json.Unmarshal(payload, &manifest); err != nil {
		return nil, fmt.Errorf("invalid internal metadata in package %s", id)
	}
	revision := manifest.SourceRevision
	if pkg.SourceRevision == "" {
		revision = manifest.SourceCommit
	}
	if manifest.Format != 1 || manifest.Project != "x86qw" || manifest.Package != id || revision != pkg.Revision() || manifest.Members == nil {
		return nil, fmt.Errorf("invalid internal identity in package %s", id)
	}
	version := manifest.Version
	if version == "" && len(revision) >= 12 {
		version = revision[:12]
	}
	if version != pkg.Version {
		return nil, fmt.Errorf("invalid internal version in package %s", id)
	}
	expected := map[string]bool{}
	for _, member := range manifest.Members {
		if !strings.HasPrefix(member.Path, "payload/") && !strings.HasPrefix(member.Path, "defaults/") {
			return nil, fmt.Errorf("invalid internal destination in package %s: %s", id, member.Path)
		}
		digest, err := fileSHA256(filepath.Join(root, filepath.FromSlash(member.Path)))
		if err != nil || digest != member.SHA256 {
			return nil, fmt.Errorf("invalid internal file in package %s: %s", id, member.Path)
		}
		expected[member.Path] = true
	}
	actual := 0
	err = filepath.WalkDir(root, func(name string, entry fs.DirEntry, err error) error {
		if err != nil || entry.IsDir() {
			return err
		}
		relative, _ := filepath.Rel(root, name)
		relative = filepath.ToSlash(relative)
		if relative == "_x86qw/component.json" {
			return nil
		}
		if !expected[relative] {
			return fmt.Errorf("the contents of package %s differ from its internal inventory", id)
		}
		actual++
		return nil
	})
	if err != nil {
		return nil, err
	}
	if actual != len(expected) {
		return nil, fmt.Errorf("the contents of package %s differ from its internal inventory", id)
	}
	managed := filepath.Join(root, "payload")
	if info, err := os.Stat(managed); err != nil || !info.IsDir() {
		return nil, fmt.Errorf("package %s has no installable payload", id)
	}
	prepared := &Prepared{Managed: managed}
	defaultsRoot := filepath.Join(root, "defaults")
	if files, err := regularFiles(defaultsRoot); err == nil {
		for _, relative := range files {
			prepared.Defaults = append(prepared.Defaults, Default{Source: filepath.Join(defaultsRoot, filepath.FromSlash(relative)), Destination: relative})
		}
	}
	for _, relative := range mutableDefaults[id] {
		source := filepath.Join(managed, filepath.FromSlash(relative))
		if info, err := os.Lstat(source); err != nil || !info.Mode().IsRegular() {
			continue
		}
		normalized := filepath.Join(root, "normalized-defaults", filepath.FromSlash(relative))
		if err := os.MkdirAll(filepath.Dir(normalized), 0o755); err != nil {
			return nil, err
		}
		if err := os.Rename(source, normalized); err != nil {
			return nil, err
		}
		prepared.Defaults = append(prepared.Defaults, Default{Source: normalized, Destination: relative})
	}
	removeEmptyDirectories(managed)
	return prepared, nil
}

// HostVariant is the runtime variant (runtimes.json) for this machine.
func HostVariant() (string, error) {
	switch runtime.GOOS + "/" + runtime.GOARCH {
	case "darwin/arm64":
		return "macos-arm64", nil
	case "linux/amd64":
		return "linux-amd64", nil
	case "windows/amd64":
		return "windows-x64", nil
	}
	return "", fmt.Errorf("platform %s/%s has no x86QW servers", runtime.GOOS, runtime.GOARCH)
}

// NormalizePlatform keeps only this host's executable of a multi-platform
// component and moves it to its install path.
func NormalizePlatform(component catalog.Component, managed, variant string) error {
	files := component.PlatformFiles()
	if len(files) == 0 {
		return nil
	}
	matches := 0
	for _, file := range files {
		if file.Platform == variant {
			matches++
		}
	}
	if matches != 1 {
		return fmt.Errorf("package %s does not hold exactly one executable for %s", component.ID, variant)
	}
	for _, file := range files {
		source := filepath.Join(managed, filepath.FromSlash(file.Destination))
		if info, err := os.Lstat(source); err != nil || !info.Mode().IsRegular() {
			return fmt.Errorf("platform executable missing from package %s: %s", component.ID, file.Destination)
		}
		if file.Platform != variant {
			if err := os.Remove(source); err != nil {
				return err
			}
			continue
		}
		destination := filepath.Join(managed, filepath.FromSlash(file.InstallDestination))
		if _, err := os.Lstat(destination); err == nil {
			return fmt.Errorf("package %s has a duplicate operational destination: %s", component.ID, file.InstallDestination)
		}
		if err := os.MkdirAll(filepath.Dir(destination), 0o755); err != nil {
			return err
		}
		if err := os.Rename(source, destination); err != nil {
			return err
		}
		if !strings.HasPrefix(variant, "windows") {
			if err := os.Chmod(destination, 0o755); err != nil {
				return err
			}
		}
	}
	removeEmptyDirectories(filepath.Join(managed, "platforms"))
	return nil
}

// ValidateManagedPath refuses a managed path that is not a safe relative
// path inside the installation.
func ValidateManagedPath(value string) error {
	if err := receipt.SafeRelative(value); err != nil {
		return err
	}
	if value == "ezquake/configs/config.cfg" || value == "ezquake/configs/preset.cfg" {
		return fmt.Errorf("personal settings cannot be managed: %s", value)
	}
	parts := strings.Split(value, "/")
	switch {
	case parts[0] == "id1":
		if len(parts) == 4 && parts[1] == "maps" && parts[2] == "ctf" && strings.EqualFold(path.Ext(value), ".ent") {
			return nil
		}
		return fmt.Errorf("unexpected path in the managed inventory: %s", value)
	case value == "mvdsv" || value == "mvdsv.exe":
		return nil
	case len(parts) >= 3 && parts[0] == "docs" && parts[1] == "licenses":
		return nil
	case len(parts) >= 2 && (parts[0] == "qtv" || parts[0] == "qwfwd"):
		return nil
	case value == "LICENSE" || value == "readme.txt" || value == "README-X86QW.txt":
		return nil
	}
	switch parts[0] {
	case "ezquake", "qw", "arena", "prox", "fortress", "td2":
		return nil
	}
	return fmt.Errorf("unexpected path in the managed inventory: %s", value)
}

// regularFiles lists files below root as sorted POSIX relative paths.
func regularFiles(root string) ([]string, error) {
	var files []string
	err := filepath.WalkDir(root, func(name string, entry fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if entry.Type()&fs.ModeSymlink != 0 {
			return fmt.Errorf("unsupported symlink: %s", name)
		}
		if entry.Type().IsRegular() {
			relative, _ := filepath.Rel(root, name)
			files = append(files, filepath.ToSlash(relative))
		}
		return nil
	})
	sort.Strings(files)
	return files, err
}

func removeEmptyDirectories(root string) {
	var directories []string
	filepath.WalkDir(root, func(name string, entry fs.DirEntry, err error) error {
		if err == nil && entry.IsDir() {
			directories = append(directories, name)
		}
		return nil
	})
	for index := len(directories) - 1; index >= 0; index-- {
		os.Remove(directories[index])
	}
}
