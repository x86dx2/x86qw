package content

import (
	"fmt"
	"html"
	"net/http"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
)

// Public upstream discovery: which files each upstream publishes now.

const (
	discoveryMaxBytes  = 4 * 1024 * 1024
	githubTreeMaxItems = 50000
	nquakeRepository   = "nQuake/distfiles"
	nquakeRef          = "master"
)

var (
	safeGitHubRepository = regexp.MustCompile(`^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9][A-Za-z0-9._-]{0,99}$`)
	safeGitHubRef        = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$`)
	githubAPIHeaders     = map[string]string{
		"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": userAgent,
	}
	releaseTagPath = regexp.MustCompile(`/releases/tag/([^/]+)$`)
	tagPattern     = regexp.MustCompile(`(?is)<(a|meta)\b([^>]*)>`)
	attrPattern    = regexp.MustCompile(`(?s)([^\s"'>/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?`)
)

// Asset is one upstream file the distribution may hold.
type Asset struct {
	Component       string
	URL             string
	Path            string
	ExpectedSize    int64 // 0 = unknown
	Subcomponent    string
	ExpectedSHA256  string
	ExpectedGitSHA1 string
}

type htmlTag struct {
	name  string
	attrs map[string]string
}

// htmlTags extracts <a> and <meta> tags like html.parser with
// convert_charrefs (attribute names lowercased, values unescaped).
func htmlTags(document string) []htmlTag {
	var tags []htmlTag
	for _, match := range tagPattern.FindAllStringSubmatch(document, -1) {
		attrs := map[string]string{}
		for _, attr := range attrPattern.FindAllStringSubmatch(match[2], -1) {
			name := strings.ToLower(attr[1])
			if _, seen := attrs[name]; seen {
				continue
			}
			value := attr[2] + attr[3] + attr[4]
			attrs[name] = html.UnescapeString(value)
		}
		tags = append(tags, htmlTag{strings.ToLower(match[1]), attrs})
	}
	return tags
}

// githubRepositoryName checks an owner/name repository and returns it.
func githubRepositoryName(repository string) (string, error) {
	if safeGitHubRepository.MatchString(repository) {
		return repository, nil
	}
	invalid := fmt.Errorf("the upstream must name a public GitHub repository")
	if repository == "" {
		return "", invalid
	}
	for _, r := range repository {
		if r == '\\' || r < 32 || pyIsSpace(r) {
			return "", invalid
		}
	}
	parsed, err := urlsplit(repository)
	if err == nil {
		err = parsed.port()
	}
	if err != nil {
		return "", invalid
	}
	_, hasUser, _, port, hasPort := parsed.hostinfo()
	if Casefold(parsed.Scheme) != "https" || Casefold(parsed.Hostname()) != "github.com" || hasUser ||
		(hasPort && strings.TrimLeft(port, "0") != "443") || parsed.Query != "" || parsed.Fragment != "" ||
		strings.Contains(parsed.Path, "%") {
		return "", invalid
	}
	path := strings.TrimSuffix(strings.TrimPrefix(parsed.Path, "/"), "/")
	path = strings.TrimSuffix(path, ".git")
	if !safeGitHubRepository.MatchString(path) {
		return "", invalid
	}
	return path, nil
}

// githubHeadRef splits a GitHub ref into its kind and name.
func githubHeadRef(value string) (string, string, error) {
	normalized := strings.TrimPrefix(value, "refs/")
	if !strings.Contains(normalized, "/") {
		normalized = "heads/" + normalized
	}
	invalid := fmt.Errorf("invalid GitHub reference")
	if !safeGitHubRef.MatchString(normalized) || !strings.HasPrefix(normalized, "heads/") || normalized == "heads/" ||
		strings.Contains(normalized, "..") || strings.Contains(normalized, "@{") || strings.Contains(normalized, "//") ||
		strings.HasSuffix(normalized, "/") || strings.HasSuffix(normalized, ".") || strings.HasSuffix(normalized, ".lock") {
		return "", "", invalid
	}
	for _, part := range strings.Split(normalized, "/") {
		if part == "" || part == "." || part == ".." {
			return "", "", invalid
		}
	}
	return normalized, "refs/" + normalized, nil
}

func (p *Project) githubAPIDocument(repository, endpoint string, deadline time.Time, label string) (*Object, error) {
	remaining := time.Until(deadline)
	if remaining <= 0 {
		return nil, fmt.Errorf("the GitHub query exceeded its total time limit")
	}
	response, err := p.Metadata(http.MethodGet, "https://api.github.com/repos/"+repository+"/"+endpoint, discoveryMaxBytes, remaining, githubAPIHeaders)
	if err != nil {
		return nil, err
	}
	value, err := pyjson.Decode(response.Data, pyjson.LastWins)
	document, ok := value.(*Object)
	if err != nil || !ok {
		return nil, fmt.Errorf("the GitHub API returned an invalid %s", label)
	}
	return document, nil
}

func githubRefTarget(document *Object, repository, expectedRef string) (string, error) {
	target, ok := document.Value("object").(*Object)
	invalid := fmt.Errorf("the GitHub API returned an invalid reference")
	if !pyjson.Eq(document.Value("ref"), expectedRef) || !ok || !pyjson.Eq(target.Value("type"), "commit") {
		return "", invalid
	}
	revision, ok := target.Value("sha").(string)
	if !ok || !hex40Pattern.MatchString(revision) {
		return "", invalid
	}
	if !pyjson.Eq(target.Value("url"), "https://api.github.com/repos/"+repository+"/git/commits/"+revision) {
		return "", invalid
	}
	return revision, nil
}

// GitHubRefRevision is the commit a ref of repository points at.
func (p *Project) GitHubRefRevision(repository, ref string) (string, error) {
	name, err := githubRepositoryName(repository)
	if err != nil {
		return "", err
	}
	apiRef, expected, err := githubHeadRef(ref)
	if err != nil {
		return "", err
	}
	document, err := p.githubAPIDocument(name, "git/ref/"+quotePath(apiRef), time.Now().Add(60*time.Second), "GitHub reference")
	if err != nil {
		return "", err
	}
	return githubRefTarget(document, name, expected)
}

// quotePath percent-encodes a URL path, keeping "/".
func quotePath(value string) string { return quote(value, "/") }

// quote percent-encodes value, keeping unreserved characters and safe.
func quote(value, safe string) string {
	const always = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-~"
	var builder strings.Builder
	for _, b := range []byte(value) {
		if strings.IndexByte(always, b) >= 0 || strings.IndexByte(safe, b) >= 0 {
			builder.WriteByte(b)
		} else {
			fmt.Fprintf(&builder, "%%%02X", b)
		}
	}
	return builder.String()
}

// GitTreeEntry is one blob of a recursive GitHub tree.
type GitTreeEntry struct {
	Path string
	SHA1 string
	Size int64
}

// GitHubRecursiveTree is the commit and full file tree of a branch.
func (p *Project) GitHubRecursiveTree(repository, branch string) (string, []GitTreeEntry, error) {
	name, err := githubRepositoryName(repository)
	if err != nil {
		return "", nil, err
	}
	apiRef, expected, err := githubHeadRef(branch)
	if err != nil {
		return "", nil, err
	}
	deadline := time.Now().Add(60 * time.Second)
	reference, err := p.githubAPIDocument(name, "git/ref/"+quotePath(apiRef), deadline, "GitHub reference")
	if err != nil {
		return "", nil, err
	}
	revision, err := githubRefTarget(reference, name, expected)
	if err != nil {
		return "", nil, err
	}
	commit, err := p.githubAPIDocument(name, "git/commits/"+revision, deadline, "GitHub commit")
	if err != nil {
		return "", nil, err
	}
	tree, ok := commit.Value("tree").(*Object)
	treeSHA, _ := pyjson.Get(commit, "tree", "sha").(string)
	if !pyjson.Eq(commit.Value("sha"), revision) || !ok || !hex40Pattern.MatchString(treeSHA) ||
		!pyjson.Eq(tree.Value("url"), "https://api.github.com/repos/"+name+"/git/trees/"+treeSHA) {
		return "", nil, fmt.Errorf("the GitHub API returned an invalid commit")
	}
	document, err := p.githubAPIDocument(name, "git/trees/"+treeSHA+"?recursive=1", deadline, "GitHub recursive tree")
	if err != nil {
		return "", nil, err
	}
	raw, ok := document.Value("tree").([]any)
	if !pyjson.Eq(document.Value("sha"), treeSHA) || document.Value("truncated") != false || !ok || len(raw) > githubTreeMaxItems {
		return "", nil, fmt.Errorf("the GitHub API returned an incomplete or invalid tree")
	}
	var entries []GitTreeEntry
	seen := map[string]bool{}
	for _, item := range raw {
		entry, ok := item.(*Object)
		if !ok {
			return "", nil, fmt.Errorf("the GitHub API returned an invalid tree entry")
		}
		path, err := ValidatePortableRelativePath(entry.Value("path"), "GitHub tree path")
		if err != nil {
			return "", nil, err
		}
		kind, _ := entry.Value("type").(string)
		sha, shaOK := entry.Value("sha").(string)
		modes := map[string]map[string]bool{"blob": setOf("100644", "100755", "120000"), "tree": setOf("040000")}[kind]
		if modes == nil || !inSet(entry.Value("mode"), modes) || !shaOK || !hex40Pattern.MatchString(sha) {
			return "", nil, fmt.Errorf("the GitHub API returned an invalid tree entry")
		}
		key := Casefold(path)
		if seen[key] {
			return "", nil, fmt.Errorf("the GitHub API returned duplicate tree paths")
		}
		seen[key] = true
		size := entry.Value("size")
		if kind == "blob" {
			low, _ := pyjson.IntCmp(size, 0)
			high, _ := pyjson.IntCmp(size, MaxArtifactBytes)
			if !pyjson.IsExactInt(size) || low < 0 || high > 0 {
				return "", nil, fmt.Errorf("the GitHub API returned an invalid blob size")
			}
			// A symbolic link is never consumed; a consumed file that turns
			// into one disappears from the entries and shows up as a change.
			if pyjson.Eq(entry.Value("mode"), "120000") {
				continue
			}
			value, _ := size.(pyjson.Number).Int()
			entries = append(entries, GitTreeEntry{path, sha, value})
		} else if size != nil {
			return "", nil, fmt.Errorf("the GitHub API returned a size on a non-blob entry")
		}
	}
	return revision, entries, nil
}

// GitHubLatestRelease is the tag of the latest release (it follows the
// redirect).
func (p *Project) GitHubLatestRelease(repository string) (string, error) {
	name, err := githubRepositoryName(repository)
	if err != nil {
		return "", err
	}
	response, err := p.Metadata(http.MethodHead, "https://github.com/"+name+"/releases/latest", discoveryMaxBytes, 60*time.Second, nil)
	if err != nil {
		return "", err
	}
	parsed, _ := urlsplit(response.URL)
	match := releaseTagPath.FindStringSubmatch(parsed.Path)
	if match == nil {
		return "", fmt.Errorf("the upstream has not published a latest release: %s", name)
	}
	return unquote(match[1]), nil
}

// GitHubCommitRevision expands an abbreviated commit to its full hash.
func (p *Project) GitHubCommitRevision(repository, abbreviation string) (string, error) {
	name, err := githubRepositoryName(repository)
	if err != nil {
		return "", err
	}
	if !regexp.MustCompile(`^[0-9a-f]{7,40}$`).MatchString(abbreviation) {
		return "", fmt.Errorf("invalid abbreviated commit: %s", abbreviation)
	}
	response, err := p.Metadata(http.MethodGet, "https://github.com/"+name+"/commit/"+abbreviation, discoveryMaxBytes, 60*time.Second, nil)
	if err != nil {
		return "", err
	}
	expected := regexp.MustCompile(`^/` + regexp.QuoteMeta(name) + `/commit/([0-9a-f]{40})$`)
	revisions := map[string]bool{}
	for _, tag := range htmlTags(strings.ToValidUTF8(string(response.Data), "�")) {
		if tag.name != "meta" || tag.attrs["property"] != "og:url" {
			continue
		}
		content, ok := tag.attrs["content"]
		if !ok {
			continue
		}
		parsed, _ := urlsplit(content)
		if parsed.Scheme != "" && (Casefold(parsed.Scheme) != "https" || Casefold(parsed.Hostname()) != "github.com") {
			continue
		}
		if match := expected.FindStringSubmatch(parsed.Path); match != nil {
			revisions[match[1]] = true
		}
	}
	if len(revisions) != 1 {
		return "", fmt.Errorf("could not resolve commit %s publicly: %s", abbreviation, name)
	}
	for revision := range revisions {
		return revision, nil
	}
	return "", nil
}

// RemoteContentLength is the size a URL announces.
func (p *Project) RemoteContentLength(url string) (int64, error) {
	response, err := p.Metadata(http.MethodHead, url, MaxArtifactBytes, 60*time.Second, nil)
	if err != nil {
		return 0, err
	}
	length := response.Header.Get("Content-Length")
	value, err := strconv.ParseInt(length, 10, 64)
	if !isDigits(length) || err != nil || value <= 0 {
		return 0, fmt.Errorf("the upstream did not report the artifact size: %s", safeURLForLog(url))
	}
	return value, nil
}

func (p *Project) links(url string) ([]string, error) {
	response, err := p.Metadata(http.MethodGet, url, discoveryMaxBytes, 90*time.Second, nil)
	if err != nil {
		return nil, err
	}
	var result []string
	for _, tag := range htmlTags(strings.ToValidUTF8(string(response.Data), "�")) {
		if href := tag.attrs["href"]; tag.name == "a" && href != "" {
			result = append(result, href)
		}
	}
	return result, nil
}

func safeFilename(href string) string {
	parsed, _ := urlsplit(href)
	name := posixName(unquote(parsed.Path))
	if name == "" || name == "." || name == ".." || strings.ContainsAny(name, "/\\\x00") {
		return ""
	}
	return name
}

var (
	ezquakeReleaseRepository = "QW-Group/ezquake-source"
	ezquakeReleaseAssets     = []string{"ezQuake-macOS-universal.zip", "ezQuake-linux-x86_64.zip", "ezQuake-windows-x64.zip"}
	nightlySources           = []struct {
		platform, root string
		pattern        *regexp.Regexp
	}{
		{"macos-universal", "https://builds.quakeworld.nu/ezquake/snapshots/macOS/universal/", nightlyPatterns[0]},
		{"linux-x86_64", "https://builds.quakeworld.nu/ezquake/snapshots/linux/x86_64/", nightlyPatterns[1]},
		{"windows-x64", "https://builds.quakeworld.nu/ezquake/snapshots/windows/x64/", nightlyPatterns[2]},
	}
)

func releaseVariant(name string) (string, error) {
	lowered := Casefold(name)
	for _, variant := range []string{"macos-universal", "macos-arm64", "linux-x86_64", "linux-amd64", "windows-x64", "windows-amd64"} {
		if strings.Contains(lowered, variant) {
			return variant, nil
		}
	}
	return "", fmt.Errorf("release asset has no supported runtime variant: %s", name)
}

// DiscoverAssets lists the files each upstream publishes now.
// Unavailable maps a network source ("ezquake" or "nquake") to the error
// that kept it from being consulted.
type Unavailable map[string]string

// DiscoverAssets lists every distribution asset. A network source that
// fails is reported in Unavailable instead of failing the whole discovery;
// its components keep their current files.
func (p *Project) DiscoverAssets() ([]Asset, Unavailable, error) {
	unavailable := Unavailable{}
	var discovered []Asset
	if ezquake, err := p.discoverEZQuake(); err != nil {
		unavailable["ezquake"] = err.Error()
	} else {
		discovered = append(discovered, ezquake...)
	}
	if nquake, err := p.discoverNQuake(); err != nil {
		unavailable["nquake"] = err.Error()
	} else {
		discovered = append(discovered, nquake...)
	}
	local, err := p.discoverLocalAssets()
	if err != nil {
		return nil, nil, err
	}
	discovered = append(discovered, local...)
	unique := map[string]Asset{}
	for _, asset := range discovered {
		path := asset.Path
		if err := p.RequireComponent(asset.Component, &path); err != nil {
			return nil, nil, err
		}
		key := Casefold(asset.Path)
		existing, ok := unique[key]
		if ok && existing.Path == asset.Path && existing.URL != asset.URL {
			return nil, nil, fmt.Errorf("two URLs resolve to the same distribution path: %s", asset.Path)
		}
		if !ok || asset.Path < existing.Path {
			unique[key] = asset
		}
	}
	result := make([]Asset, 0, len(unique))
	for _, asset := range unique {
		result = append(result, asset)
	}
	sort.Slice(result, func(i, j int) bool { return result[i].Path < result[j].Path })
	return result, unavailable, nil
}

// discoverEZQuake lists the latest stable release and nightly builds.
func (p *Project) discoverEZQuake() ([]Asset, error) {
	var discovered []Asset
	tag, err := p.GitHubLatestRelease(ezquakeReleaseRepository)
	if err != nil {
		return nil, err
	}
	for _, name := range ezquakeReleaseAssets {
		variant, err := releaseVariant(name)
		if err != nil {
			return nil, err
		}
		assetURL := "https://github.com/" + ezquakeReleaseRepository + "/releases/download/" + quote(tag, "") + "/" + name
		size, err := p.RemoteContentLength(assetURL)
		if err != nil {
			return nil, err
		}
		discovered = append(discovered, Asset{Component: "ezquake", URL: assetURL, Path: "clients/ezquake/stable/" + tag + "/" + variant + "/" + name, ExpectedSize: size})
	}
	for _, source := range nightlySources {
		hrefs, err := p.links(source.root)
		if err != nil {
			return nil, err
		}
		names := map[string]bool{}
		for _, href := range hrefs {
			if name := safeFilename(href); name != "" && source.pattern.MatchString(name) {
				names[name] = true
			}
		}
		if len(names) == 0 {
			return nil, fmt.Errorf("no nightly found for %s", source.platform)
		}
		name := sortedKeys(names)[len(names)-1]
		base, _ := url.Parse(source.root)
		reference, _ := url.Parse(name)
		assetURL := base.ResolveReference(reference).String()
		size, err := p.RemoteContentLength(assetURL)
		if err != nil {
			return nil, err
		}
		build, _, _ := strings.Cut(name, "_ez")
		discovered = append(discovered, Asset{Component: "ezquake", URL: assetURL, Path: "clients/ezquake/nightly/" + build + "/" + source.platform + "/" + name, ExpectedSize: size})
	}
	return discovered, nil
}

func (p *Project) discoverNQuake() ([]Asset, error) {
	catalog, err := p.Components()
	if err != nil {
		return nil, err
	}
	used := SourceRoots(catalog, "reference")
	commit, tree, err := p.GitHubRecursiveTree(nquakeRepository, nquakeRef)
	if err != nil {
		return nil, err
	}
	upstream, err := p.fetchNQuakeUpstream(commit, tree)
	if err != nil {
		return nil, err
	}
	p.nquake = upstream
	var assets []Asset
	found := map[string]bool{}
	for _, item := range tree {
		var roots []string
		for _, root := range used {
			if item.Path == root || strings.HasPrefix(item.Path, root+"/") {
				roots = append(roots, root)
			}
		}
		if len(roots) == 0 {
			continue
		}
		for _, root := range roots {
			found[root] = true
		}
		owner, err := ComponentForSource(catalog, item.Path, "reference")
		if err != nil {
			return nil, err
		}
		if owner == "" {
			continue
		}
		assets = append(assets, Asset{
			Component: "nquake", URL: "https://raw.githubusercontent.com/" + nquakeRepository + "/" + commit + "/" + quotePath(item.Path),
			Path: nquakeDir + "/" + item.Path, ExpectedSize: item.Size, Subcomponent: owner,
			ExpectedSHA256: upstream.Files[item.Path].SHA256, ExpectedGitSHA1: item.SHA1,
		})
	}
	for _, root := range used {
		if !found[root] {
			return nil, fmt.Errorf("nQuake used path is missing at %s: %s", commit, root)
		}
	}
	return assets, nil
}

func int64Of(value any) int64 {
	number, _ := value.(pyjson.Number)
	result, _ := number.Int()
	return result
}

// discoverLocalAssets lists the component release assets, the preserved
// sources and the installer assets.
func (p *Project) discoverLocalAssets() ([]Asset, error) {
	var assets []Asset
	releases, err := p.Releases()
	if err != nil {
		return nil, err
	}
	components := releases.Value("components").(*Object)
	for _, identifier := range components.Keys() {
		release := components.Value(identifier).(*Object)
		for _, artifact := range objects(getOr(release, "artifacts", []any{})) {
			path := pyjson.Repr(artifact.Value("distribution_path"))
			owner, err := p.ComponentForDistributionPath(path)
			if err != nil {
				return nil, err
			}
			if owner == "" {
				return nil, fmt.Errorf("release artifact has no distribution owner: %s", path)
			}
			assets = append(assets, Asset{owner, pyjson.Repr(artifact.Value("url")), path, int64Of(artifact.Value("size")), identifier, pyjson.Repr(artifact.Value("sha256")), ""})
		}
	}
	registry, err := p.Upstreams()
	if err != nil {
		return nil, err
	}
	for _, entry := range objects(registry.Value("upstreams")) {
		source := entry.Value("source").(*Object)
		if !inSet(source.Value("status"), preservedStates) || !source.Has("size") {
			continue
		}
		path := pyjson.Repr(source.Value("distribution_path"))
		owner, err := p.ComponentForDistributionPath(path)
		if err != nil {
			return nil, err
		}
		if owner == "" {
			return nil, fmt.Errorf("preserved source has no distribution owner: %s", path)
		}
		assets = append(assets, Asset{owner, pyjson.Repr(source.Value("url")), path, int64Of(source.Value("size")), pyjson.Repr(entry.Value("id")), pyjson.Repr(source.Value("sha256")), ""})
	}
	return assets, nil
}

// PinAssetsFromManifest gives each asset the size and digest the manifest
// records.
func PinAssetsFromManifest(assets []Asset, manifest *Object) ([]Asset, error) {
	files, ok := manifest.Value("files").(*Object)
	if !ok {
		return nil, fmt.Errorf("distribution manifest has no files collection")
	}
	resolved := make([]Asset, 0, len(assets))
	for _, asset := range assets {
		known, ok := files.Value(asset.Path).(*Object)
		if asset.ExpectedSHA256 != "" || !ok {
			resolved = append(resolved, asset)
			continue
		}
		size := known.Value("size")
		digest, digestOK := known.Value("sha256").(string)
		value, _ := size.(pyjson.Number)
		sizeValue, sizeOK := value.Int()
		if !pyjson.Eq(known.Value("url"), asset.URL) || !pyjson.IsExactInt(size) || !sizeOK || sizeValue <= 0 ||
			!digestOK || !sha256Pattern.MatchString(digest) || asset.ExpectedSize != sizeValue {
			resolved = append(resolved, asset)
			continue
		}
		asset.ExpectedSize, asset.ExpectedSHA256 = sizeValue, digest
		resolved = append(resolved, asset)
	}
	return resolved, nil
}

// UnpinnedAssetPaths are the assets without a recorded digest.
func UnpinnedAssetPaths(assets []Asset) []string {
	var paths []string
	for _, asset := range assets {
		if asset.ExpectedSize <= 0 || asset.ExpectedSize > MaxArtifactBytes || !sha256Pattern.MatchString(asset.ExpectedSHA256) {
			paths = append(paths, asset.Path)
		}
	}
	sort.Strings(paths)
	return paths
}
