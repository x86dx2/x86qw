// Package catalog models the authenticated public catalog (catalog.json)
// and the component inventory shipped with the CLI (components.json).
package catalog

import (
	_ "embed"
	"encoding/json"
	"fmt"
	"net/url"
	"path"
	"regexp"
	"sort"
	"strconv"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/receipt"
	"github.com/x86dx2/x86qw/go/internal/semver"
)

var (
	hex40          = regexp.MustCompile(`^[0-9a-f]{40}$`)
	hex64          = regexp.MustCompile(`^[0-9a-f]{64}$`)
	componentID    = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}$`)
	stableVersion  = regexp.MustCompile(`^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$`)
	nightlyVersion = regexp.MustCompile(`^[0-9]{8}-[0-9]{6}_[0-9a-f]{7}$`)
)

// Package is one published artifact.
type Package struct {
	Component              string   `json:"component"`
	Package                string   `json:"package"`
	Version                string   `json:"version"`
	Channel                string   `json:"channel"`
	Platform               string   `json:"platform"`
	Architecture           string   `json:"architecture"`
	Filename               string   `json:"filename"`
	Size                   int64    `json:"size"`
	SHA256                 string   `json:"sha256"`
	URLs                   []string `json:"urls"`
	OriginURL              string   `json:"origin_url"`
	License                string   `json:"license"`
	SourceRevision         string   `json:"source_revision"`
	SourceCommit           string   `json:"source_commit"`
	RedistributionReviewed bool     `json:"redistribution_reviewed"`
	DistributionPath       string   `json:"distribution_path"`
	Current                bool     `json:"current"`
}

// Catalog is a parsed catalog.json (format 1).
type Catalog struct {
	Packages []Package
}

// Parse validates the catalog identity and decodes its packages.
func Parse(raw []byte) (*Catalog, error) {
	var document struct {
		Format   int       `json:"format"`
		Project  string    `json:"project"`
		Packages []Package `json:"packages"`
	}
	if err := json.Unmarshal(raw, &document); err != nil {
		return nil, fmt.Errorf("catálogo x86QW inválido: %w", err)
	}
	if document.Format != 1 || document.Project != "x86qw" || document.Packages == nil {
		return nil, fmt.Errorf("catálogo x86QW usa identidade ou formato incompatível")
	}
	return &Catalog{Packages: document.Packages}, nil
}

// Revision returns source_revision, falling back to source_commit.
func (p Package) Revision() string {
	if p.SourceRevision != "" {
		return p.SourceRevision
	}
	return p.SourceCommit
}

func (p Package) validateArtifact(label string) error {
	if p.Size <= 0 {
		return fmt.Errorf("tamanho inválido de %s", label)
	}
	if !hex64.MatchString(p.SHA256) {
		return fmt.Errorf("SHA-256 inválido de %s", label)
	}
	if len(p.URLs) == 0 {
		return fmt.Errorf("mirrors inválidos de %s", label)
	}
	seen := map[string]bool{}
	for _, raw := range p.URLs {
		parsed, err := url.Parse(raw)
		if err != nil || parsed.Scheme != "https" || parsed.Host == "" || parsed.User != nil || parsed.Fragment != "" || seen[raw] {
			return fmt.Errorf("mirror inválido de %s: %s", label, raw)
		}
		seen[raw] = true
		if name, _ := url.PathUnescape(path.Base(parsed.Path)); name != p.Filename {
			return fmt.Errorf("nome inesperado em um mirror de %s", label)
		}
	}
	if !p.RedistributionReviewed {
		return fmt.Errorf("o pacote %s ainda não foi liberado pelo x86QW", label)
	}
	return nil
}

// ComponentPackage returns the single current content package of a component.
func (c *Catalog) ComponentPackage(id string, namespaces []string) (Package, error) {
	allowed := map[string]bool{}
	for _, namespace := range namespaces {
		allowed[namespace] = true
	}
	var matches []Package
	for _, pkg := range c.Packages {
		if pkg.Package == id && pkg.Channel == "content" && pkg.Platform == "any" && pkg.Architecture == "any" && allowed[pkg.Component] {
			matches = append(matches, pkg)
		}
	}
	if len(matches) != 1 {
		return Package{}, fmt.Errorf("o catálogo deve publicar exatamente um pacote atual para %s", id)
	}
	pkg := matches[0]
	if !componentID.MatchString(pkg.Version) {
		return Package{}, fmt.Errorf("versão inválida do componente %s", id)
	}
	revision := pkg.Revision()
	if !hex40.MatchString(revision) && !hex64.MatchString(revision) {
		return Package{}, fmt.Errorf("revisão de origem inválida do componente %s", id)
	}
	if pkg.Filename != id+"-"+pkg.Version+".zip" {
		return Package{}, fmt.Errorf("identidade inconsistente do pacote %s", id)
	}
	return pkg, pkg.validateArtifact(id)
}

// ClientReleases lists ezQuake packages for one platform and channel,
// newest first, using the same filename and ordering rules as manager.py.
func (c *Catalog) ClientReleases(spec ClientSpec, channel string) ([]Package, error) {
	var releases []Package
	seen := map[string]bool{}
	for index, pkg := range c.Packages {
		if pkg.Component != "ezquake" || pkg.Channel != channel || pkg.Platform != spec.Platform || pkg.Architecture != spec.Architecture {
			continue
		}
		expected := spec.StableArchive
		pattern := stableVersion
		if channel == "nightly" {
			expected = pkg.Version + spec.NightlySuffix
			pattern = nightlyVersion
		}
		if !pattern.MatchString(pkg.Version) || pkg.Filename != expected {
			return nil, fmt.Errorf("versão ou artefato inválido no catálogo x86QW: packages[%d]", index)
		}
		if err := pkg.validateArtifact(fmt.Sprintf("packages[%d]", index)); err != nil {
			return nil, err
		}
		if seen[pkg.Version] {
			return nil, fmt.Errorf("o catálogo x86QW contém versões duplicadas de ezquake para %s", spec.Label)
		}
		seen[pkg.Version] = true
		releases = append(releases, pkg)
	}
	if len(releases) == 0 {
		return nil, fmt.Errorf("nenhuma versão %s de ezquake está disponível para %s", channel, spec.Label)
	}
	sort.SliceStable(releases, func(a, b int) bool {
		if channel == "nightly" {
			return releases[a].Version > releases[b].Version
		}
		return stableKeyLess(releases[b].Version, releases[a].Version)
	})
	return releases, nil
}

func stableKeyLess(a, b string) bool {
	left, right := strings.Split(a, "."), strings.Split(b, ".")
	for index := range left {
		l, _ := strconv.Atoi(left[index])
		r, _ := strconv.Atoi(right[index])
		if l != r {
			return l < r
		}
	}
	return false
}

// ReleaseIsNewer compares client versions like manager.py release_is_newer.
func ReleaseIsNewer(candidate, installed, channel string) bool {
	if channel == "stable" {
		result, err := semver.CompareStrings(candidate, installed)
		return err == nil && result > 0
	}
	return candidate > installed
}

// ClientSpec is one ezQuake platform layout (runtimes.json + capabilities.json).
type ClientSpec struct {
	Platform, Label, Architecture, StableArchive, NightlySuffix  string
	StableRuntime, NightlyRuntime, StableReceipt, NightlyReceipt string
}

// Runtime returns the install path for a channel.
func (s ClientSpec) Runtime(channel string) string {
	if channel == "stable" {
		return s.StableRuntime
	}
	return s.NightlyRuntime
}

// Context returns the receipt validation context for a channel.
func (s ClientSpec) Context(channel string) receipt.ClientContext {
	return receipt.ClientContext{
		Platform: s.Platform, Architecture: s.Architecture, Channel: channel,
		InstallName: s.Runtime(channel), StableArchive: s.StableArchive, NightlySuffix: s.NightlySuffix,
	}
}

// Receipt returns the receipt path for a channel.
func (s ClientSpec) Receipt(channel string) string {
	if channel == "stable" {
		return s.StableReceipt
	}
	return s.NightlyReceipt
}

// ClientSpecs mirrors PLATFORMS in manager.py, in its iteration order.
var ClientSpecs = []ClientSpec{
	{"macos", "macOS", "universal", "ezQuake-macOS-universal.zip", "_ezQuake-macOS-universal.zip",
		"ezQuake Stable.app", "ezQuake Nightly.app", ".x86qw/clients/ezquake/macos/stable.receipt", ".x86qw/clients/ezquake/macos/nightly.receipt"},
	{"linux", "Linux x86_64", "x86_64", "ezQuake-linux-x86_64.zip", "_ezQuake-x86_64.AppImage",
		"ezquake-stable-x86_64.AppImage", "ezquake-nightly-x86_64.AppImage", ".x86qw/clients/ezquake/linux/stable.receipt", ".x86qw/clients/ezquake/linux/nightly.receipt"},
	{"windows", "Windows x64", "x64", "ezQuake-windows-x64.zip", "_ezquake.exe",
		"ezquake-stable.exe", "ezquake-nightly.exe", ".x86qw/clients/ezquake/windows/stable.receipt", ".x86qw/clients/ezquake/windows/nightly.receipt"},
}
