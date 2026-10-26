package content

import (
	"fmt"
	"io"
	"net/http"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"time"

	"github.com/x86dx2/x86qw/go/internal/archive"
	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
)

// Mirror publication: x86qw-content publish and commit.

const githubAPIMaxBytes = 4 * 1024 * 1024

var githubRepositoryPattern = regexp.MustCompile(`^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$`)

// githubHeaders are the GitHub API headers, with the token from GH_TOKEN or
// GITHUB_TOKEN.
func (p *Project) githubHeaders() (map[string]string, error) {
	headers := map[string]string{
		"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": userAgent,
	}
	token := p.env("GH_TOKEN")
	if token == "" {
		token = p.env("GITHUB_TOKEN")
	}
	if token == "" {
		if output, err := p.command(nil, "gh", "auth", "token"); err == nil {
			token = strings.TrimSpace(string(output))
		}
	}
	if token != "" {
		if len(token) > 4096 || strings.IndexFunc(token, func(r rune) bool { return r < 33 || r == 127 }) >= 0 {
			return nil, managerError("invalid GitHub token")
		}
		headers["Authorization"] = "Bearer " + token
	}
	return headers, nil
}

// githubAPIObject fetches a GitHub API object (nil on 404).
func (p *Project) githubAPIObject(repository, endpoint, label string) (*Object, error) {
	parts := strings.Split(repository, "/")
	if len(parts) != 2 || parts[0] == "" || parts[1] == "" || parts[0] == "." || parts[0] == ".." ||
		parts[1] == "." || parts[1] == ".." || !githubRepositoryPattern.MatchString(repository) {
		return nil, managerError("invalid GitHub repository: %s", pyRepr(repository))
	}
	headers, err := p.githubHeaders()
	if err != nil {
		return nil, err
	}
	response, err := p.Metadata(http.MethodGet, "https://api.github.com/repos/"+repository+"/"+endpoint, githubAPIMaxBytes, 60*time.Second, headers)
	if IsHTTPStatus(err, 404) {
		return nil, nil
	}
	if status, ok := err.(*HTTPError); ok {
		return nil, managerError("failed to query %s: HTTP %d", label, status.Status)
	}
	if err != nil {
		return nil, managerError("failed to query %s: %v", label, err)
	}
	value, err := pyjson.Decode(response.Data, pyjson.LastWins)
	if err != nil {
		return nil, managerError("invalid JSON response when querying %s", label)
	}
	document, _ := value.(*Object)
	return document, nil
}

func (p *Project) githubRelease(repository, tag string) (*Object, error) {
	return p.githubAPIObject(repository, "releases/tags/"+quote(tag, ""), "GitHub release "+tag)
}

func (p *Project) githubLatestReleaseTag(repository string) (string, error) {
	release, err := p.githubAPIObject(repository, "releases/latest", "GitHub latest of "+repository)
	if err != nil || release == nil {
		return "", err
	}
	tag, _ := release.Value("tag_name").(string)
	return tag, nil
}

type mirrorArtifact struct {
	pkg     *Object
	primary string
}

func remoteAssets(release *Object) map[string]*Object {
	result := map[string]*Object{}
	if release == nil {
		return result
	}
	for _, item := range objects(release.Value("assets")) {
		if name, ok := item.Value("name").(string); ok {
			result[name] = item
		}
	}
	return result
}

func (p *Project) assetFingerprint(remote *Object, artifact mirrorArtifact) (bool, error) {
	size, digest := artifact.pkg.Value("size"), pyjson.Repr(artifact.pkg.Value("sha256"))
	if text, ok := remote.Value("digest").(string); ok {
		return pyjson.Eq(remote.Value("size"), size) && strings.TrimPrefix(text, "sha256:") == digest, nil
	}
	gotSize, gotDigest, found, err := p.RemoteFingerprint(artifact.primary, int64Of(size), digest)
	if err != nil {
		return false, err
	}
	return found && pyjson.EqInt(size, gotSize) && gotDigest == digest, nil
}

// PublishGitHub uploads the catalog's artifacts to their GitHub releases.
func (p *Project) PublishGitHub(out io.Writer, catalog *Object, dryRun bool) error {
	type key struct{ repository, tag string }
	groups := map[key][]mirrorArtifact{}
	var order []key
	for _, pkg := range objects(catalog.Value("packages")) {
		if IsCLIPackage(pkg) {
			continue
		}
		filename := pyjson.Repr(pkg.Value("filename"))
		found := false
		for _, url := range objectsOrStrings(pkg.Value("urls")) {
			repository, tag, err := GitHubReleaseCoordinates(url, filename)
			if err != nil {
				continue
			}
			group := key{repository, tag}
			if _, seen := groups[group]; !seen {
				order = append(order, group)
			}
			groups[group] = append(groups[group], mirrorArtifact{pkg, url})
			found = true
			break
		}
		if !found {
			return managerError("package without a GitHub mirror: %s", filename)
		}
	}
	latest := map[string]string{}
	for _, group := range order {
		if _, ok := latest[group.repository]; ok {
			continue
		}
		tag, err := p.githubLatestReleaseTag(group.repository)
		if err != nil {
			return err
		}
		latest[group.repository] = tag
	}
	for _, group := range order {
		artifacts := groups[group]
		titles, notes, latestValues := map[string]bool{}, map[string]bool{}, map[bool]bool{}
		for _, artifact := range artifacts {
			pkg := artifact.pkg
			titles[pyjson.Repr(getOr(pkg, "mirror_title", getOr(pkg, "release_title", "x86QW Content · "+group.tag)))] = true
			notes[pyjson.Repr(getOr(pkg, "mirror_notes", "Pacotes versionados da distribuição x86QW."))] = true
			latestValues[pyjson.Truthy(getOr(pkg, "mirror_latest", false))] = true
		}
		if len(titles) != 1 || len(notes) != 1 || len(latestValues) != 1 {
			return managerError("release %s has conflicting mirror metadata", group.tag)
		}
		title, body := sortedKeys(titles)[0], sortedKeys(notes)[0]
		makeLatest := latestValues[true]
		release, err := p.githubRelease(group.repository, group.tag)
		if err != nil {
			return err
		}
		if release == nil {
			fmt.Fprintf(out, "[GITHUB %s] create release %s (%s)\n", group.repository, group.tag, title)
			if !dryRun {
				flag := "--latest=false"
				if makeLatest {
					flag = "--latest"
				}
				if _, err := p.command(nil, "gh", "release", "create", group.tag, "--repo", group.repository, "--title", title, "--notes", body, flag); err != nil {
					return err
				}
				if release, err = p.githubRelease(group.repository, group.tag); err != nil {
					return err
				}
				if makeLatest {
					latest[group.repository] = group.tag
				}
			}
		} else {
			isLatest := latest[group.repository] == group.tag
			if !pyjson.Eq(release.Value("name"), title) || !pyjson.Eq(release.Value("body"), body) || isLatest != makeLatest {
				fmt.Fprintf(out, "[GITHUB %s] update metadata of %s\n", group.repository, group.tag)
				if !dryRun {
					value := "false"
					if makeLatest {
						value = "true"
					}
					if _, err := p.command(nil, "gh", "api", "--method", "PATCH",
						"repos/"+group.repository+"/releases/"+pyjson.Repr(release.Value("id")),
						"-f", "name="+title, "-f", "body="+body, "-f", "make_latest="+value); err != nil {
						return err
					}
					switch {
					case makeLatest:
						latest[group.repository] = group.tag
					case isLatest:
						latest[group.repository] = ""
					}
				}
			}
		}
		if release == nil && !dryRun {
			return managerError("GitHub release %s did not become available after creation", group.tag)
		}
		assets := remoteAssets(release)
		for _, artifact := range artifacts {
			filename := pyjson.Repr(artifact.pkg.Value("filename"))
			if remote, ok := assets[filename]; ok {
				same, err := p.assetFingerprint(remote, artifact)
				if err != nil {
					return err
				}
				if !same {
					return managerError("immutable GitHub asset differs from the catalog: %s", filename)
				}
				fmt.Fprintf(out, "[GITHUB %s] verified %s\n", group.repository, filename)
				continue
			}
			fmt.Fprintf(out, "[GITHUB %s] upload %s\n", group.repository, filename)
			if dryRun {
				continue
			}
			path, err := p.LocalArtifact(artifact.pkg)
			if err != nil {
				return err
			}
			if _, err := p.command(nil, "gh", "release", "upload", group.tag, path, "--repo", group.repository); err != nil {
				return err
			}
			if release, err = p.githubRelease(group.repository, group.tag); err != nil {
				return err
			}
			if release == nil {
				return managerError("GitHub release %s disappeared after the upload", group.tag)
			}
			assets = remoteAssets(release)
			remote, ok := assets[filename]
			if !ok {
				return managerError("GitHub asset did not appear after the upload: %s", filename)
			}
			same, err := p.assetFingerprint(remote, artifact)
			if err != nil {
				return err
			}
			if !same {
				return managerError("uploaded GitHub asset differs from the catalog: %s", filename)
			}
		}
	}
	return nil
}

// LocalArtifact is a package's built artifact, checked against the
// catalog.
func (p *Project) LocalArtifact(pkg *Object) (string, error) {
	filename := pyjson.Repr(pkg.Value("filename"))
	var path string
	if relative, ok := pkg.Value("distribution_path").(string); ok {
		path = joinPosix(p.Path(DistDir), relative)
	} else if pkg.Value("channel") == "content" {
		var matches []string
		files, _ := rglobFiles(p.Path(BuildsDir))
		for _, relative := range files {
			candidate := joinPosix(p.Path(BuildsDir), relative)
			if posixName(relative) == filename && !isSymlink(candidate) {
				matches = append(matches, candidate)
			}
		}
		if len(matches) != 1 {
			return "", fmt.Errorf("expected one temporary build artifact for %s, found %d", filename, len(matches))
		}
		path = matches[0]
	} else {
		return "", fmt.Errorf("catalog package has no local distribution artifact: %s", filename)
	}
	if !isFile(path) || isSymlink(path) {
		return "", fmt.Errorf("distribution artifact is missing: %s", path)
	}
	digest, err := archive.FileSHA256(path)
	if err != nil {
		return "", err
	}
	info, _ := filepath.Abs(path)
	if size, _ := fileSize(path); !pyjson.EqInt(pkg.Value("size"), size) {
		return "", fmt.Errorf("local artifact size differs from catalog: %s", info)
	}
	if !pyjson.Eq(digest, pkg.Value("sha256")) {
		return "", fmt.Errorf("local artifact SHA-256 differs from catalog: %s", info)
	}
	return path, nil
}

// PublishGitLab uploads the artifacts to the GitLab package registry and
// records the mirror URLs in the catalog.
func (p *Project) PublishGitLab(out io.Writer, catalogPath string) error {
	catalog, err := LoadJSONObject(catalogPath)
	if err != nil {
		return err
	}
	if _, err := p.ValidateCatalog(catalog); err != nil {
		return err
	}
	packages := objects(catalog.Value("packages"))
	verified, changed := 0, false
	for index, pkg := range packages {
		if IsCLIPackage(pkg) {
			continue
		}
		url := ArtifactURL(pkg)
		size, digest := int64Of(pkg.Value("size")), pyjson.Repr(pkg.Value("sha256"))
		filename := pyjson.Repr(pkg.Value("filename"))
		gotSize, gotDigest, found, err := p.RemoteFingerprint(url, size, digest)
		if err != nil {
			return err
		}
		if !found {
			fmt.Fprintf(out, "[%d/%d] uploading %s...\n", index+1, len(packages), filename)
			path, err := p.LocalArtifact(pkg)
			if err != nil {
				return err
			}
			if err := p.uploadGitLab(path, url); err != nil {
				return err
			}
			if gotSize, gotDigest, found, err = p.RemoteFingerprint(url, size, digest); err != nil {
				return err
			}
		}
		if !found || gotSize != size || gotDigest != digest {
			return fmt.Errorf("GitLab mirror differs from catalog: %s", filename)
		}
		urls, _ := pkg.Value("urls").([]any)
		if !listContains(urls, url) {
			pkg.Set("urls", append(urls, url))
			changed = true
		}
		verified++
		fmt.Fprintf(out, "[%d/%d] verified %s\n", index+1, len(packages), filename)
	}
	if !changed {
		fmt.Fprintf(out, "catalog already had the %d GitLab mirrors\n", verified)
		return nil
	}
	catalog.Set("generated_at", utcNow())
	if _, err := p.ValidateCatalog(catalog); err != nil {
		return err
	}
	if err := WriteJSON(catalogPath, catalog); err != nil {
		return err
	}
	fmt.Fprintf(out, "catalog updated with %d GitLab mirrors\n", verified)
	return nil
}

func (p *Project) uploadGitLab(path, url string) error {
	token := p.env("GLAB_TOKEN")
	if token == "" {
		token = p.env("GITLAB_TOKEN")
	}
	if token == "" {
		return fmt.Errorf("GitLab publication token is missing")
	}
	if strings.ContainsAny(token, "\r\n\x00") {
		return fmt.Errorf("GitLab publication token contains an invalid control character")
	}
	headers := map[string]string{"Authorization": "Bearer " + token}
	for _, prefix := range []string{"glpat-", "gldt-", "glrt-", "glptt-"} {
		if strings.HasPrefix(token, prefix) {
			headers = map[string]string{"PRIVATE-TOKEN": token}
		}
	}
	payload, err := readFile(path)
	if err != nil {
		return err
	}
	return p.Put(url, headers, payload)
}

// Publish is `x86qw-content publish`.
func (p *Project) Publish(out io.Writer, githubOnly, gitlabOnly, dryRun bool) error {
	catalog, err := LoadJSONObject(p.Path(CatalogPath))
	if err != nil {
		return err
	}
	if _, err := p.ValidateCatalog(catalog); err != nil {
		return err
	}
	if !gitlabOnly {
		if err := p.PublishGitHub(out, catalog, dryRun); err != nil {
			return err
		}
	}
	if !githubOnly {
		if dryRun {
			fmt.Fprintf(out, "[GITLAB] verify/publish %d artifact(s)\n", len(objects(catalog.Value("packages"))))
		} else if err := p.PublishGitLab(out, p.Path(CatalogPath)); err != nil {
			return err
		}
	}
	if dryRun {
		fmt.Fprintln(out, "[OK] Publication dry run finished.")
	} else {
		fmt.Fprintln(out, "[OK] Publication finished.")
	}
	return nil
}

// Commit is `x86qw-content commit`.
func (p *Project) Commit(out io.Writer, message string, push bool) error {
	status, err := p.command(nil, "git", "status", "--porcelain")
	if err != nil {
		return err
	}
	if strings.TrimSpace(string(status)) == "" {
		fmt.Fprintln(out, "[OK] No changes to record in Git.")
		return nil
	}
	// The pathspec keeps whatever else the maintainer staged out of the commit.
	paths := []string{"dist", "maintenance/inventory", "maintenance/recipes", CatalogPath}
	if _, err := p.command(nil, "git", append([]string{"add"}, paths...)...); err != nil {
		return err
	}
	staged, err := p.command(nil, "git", append([]string{"diff", "--cached", "--name-only", "--"}, paths...)...)
	if err != nil {
		return err
	}
	if strings.TrimSpace(string(staged)) == "" {
		return managerError("no distribution changes to commit")
	}
	if message == "" {
		message = "chore(distribution): update preserved upstreams"
	}
	if _, err := p.command(nil, "git", append([]string{"commit", "-m", message, "--"}, paths...)...); err != nil {
		return err
	}
	if push {
		if _, err := p.command(nil, "git", "push", "origin", "HEAD"); err != nil {
			return err
		}
		remotes, err := p.command(nil, "git", "remote")
		if err != nil {
			return err
		}
		if slices.Contains(strings.Fields(string(remotes)), "gitlab") {
			if _, err := p.command(nil, "git", "push", "gitlab", "HEAD"); err != nil {
				return err
			}
		}
	}
	fmt.Fprintln(out, "[OK] Distribution changes recorded in Git.")
	return nil
}
