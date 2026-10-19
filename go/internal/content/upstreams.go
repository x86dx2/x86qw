package content

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"regexp"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
)

// Upstream and preserved-source registry (maintenance/tools/upstreams.py).

var (
	upstreamID        = regexp.MustCompile(`^[a-z0-9]+(?:-[a-z0-9]+)*$`)
	sourceStates      = setOf("complete", "partial", "unavailable", "not-applicable")
	buildStates       = setOf("reproducible", "documented", "unverified", "unavailable", "not-applicable")
	updateStrategies  = setOf("github-release", "git-ref", "ezquake-nightly", "artifact-fingerprint", "fixed-release", "manual")
	preservedStates   = setOf("complete", "partial")
	recipeBuildStates = setOf("reproducible", "documented", "unverified")
)

func persistentUpstreamURL(value any, label, identifier string) error {
	parsed, err := validateHTTPSURL(value, label)
	if err != nil {
		return fmt.Errorf("invalid persistent %s: %s", label, identifier)
	}
	if parsed.Query != "" {
		return fmt.Errorf("persistent %s must not contain a query: %s", label, identifier)
	}
	return nil
}

// Upstreams mirrors load_upstreams(UPSTREAMS).
func (p *Project) Upstreams() (*Object, error) {
	if !p.upstreamsDone {
		p.upstreamsDone = true
		path := p.Path(UpstreamsPath)
		value, err := ReadJSON(path)
		if err != nil {
			p.upstreamsErr = fmt.Errorf("cannot read upstream registry: %s", path)
		} else if err := ValidateUpstreams(value); err != nil {
			p.upstreamsErr = err
		} else {
			p.upstreams = value.(*Object)
		}
	}
	return p.upstreams, p.upstreamsErr
}

// ValidateUpstreams mirrors validate_upstreams.
func ValidateUpstreams(value any) error {
	registry, ok := value.(*Object)
	if !ok || !pyjson.EqInt(registry.Value("format"), 1) || !pyjson.Eq(registry.Value("project"), "x86qw") {
		return fmt.Errorf("invalid upstream registry identity")
	}
	entries, ok := registry.Value("upstreams").([]any)
	if !ok || len(entries) == 0 {
		return fmt.Errorf("upstream registry is empty")
	}
	identifiers, paths := map[string]bool{}, map[string]bool{}
	for _, raw := range entries {
		entry, ok := raw.(*Object)
		if !ok {
			return fmt.Errorf("invalid upstream entry")
		}
		identifier, ok := entry.Value("id").(string)
		if !ok || !upstreamID.MatchString(identifier) || identifiers[identifier] {
			return fmt.Errorf("invalid or duplicate upstream id: %s", reprScalar(entry.Value("id")))
		}
		identifiers[identifier] = true
		for _, field := range []string{"name", "kind", "version", "release_url"} {
			if !pyjson.NonEmptyStr(entry.Value(field)) {
				return fmt.Errorf("upstream lacks metadata: %s", identifier)
			}
		}
		if err := persistentUpstreamURL(entry.Value("release_url"), "upstream release URL", identifier); err != nil {
			return err
		}
		update, ok := entry.Value("update").(*Object)
		if !ok || !inSet(update.Value("strategy"), updateStrategies) {
			return fmt.Errorf("invalid update strategy: %s", identifier)
		}
		if update.Value("strategy") == "git-ref" {
			if err := persistentUpstreamURL(update.Value("repository"), "Git repository URL", identifier); err != nil {
				return err
			}
		}
		source, ok := entry.Value("source").(*Object)
		if !ok || !inSet(source.Value("status"), sourceStates) {
			return fmt.Errorf("invalid source status: %s", identifier)
		}
		if preservedStates[source.Value("status").(string)] {
			if err := persistentUpstreamURL(source.Value("url"), "preserved source URL", identifier); err != nil {
				return err
			}
			distributionPath, err := safePath(source.Value("distribution_path"), "source distribution path")
			if err != nil {
				return err
			}
			if paths[distributionPath] {
				return fmt.Errorf("duplicate source distribution path: %s", distributionPath)
			}
			paths[distributionPath] = true
			if (source.Has("size") || source.Has("sha256")) &&
				(!validSize(source.Value("size")) || !hex64Pattern.MatchString(pyjson.Repr(getOr(source, "sha256", "")))) {
				return fmt.Errorf("invalid preserved source identity: %s", identifier)
			}
		}
		build, ok := entry.Value("build").(*Object)
		if !ok || !inSet(build.Value("status"), buildStates) {
			return fmt.Errorf("invalid build status: %s", identifier)
		}
		if recipeBuildStates[build.Value("status").(string)] {
			if _, err := safePath(build.Value("recipe"), "build recipe"); err != nil {
				return err
			}
		}
	}
	return nil
}

func fileSHA256(path string) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer file.Close()
	digest := sha256.New()
	if _, err := io.Copy(digest, file); err != nil {
		return "", err
	}
	return hex.EncodeToString(digest.Sum(nil)), nil
}

// VerifyPreservedSources mirrors verify_preserved_sources.
func (p *Project) VerifyPreservedSources(registry *Object, distribution string) (int, error) {
	verified := 0
	for _, entry := range objects(registry.Value("upstreams")) {
		source := entry.Value("source").(*Object)
		build := entry.Value("build").(*Object)
		if preservedStates[source.Value("status").(string)] {
			path := joinPosix(distribution, purePosix(source.Value("distribution_path").(string)).asPosix())
			if source.Has("size") {
				info, err := os.Stat(path)
				if err != nil || !info.Mode().IsRegular() || isSymlink(path) {
					return 0, fmt.Errorf("preserved source is missing or has the wrong size: %s", path)
				}
				if !pyjson.EqInt(source.Value("size"), info.Size()) {
					return 0, fmt.Errorf("preserved source is missing or has the wrong size: %s", path)
				}
				digest, err := fileSHA256(path)
				if err != nil {
					return 0, err
				}
				if !pyjson.Eq(digest, source.Value("sha256")) {
					return 0, fmt.Errorf("preserved source failed SHA-256: %s", path)
				}
			} else if !isDir(path) || isSymlink(path) {
				return 0, fmt.Errorf("preserved source tree is missing: %s", path)
			}
			verified++
		}
		if recipe, ok := build.Value("recipe").(string); ok {
			path := p.Path(purePosix(recipe).asPosix())
			if !isFile(path) || isSymlink(path) {
				return 0, fmt.Errorf("upstream build recipe is missing: %s", path)
			}
		}
	}
	return verified, nil
}

// SourceOwner mirrors source_owner.
func SourceOwner(registry *Object, relative string) string {
	for _, entry := range objects(registry.Value("upstreams")) {
		source, _ := entry.Value("source").(*Object)
		if source == nil {
			continue
		}
		if root, ok := source.Value("distribution_path").(string); ok && (relative == root || strings.HasPrefix(relative, root+"/")) {
			return pyjson.Repr(entry.Value("id"))
		}
	}
	return ""
}
