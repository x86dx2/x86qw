package maint

import (
	"archive/zip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/pyjson"
)

// Ported from x86qw_runtime/profiles.py.

var (
	profileFiles = []string{"ezquake/configs/config.cfg", "ezquake/configs/preset.cfg", "qtv/qtv.cfg", "qwfwd/qwfwd.cfg"}
	profileRoots = []string{"arena", "fortress", "prox", "qw", "td2"}
	cacheRoots   = []string{"ezquake/sb/cache", "ezquake/temp"}
	personalRoot = []string{"ezquake/.ezquake_history", "ezquake/qconsole.log", "qw/qconsole.log", "qw/demos", "qw/screenshots", "logs", "td2/demos"}
)

const profileNotice = "Modo owner-only: perfil user-owned. Sem upload automático. " +
	"Este zip contém só configurações pessoais, sem cache, demos ou bytes gerenciados.\n"

// IsUserProfilePath mirrors profiles.is_user_profile_path.
func IsUserProfilePath(relative string) bool {
	if relative == "" || strings.Contains(relative, `\`) || strings.HasPrefix(relative, "/") {
		return false
	}
	parts := strings.Split(relative, "/")
	for _, part := range parts {
		if part == "" || part == "." || part == ".." {
			return false
		}
	}
	for _, name := range profileFiles {
		if relative == name {
			return true
		}
	}
	if len(parts) != 2 {
		return false
	}
	root := false
	for _, name := range profileRoots {
		root = root || parts[0] == name
	}
	ext := path.Ext(parts[1])
	return root && strings.HasPrefix(parts[1], "x86qw-") && (ext == ".cfg" || ext == ".json")
}

func regular(name string) bool {
	info, err := os.Lstat(name)
	return err == nil && info.Mode().IsRegular()
}

func existsNoLink(name string) bool {
	info, err := os.Lstat(name)
	return err == nil && info.Mode()&os.ModeSymlink == 0
}

// DataReport classifies install data.
type DataReport struct {
	Profile, Cache, Personal []string
}

// ClassifyInstallData mirrors profiles.classify_install_data.
func ClassifyInstallData(target string) DataReport {
	var report DataReport
	for _, name := range profileFiles {
		if regular(filepath.Join(target, filepath.FromSlash(name))) {
			report.Profile = append(report.Profile, name)
		}
	}
	if info, err := os.Lstat(target); err == nil && info.IsDir() {
		roots := append([]string{}, profileRoots...)
		sort.Strings(roots)
		for _, root := range roots {
			dir := filepath.Join(target, root)
			if info, err := os.Lstat(dir); err != nil || !info.IsDir() {
				continue
			}
			entries, _ := os.ReadDir(dir)
			names := make([]string, len(entries))
			for index, entry := range entries {
				names[index] = entry.Name()
			}
			sort.Slice(names, func(a, b int) bool { return lessCodepoints(names[a], names[b]) })
			for _, name := range names {
				relative := root + "/" + name
				if !contains(report.Profile, relative) && IsUserProfilePath(relative) && regular(filepath.Join(dir, name)) {
					report.Profile = append(report.Profile, relative)
				}
			}
		}
	}
	for _, name := range cacheRoots {
		if existsNoLink(filepath.Join(target, filepath.FromSlash(name))) {
			report.Cache = append(report.Cache, name)
		}
	}
	for _, name := range personalRoot {
		if existsNoLink(filepath.Join(target, filepath.FromSlash(name))) {
			report.Personal = append(report.Personal, name)
		}
	}
	return report
}

func contains(values []string, wanted string) bool {
	for _, value := range values {
		if value == wanted {
			return true
		}
	}
	return false
}

func lessCodepoints(a, b string) bool {
	ra, rb := []rune(a), []rune(b)
	for index := 0; index < len(ra) && index < len(rb); index++ {
		if ra[index] != rb[index] {
			return ra[index] < rb[index]
		}
	}
	return len(ra) < len(rb)
}

// RenderProfile mirrors render_profile_report.
func RenderProfile(report DataReport) string {
	lines := []string{"x86QW profile — owner-only", ""}
	for _, group := range []struct {
		key   string
		items []string
	}{{"profile", report.Profile}, {"cache", report.Cache}, {"personal", report.Personal}} {
		lines = append(lines, fmt.Sprintf("%s: %d", group.key, len(group.items)))
		for _, item := range group.items {
			lines = append(lines, "  "+item)
		}
	}
	return strings.Join(lines, "\n") + "\n"
}

// ResolveBundleDestination mirrors doctor.resolve_bundle_destination: a
// bundle is never written inside the installation.
func ResolveBundleDestination(raw, target string) (string, error) {
	requested := raw
	if strings.HasPrefix(requested, "~") {
		home, _ := os.UserHomeDir()
		requested = home + requested[1:]
	}
	destination, err := filepath.Abs(requested)
	if err != nil {
		return "", err
	}
	destination = filepath.Join(resolve(filepath.Dir(destination)), filepath.Base(destination))
	root := resolve(target)
	if !inside(destination, root) {
		return destination, nil
	}
	if filepath.Dir(raw) != "." {
		return "", errors.New("o bundle não pode ser gravado dentro da instalação")
	}
	home, _ := os.UserHomeDir()
	fallback := filepath.Join(resolve(home), filepath.Base(raw))
	if !inside(fallback, root) {
		return fallback, nil
	}
	return "", errors.New("o bundle não pode ser gravado dentro da instalação")
}

// resolve makes name absolute and follows the links in as much of it as
// exists. A path that does not exist yet must still compare equal to the
// same place reached through a link (on macOS, /var is /private/var), or
// a bundle could be written inside the installation.
func resolve(name string) string {
	absolute, _ := filepath.Abs(name)
	if resolved, err := filepath.EvalSymlinks(absolute); err == nil {
		return resolved
	}
	parent := filepath.Dir(absolute)
	if parent == absolute {
		return absolute
	}
	return filepath.Join(resolve(parent), filepath.Base(absolute))
}

// inside reports whether name is root or below it. Rel speaks the OS
// separator, so on Windows a path outside starts with ..\ (and one on
// another drive is an error).
func inside(name, root string) bool {
	relative, err := filepath.Rel(root, name)
	return err == nil && relative != ".." && !strings.HasPrefix(relative, ".."+string(filepath.Separator))
}

// BackupProfile writes a zip of user-owned profile files.
func BackupProfile(target, destination string) (string, error) {
	if _, err := os.Lstat(destination); err == nil {
		return "", errors.New("profile backup destination already exists")
	}
	files := ClassifyInstallData(target).Profile
	var entries []any
	payloads := map[string][]byte{}
	for _, relative := range files {
		payload, err := os.ReadFile(filepath.Join(target, filepath.FromSlash(relative)))
		if err != nil {
			return "", err
		}
		payloads[relative] = payload
		sum := sha256.Sum256(payload)
		entries = append(entries, map[string]any{"path": relative, "sha256": hex.EncodeToString(sum[:]), "size": len(payload)})
	}
	if entries == nil {
		entries = []any{}
	}
	manifest, err := pyjson.Dumps(map[string]any{"format": 1, "kind": "profile", "files": entries}, -1)
	if err != nil {
		return "", err
	}
	if err := os.MkdirAll(filepath.Dir(destination), 0o755); err != nil {
		return "", err
	}
	staging := destination + ".tmp"
	file, err := os.Create(staging)
	if err != nil {
		return "", err
	}
	writer := zip.NewWriter(file)
	add := func(name string, payload []byte) error {
		out, err := writer.CreateHeader(&zip.FileHeader{Name: name, Method: zip.Store})
		if err != nil {
			return err
		}
		_, err = out.Write(payload)
		return err
	}
	err = add("NOTICE.txt", []byte(profileNotice))
	if err == nil {
		err = add("manifest.json", []byte(manifest+"\n"))
	}
	for _, relative := range files {
		if err == nil {
			err = add(relative, payloads[relative])
		}
	}
	if err = errors.Join(err, writer.Close(), file.Close()); err != nil {
		os.Remove(staging)
		return "", err
	}
	return destination, os.Rename(staging, destination)
}

// RestoreProfile restores profile files byte-for-byte, rolling back on failure.
func RestoreProfile(archive, target string) ([]string, error) {
	reader, err := zip.OpenReader(archive)
	if err != nil {
		return nil, err
	}
	defer reader.Close()
	members := map[string]*zip.File{}
	for _, file := range reader.File {
		members[file.Name] = file
	}
	read := func(name string) ([]byte, error) {
		file, ok := members[name]
		if !ok {
			return nil, fmt.Errorf("\"There is no item named %s in the archive\"", pyRepr(name)) // str(KeyError)
		}
		source, err := file.Open()
		if err != nil {
			return nil, err
		}
		defer source.Close()
		return io.ReadAll(source)
	}
	raw, err := read("manifest.json")
	if err != nil {
		return nil, err
	}
	var manifest struct {
		Format int    `json:"format"`
		Kind   string `json:"kind"`
		Files  []struct {
			Path   string `json:"path"`
			SHA256 string `json:"sha256"`
			Size   int    `json:"size"`
		} `json:"files"`
	}
	if err := json.Unmarshal(raw, &manifest); err != nil {
		return nil, err
	}
	if manifest.Kind != "profile" || manifest.Format != 1 {
		return nil, errors.New("profile backup has an invalid manifest")
	}
	planned := map[string]bool{}
	var order []string
	for _, item := range manifest.Files {
		planned[item.Path] = true
		order = append(order, item.Path)
	}
	extra := 0
	for name := range members {
		if name == "NOTICE.txt" || name == "manifest.json" {
			continue
		}
		if !planned[name] {
			return nil, errors.New("profile backup contents diverge from the manifest")
		}
		extra++
	}
	if extra != len(planned) {
		return nil, errors.New("profile backup contents diverge from the manifest")
	}
	payloads := map[string][]byte{}
	for _, item := range manifest.Files {
		if !IsUserProfilePath(item.Path) {
			return nil, fmt.Errorf("profile backup contains a non-profile path: %s", item.Path)
		}
		payload, err := read(item.Path)
		if err != nil {
			return nil, err
		}
		sum := sha256.Sum256(payload)
		if hex.EncodeToString(sum[:]) != item.SHA256 || len(payload) != item.Size {
			return nil, fmt.Errorf("profile backup hash mismatch: %s", item.Path)
		}
		payloads[item.Path] = payload
	}
	originals := map[string][]byte{}
	missing := map[string]bool{}
	var restored []string
	for _, relative := range order {
		destination := filepath.Join(target, filepath.FromSlash(relative))
		if info, err := os.Lstat(destination); err == nil && info.Mode()&os.ModeSymlink != 0 {
			rollbackProfile(target, originals, missing)
			return nil, fmt.Errorf("profile destination is a symlink: %s", relative)
		}
		if data, err := os.ReadFile(destination); err == nil && regular(destination) {
			originals[relative] = data
		} else {
			missing[relative] = true
		}
		if err := os.MkdirAll(filepath.Dir(destination), 0o755); err != nil {
			rollbackProfile(target, originals, missing)
			return nil, err
		}
		staging := destination + ".restore-tmp"
		err := os.WriteFile(staging, payloads[relative], 0o644)
		if err == nil {
			err = os.Rename(staging, destination)
		}
		os.Remove(staging)
		if err != nil {
			rollbackProfile(target, originals, missing)
			return nil, err
		}
		restored = append(restored, relative)
	}
	return restored, nil
}

func rollbackProfile(target string, originals map[string][]byte, missing map[string]bool) {
	for relative, payload := range originals {
		os.WriteFile(filepath.Join(target, filepath.FromSlash(relative)), payload, 0o644)
	}
	for relative := range missing {
		os.Remove(filepath.Join(target, filepath.FromSlash(relative)))
	}
}
