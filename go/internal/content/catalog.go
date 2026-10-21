package content

import (
	"fmt"
	"regexp"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
)

// Public package catalog validation.

var (
	sha256Pattern        = regexp.MustCompile(`^[0-9a-f]{64}$`)
	hex40Pattern         = regexp.MustCompile(`^[0-9a-f]{40}$`)
	safeSegment          = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.+-]*$`)
	ezquakeStableVersion = regexp.MustCompile(`^[0-9]+\.[0-9]+\.[0-9]+$`)
	ezquakeNightly       = regexp.MustCompile(`^[0-9]{8}-[0-9]{6}_[0-9a-f]{7}$`)
	catalogPlatforms     = setOf("macos", "linux", "windows", "source", "any")
	catalogChannels      = setOf("stable", "nightly", "content")
)

// PackageFields lists the fields every catalog package must carry.
var PackageFields = []string{
	"component", "version", "channel", "platform", "architecture",
	"filename", "size", "sha256", "urls", "origin_url", "license", "license_url",
	"source_urls", "redistribution_reviewed",
}

func setOf(values ...string) map[string]bool {
	result := map[string]bool{}
	for _, value := range values {
		result[value] = true
	}
	return result
}

// inSet mirrors `value in {...}` for a set of strings.
func inSet(value any, set map[string]bool) bool {
	text, ok := value.(string)
	return ok && set[text]
}

// sizeWithin reports whether value is an integer with 0 < size <= MAX.
func sizeWithin(value any, exact bool) bool {
	if exact && !pyjson.IsExactInt(value) || !exact && !pyjson.IsInstanceInt(value) {
		return false
	}
	low, _ := pyjson.IntCmp(value, 0)
	high, _ := pyjson.IntCmp(value, MaxArtifactBytes)
	return low > 0 && high <= 0
}

func nonEmptyText(value any) bool {
	text, ok := value.(string)
	return ok && strings.TrimSpace(text) != ""
}

// PackageIdentity is (component, package, version, channel, platform, architecture).
type PackageIdentity [6]string

// ValidatePackage checks one catalog package and returns its identity.
func (p *Project) ValidatePackage(value any, label string, requireReviewed bool) (PackageIdentity, error) {
	var identity PackageIdentity
	pkg, ok := value.(*Object)
	if ok {
		for _, field := range PackageFields {
			ok = ok && pkg.Has(field)
		}
	}
	if !ok {
		return identity, fmt.Errorf("%s is missing required fields", label)
	}
	if !inSet(pkg.Value("channel"), catalogChannels) {
		return identity, fmt.Errorf("%s.channel is invalid", label)
	}
	if !inSet(pkg.Value("platform"), catalogPlatforms) {
		return identity, fmt.Errorf("%s.platform is invalid", label)
	}
	for _, key := range []string{"component", "version", "architecture", "filename", "license"} {
		if !pyjson.NonEmptyStr(pkg.Value(key)) {
			return identity, fmt.Errorf("%s has an empty text field", label)
		}
	}
	component := pkg.Value("component").(string)
	if err := p.RequireComponent(component, nil); err != nil {
		return identity, err
	}
	for _, key := range []string{"component", "version", "architecture"} {
		if !safeSegment.MatchString(pkg.Value(key).(string)) {
			return identity, fmt.Errorf("%s.%s is not a safe path segment", label, key)
		}
	}
	packageID := pkg.Value("component")
	if pkg.Has("package") {
		packageID = pkg.Value("package")
	}
	if text, ok := packageID.(string); !ok || !safeSegment.MatchString(text) {
		return identity, fmt.Errorf("%s.package is not a safe path segment", label)
	}
	channel, version := pkg.Value("channel").(string), pkg.Value("version").(string)
	if channel == "content" && component == "ezquake" {
		return identity, fmt.Errorf("%s content packages must use a content component namespace", label)
	}
	if component == "ezquake" {
		pattern := map[string]*regexp.Regexp{"stable": ezquakeStableVersion, "nightly": ezquakeNightly}[channel]
		if pattern == nil || !pattern.MatchString(version) {
			return identity, fmt.Errorf("%s.version is invalid for ezquake %s", label, channel)
		}
	}
	filename := pkg.Value("filename").(string)
	if posixName(filename) != filename || strings.ContainsAny(filename, `/\`) || filename == "." || filename == ".." {
		return identity, fmt.Errorf("%s.filename must not contain a path", label)
	}
	reviewed, ok := pkg.Value("redistribution_reviewed").(bool)
	if !ok {
		return identity, fmt.Errorf("%s.redistribution_reviewed must be a boolean", label)
	}
	if requireReviewed && !reviewed {
		return identity, fmt.Errorf("%s.redistribution_reviewed must be true", label)
	}
	size := pkg.Value("size")
	if !pyjson.IsExactInt(size) {
		return identity, fmt.Errorf("%s.size must be a positive integer", label)
	}
	if order, _ := pyjson.IntCmp(size, 0); order <= 0 {
		return identity, fmt.Errorf("%s.size must be a positive integer", label)
	}
	if order, _ := pyjson.IntCmp(size, MaxArtifactBytes); order > 0 {
		return identity, fmt.Errorf("%s.size exceeds the supported download limit", label)
	}
	if digest, ok := pkg.Value("sha256").(string); !ok || !sha256Pattern.MatchString(digest) {
		return identity, fmt.Errorf("%s.sha256 is invalid", label)
	}
	urls, ok := pkg.Value("urls").([]any)
	if !ok || len(urls) == 0 {
		return identity, fmt.Errorf("%s.urls must contain at least one mirror", label)
	}
	if _, ok := pyjson.StrList(urls); !ok {
		return identity, fmt.Errorf("%s.urls must contain only strings", label)
	}
	if unique, _ := pyjson.UniqueHashable(urls); !unique {
		return identity, fmt.Errorf("%s.urls contains duplicates", label)
	}
	sources, ok := pkg.Value("source_urls").([]any)
	if !ok || len(sources) == 0 {
		return identity, fmt.Errorf("%s.source_urls must contain at least one source", label)
	}
	if _, ok := pyjson.StrList(sources); !ok {
		return identity, fmt.Errorf("%s.source_urls must contain unique strings", label)
	}
	if unique, _ := pyjson.UniqueHashable(sources); !unique {
		return identity, fmt.Errorf("%s.source_urls must contain unique strings", label)
	}
	all := append([]any{pkg.Value("origin_url"), pkg.Value("license_url")}, sources...)
	for _, url := range append(all, urls...) {
		parsed, err := validateHTTPSURL(url, label+" URL")
		if err != nil {
			return identity, err
		}
		if parsed.Query != "" {
			return identity, fmt.Errorf("%s persistent URLs must not contain a query", label)
		}
	}
	if pkg.Has("release_url") {
		parsed, err := validateHTTPSURL(pkg.Value("release_url"), label+".release_url")
		if err != nil {
			return identity, err
		}
		if parsed.Query != "" {
			return identity, fmt.Errorf("%s.release_url must not contain a query", label)
		}
	}
	for _, key := range []string{"release_notes", "release_title", "mirror_title", "mirror_notes"} {
		if pkg.Has(key) && !nonEmptyText(pkg.Value(key)) {
			return identity, fmt.Errorf("%s.%s must be non-empty text", label, key)
		}
	}
	if pkg.Has("mirror_latest") {
		if _, ok := pkg.Value("mirror_latest").(bool); !ok {
			return identity, fmt.Errorf("%s.mirror_latest must be a boolean", label)
		}
	}
	if pkg.Has("upstream_version") {
		if text, ok := pkg.Value("upstream_version").(string); !ok || !safeSegment.MatchString(text) {
			return identity, fmt.Errorf("%s.upstream_version is invalid", label)
		}
	}
	if pkg.Has("distribution_path") {
		distribution, ok := pkg.Value("distribution_path").(string)
		if !ok {
			return identity, fmt.Errorf("%s.distribution_path is invalid", label)
		}
		relative := purePosix(distribution)
		if relative.absolute || relative.hasPart("..") || strings.Contains(distribution, `\`) || relative.name() != filename {
			return identity, fmt.Errorf("%s.distribution_path is unsafe", label)
		}
		if component == "ezquake" {
			expected := strings.Join([]string{
				"clients", "ezquake", channel, version,
				pkg.Value("platform").(string) + "-" + pkg.Value("architecture").(string), filename,
			}, "/")
			if distribution != expected {
				return identity, fmt.Errorf("%s.distribution_path does not match ezquake coordinates", label)
			}
		}
	}
	for _, url := range append([]any{pkg.Value("origin_url")}, urls...) {
		parsed, _ := urlsplit(url.(string))
		if posixName(unquote(parsed.Path)) != filename {
			return identity, fmt.Errorf("%s artifact URLs must end with filename", label)
		}
	}
	return PackageIdentity{
		component, packageID.(string), version, channel,
		pkg.Value("platform").(string), pkg.Value("architecture").(string),
	}, nil
}

// ValidateCatalog checks the whole catalog and returns the
// package count.
func (p *Project) ValidateCatalog(value any) (int, error) {
	catalog, ok := value.(*Object)
	if !ok {
		return 0, fmt.Errorf("catalog must be a JSON object")
	}
	if !pyjson.IsExactInt(catalog.Value("format")) || !pyjson.EqInt(catalog.Value("format"), 1) || !pyjson.Eq(catalog.Value("project"), "x86qw") {
		return 0, fmt.Errorf("unsupported catalog identity or format")
	}
	if validateCatalogVersions(catalog, true) != nil {
		return 0, fmt.Errorf("unsupported catalog schema contract")
	}
	packages, ok := catalog.Value("packages").([]any)
	if !ok {
		return 0, fmt.Errorf("packages must be a list")
	}
	identities := map[PackageIdentity]bool{}
	for index, pkg := range packages {
		label := fmt.Sprintf("packages[%d]", index)
		identity, err := p.ValidatePackage(pkg, label, true)
		if err != nil {
			return 0, err
		}
		if identities[identity] {
			return 0, fmt.Errorf("%s duplicates a package identity", label)
		}
		identities[identity] = true
	}
	return len(packages), nil
}

// PublishedPackageCount counts the content packages: the x86qw CLI
// binaries are the product itself, not distributed content.
func PublishedPackageCount(value any) (int, error) {
	catalog, ok := value.(*Object)
	if !ok {
		return 0, fmt.Errorf("catalog must be a JSON object")
	}
	packages, ok := catalog.Value("packages").([]any)
	if !ok {
		return 0, fmt.Errorf("packages must be a list")
	}
	count := 0
	for _, pkg := range packages {
		if object, ok := pkg.(*Object); ok && !IsCLIPackage(object) {
			count++
		}
	}
	return count, nil
}

// IsCLIPackage reports whether pkg is an x86qw CLI binary, published by the
// CLI release workflow rather than by the content mirrors.
func IsCLIPackage(pkg *Object) bool {
	return pyjson.Eq(pkg.Value("component"), "x86qw") && pyjson.Eq(pkg.Value("package"), "x86qw-cli")
}
