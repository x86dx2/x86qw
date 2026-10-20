package content

import (
	"bytes"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
)

// Distribution manifest and tree checks (maintenance/tools/sync_distribution.py).

// GitLFSPointerPrefix starts every unhydrated Git LFS pointer.
var GitLFSPointerPrefix = []byte("version https://git-lfs.github.com/spec/v1\n")

var (
	installerPackagePath = regexp.MustCompile(`^installer/packages/[0-9]+\.[0-9]+\.[0-9]+/x86qw-installer-[0-9]+\.[0-9]+\.[0-9]+\.zip$`)
	ezquakeReleaseAsset  = regexp.MustCompile(`^ezQuake-(?:macOS-universal|linux-x86_64|windows-x64)\.zip$`)
	nightlyPatterns      = []*regexp.Regexp{
		regexp.MustCompile(`^[0-9]{8}-[0-9]{6}_[0-9a-f]{7}_ezQuake-macOS-universal\.zip$`),
		regexp.MustCompile(`^[0-9]{8}-[0-9]{6}_[0-9a-f]{7}_ezQuake-x86_64\.AppImage$`),
		regexp.MustCompile(`^[0-9]{8}-[0-9]{6}_[0-9a-f]{7}_ezquake\.exe$`),
	}
)

// allowedUnmanaged are dist files that need no manifest entry.
var allowedUnmanaged = []string{
	"installer/README.md", "installer/VERSION", "installer/docs/installer.md",
	"installer/bin/install.ps1", "installer/bin/install.sh", "installer/bin/x86qw.sh",
	"installer/bin/x86qw.cmd", "installer/assets/x86qw.svg", "installer/assets/x86qw.ico",
	"installer/assets/x86qw.icns", "installer/assets/x86qw-16.png", "installer/assets/x86qw-32.png",
	"installer/assets/x86qw-48.png", "installer/assets/x86qw-64.png", "installer/assets/x86qw-128.png",
	"installer/assets/x86qw-256.png", "installer/assets/x86qw-512.png",
	"game-data/id1/pak0.pak", "game-data/id1/pak1.pak",
}

// LoadManifest mirrors load_manifest.
func LoadManifest(path string) (*Object, error) {
	if !exists(path) {
		manifest := pyjson.NewObject()
		manifest.Set("format", pyjson.Number("1"))
		manifest.Set("project", "x86qw")
		manifest.Set("captured_at", nil)
		manifest.Set("layout", "distribution-v1")
		manifest.Set("files", pyjson.NewObject())
		manifest.Set("repositories", pyjson.NewObject())
		return manifest, nil
	}
	value, err := ReadJSON(path)
	if err != nil {
		return nil, err
	}
	manifest, ok := value.(*Object)
	if !ok || !pyjson.EqInt(manifest.Value("format"), 1) || !pyjson.Eq(manifest.Value("project"), "x86qw") {
		return nil, fmt.Errorf("invalid distribution manifest: %s", path)
	}
	_, filesOK := manifest.Value("files").(*Object)
	_, repositoriesOK := manifest.Value("repositories").(*Object)
	if !filesOK || !repositoriesOK {
		return nil, fmt.Errorf("invalid distribution manifest collections: %s", path)
	}
	if err := validateManifestSizes(manifest.Value("files")); err != nil {
		return nil, err
	}
	return manifest, nil
}

func validateManifestSizes(value any) error {
	files, ok := value.(*Object)
	if !ok {
		return fmt.Errorf("invalid distribution manifest files")
	}
	for _, relative := range files.Keys() {
		metadata, _ := files.Value(relative).(*Object)
		var size any
		if metadata != nil {
			size = metadata.Value("size")
		}
		if !sizeWithin(size, false) {
			return fmt.Errorf("invalid distribution file size: %s", relative)
		}
		parsed, err := validateHTTPSURL(metadata.Value("url"), "distribution file URL")
		if err != nil {
			return fmt.Errorf("invalid distribution file URL: %s", relative)
		}
		if parsed.Query != "" {
			return fmt.Errorf("persistent distribution file URL has a query: %s", relative)
		}
	}
	return nil
}

// DiscoverSnapshot mirrors component_sources.discover_snapshot.
func DiscoverSnapshot(distribution string) (string, string, error) {
	root := filepath.Join(distribution, "distributions", "nquake")
	if !isDir(root) || isSymlink(root) {
		return "", "", fmt.Errorf("canonical nQuake source tree is missing: %s", root)
	}
	entries, err := os.ReadDir(root)
	if err != nil {
		return "", "", err
	}
	var snapshots []string
	for _, entry := range entries {
		path := filepath.Join(root, entry.Name())
		if isDir(path) && !isSymlink(path) {
			snapshots = append(snapshots, path)
		}
	}
	if len(snapshots) != 1 || len(filepath.Base(snapshots[0])) != 40 {
		return "", "", fmt.Errorf("distribution must contain exactly one commit-addressed nQuake source tree")
	}
	return filepath.Base(snapshots[0]), snapshots[0], nil
}

// IsGitLFSPointer mirrors is_git_lfs_pointer.
func IsGitLFSPointer(path string) bool {
	file, err := os.Open(path)
	if err != nil {
		return false
	}
	defer file.Close()
	head := make([]byte, len(GitLFSPointerPrefix))
	n, _ := io.ReadFull(file, head)
	return bytes.Equal(head[:n], GitLFSPointerPrefix)
}

// ConsumedComponent mirrors consumed_component for the canonical inventories.
func (p *Project) ConsumedComponent(path string) (string, error) {
	component, err := p.ComponentForDistributionPath(path)
	if err != nil {
		return "", err
	}
	registry, err := p.Upstreams()
	if err != nil {
		return "", err
	}
	upstream := SourceOwner(registry, path)
	if component == "nquake" {
		parts := purePosix(path).parts
		if len(parts) < 4 || parts[0] != "distributions" || parts[1] != "nquake" || len([]rune(parts[2])) != 40 {
			return "", nil
		}
		catalog, err := p.Components()
		if err != nil {
			return "", err
		}
		owner, err := ComponentForSource(catalog, strings.Join(parts[3:], "/"), "reference")
		if err != nil || owner == "" {
			return "", err
		}
		return component, nil
	}
	if upstream != "" {
		return component, nil
	}
	releases, err := p.Releases()
	if err != nil {
		return "", err
	}
	if component == "installer" {
		if installerPackagePath.MatchString(path) {
			return component, nil
		}
		return "", nil
	}
	artifactComponent, err := ComponentForArtifactPath(releases, path)
	if err != nil {
		return "", err
	}
	if artifactComponent != "" {
		if artifactComponent == component {
			return component, nil
		}
		return "", nil
	}
	name := posixName(path)
	if component == "ezquake" {
		if strings.Contains(path, "/nightly/") {
			for _, pattern := range nightlyPatterns {
				if pattern.MatchString(name) {
					return component, nil
				}
			}
			return "", nil
		}
		if ezquakeReleaseAsset.MatchString(name) {
			return component, nil
		}
	}
	return "", nil
}

// distributionFiles mirrors the `actual` set of verify_distribution.
func distributionFiles(root string) (map[string]bool, error) {
	files, err := rglobFiles(root)
	if err != nil {
		return nil, err
	}
	actual := map[string]bool{}
	for _, relative := range files {
		name := posixName(relative)
		suffix := pathSuffix(name)
		if name == "manifest.json" || name == ".DS_Store" || contains(strings.Split(relative, "/"), "__pycache__") || suffix == ".pyc" || suffix == ".pyo" {
			continue
		}
		actual[relative] = true
	}
	return actual, nil
}

// VerifyDistribution mirrors verify_distribution and returns the number of
// manifest files checked.
func (p *Project) VerifyDistribution(root string, manifest *Object) (int, error) {
	files := manifest.Value("files").(*Object)
	if err := validateManifestSizes(files); err != nil {
		return 0, err
	}
	if !pyjson.Eq(manifest.Value("layout"), "distribution-v1") || !pyjson.Eq(manifest.Value("repositories"), pyjson.NewObject()) {
		return 0, fmt.Errorf("distribution still contains a legacy or repository-oriented layout")
	}
	actual, err := distributionFiles(root)
	if err != nil {
		return 0, err
	}
	var missing []string
	for _, relative := range files.Keys() {
		if !actual[relative] {
			missing = append(missing, relative)
		}
	}
	if len(missing) > 0 {
		sort.Strings(missing)
		return 0, fmt.Errorf("distribution is missing a registered upstream file: %s", missing[0])
	}
	catalog, err := p.Components()
	if err != nil {
		return 0, err
	}
	releases, err := p.Releases()
	if err != nil {
		return 0, err
	}
	allowed := setOf(allowedUnmanaged...)
	for _, component := range objects(catalog.Value("components")) {
		for _, key := range []string{"project_sources", "project_overrides", "project_archive_overrides", "project_inputs"} {
			list, _ := getOr(component, key, []any{}).([]any)
			for _, source := range objects(list) {
				relative := strings.TrimPrefix(purePosix(pyjson.Repr(source.Value("path"))).asPosix(), "dist/")
				allowed[relative] = true
			}
		}
	}
	releaseComponents := releases.Value("components").(*Object)
	for _, identifier := range releaseComponents.Keys() {
		release, _ := releaseComponents.Value(identifier).(*Object)
		for _, artifact := range objects(getOr(release, "artifacts", []any{})) {
			allowed[pyjson.Repr(artifact.Value("distribution_path"))] = true
		}
	}
	var unexpected []string
	for relative := range actual {
		if !files.Has(relative) && !allowed[relative] {
			unexpected = append(unexpected, relative)
		}
	}
	if len(unexpected) > 0 {
		sort.Strings(unexpected)
		return 0, fmt.Errorf("distribution contains a file without an explicit consumer: %s", unexpected[0])
	}
	var installers, current []string
	for _, relative := range files.Keys() {
		metadata, ok := files.Value(relative).(*Object)
		if ok && pyjson.Eq(metadata.Value("component"), "installer") {
			installers = append(installers, relative)
			if pyjson.Eq(metadata.Value("consumer"), "bootstrap:installer") {
				current = append(current, relative)
			}
		}
	}
	if len(installers) > 0 && len(current) != 1 {
		return 0, fmt.Errorf("distribution must register exactly one current installer package")
	}
	if len(installers) > 0 {
		parts := purePosix(current[0]).parts
		if len(parts) != 4 || parts[0] != "installer" || parts[1] != "packages" {
			return 0, fmt.Errorf("current installer package has an invalid distribution path")
		}
		latest := filepath.Join(root, "installer", "packages", "latest")
		link, err := os.Readlink(latest)
		if !isSymlink(latest) || err != nil || link != parts[2] {
			return 0, fmt.Errorf("installer latest pointer must target %s", parts[2])
		}
		if resolve(latest) != resolve(filepath.Join(root, "installer", "packages", parts[2])) {
			return 0, fmt.Errorf("installer latest pointer escapes its package directory")
		}
	}
	keys := files.Keys()
	sort.Strings(keys)
	checked := 0
	for _, relative := range keys {
		component, err := p.ConsumedComponent(relative)
		if err != nil {
			return 0, err
		}
		if component == "" {
			return 0, fmt.Errorf("distribution file has no explicit x86QW consumer: %s", relative)
		}
		metadata, ok := files.Value(relative).(*Object)
		if !ok || (metadata.Value("component") != nil && !pyjson.Eq(metadata.Value("component"), component)) {
			return 0, fmt.Errorf("distribution file has invalid component metadata: %s", relative)
		}
		path := joinPosix(root, relative)
		if IsGitLFSPointer(path) {
			return 0, fmt.Errorf("distribution file is an unhydrated Git LFS pointer: %s; run `git lfs pull --include='%s'`", relative, relative)
		}
		info, err := os.Stat(path)
		if err != nil {
			return 0, err
		}
		if isSymlink(path) || !pyjson.EqInt(metadata.Value("size"), info.Size()) {
			return 0, fmt.Errorf("distribution file failed integrity verification: %s", relative)
		}
		digest, err := fileSHA256(path)
		if err != nil {
			return 0, err
		}
		if !pyjson.Eq(digest, metadata.Value("sha256")) {
			return 0, fmt.Errorf("distribution file failed integrity verification: %s", relative)
		}
		checked++
	}
	return checked, nil
}

// resolve mirrors Path.resolve() (non-strict).
func resolve(path string) string {
	absolute, _ := filepath.Abs(path)
	if resolved, err := filepath.EvalSymlinks(absolute); err == nil {
		return resolved
	}
	// Resolve the longest existing prefix, keeping the missing tail.
	dir, tail := filepath.Split(absolute)
	if dir == absolute || dir == "" {
		return absolute
	}
	return filepath.Join(resolve(filepath.Clean(dir)), tail)
}
