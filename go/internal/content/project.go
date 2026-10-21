// Package content is the maintainer content tooling behind x86qw-content:
// data model, validators, deterministic package builders and mirror
// publication.
package content

import (
	"bytes"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"

	"github.com/x86dx2/x86qw/go/internal/atomicfile"
	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
)

// Repository-relative locations.
const (
	DistDir           = "dist"
	InventoryDir      = "maintenance/inventory"
	ComponentsPath    = "maintenance/inventory/components.json"
	ReleasesPath      = "maintenance/inventory/component-releases.json"
	PolicyPath        = "maintenance/inventory/component-policy.json"
	UpstreamsPath     = "maintenance/inventory/upstreams.json"
	RecipesDir        = "maintenance/recipes"
	BuildsDir         = "maintenance/build/packages"
	CatalogPath       = "site/public/api/v1/catalog.json"
	ProductPath       = "site/public/api/v1/product.json"
	TrustRootPath     = "maintenance/trust/root.json"
	TrustDir          = "site/public/api/v1/trust"
	TrustMetadataDir  = "site/public/api/v1/trust/metadata"
	TrustTargetsDir   = "site/public/api/v1/trust/targets"
	ManifestPath      = "dist/manifest.json"
	PrimaryRepository = "x86dx2/x86qw"
)

// Object is the ordered JSON object used throughout the package.
type Object = pyjson.Object

// Project is one checkout. Documents are loaded once and cached, which is
// safe for a tree that does not change during a run.
type Project struct {
	Root string

	// HTTP carries every network request (tests inject a fake transport).
	HTTP *http.Client
	// Run executes git/gh (tests inject a recorder); nil uses os/exec.
	Run func(dir string, stdin []byte, name string, args ...string) ([]byte, error)
	// Getenv reads tokens; nil uses os.Getenv.
	Getenv func(string) string
	// componentsFile/releasesFile override the canonical inventories
	// for staged workspaces.
	componentsFile, releasesFile string

	policy     map[string]*Object
	policyKeys []string
	policyErr  error
	policyDone bool

	components     *Object
	componentsErr  error
	componentsDone bool

	releases     *Object
	releasesErr  error
	releasesDone bool

	upstreams     *Object
	upstreamsErr  error
	upstreamsDone bool

	// nquake is the whole nQuake revision the last discovery saw.
	nquake *NQuakeUpstream
}

// NewProject returns a project rooted at root.
func NewProject(root string) *Project { return &Project{Root: root} }

// Staged returns a view of the project whose component catalog and release
// inventory come from a workspace, to validate a staged change.
func (p *Project) Staged(componentsFile, releasesFile string) *Project {
	return &Project{
		Root: p.Root, HTTP: p.HTTP, Run: p.Run, Getenv: p.Getenv,
		componentsFile: componentsFile, releasesFile: releasesFile,
	}
}

func (p *Project) env(name string) string {
	if p.Getenv != nil {
		return p.Getenv(name)
	}
	return os.Getenv(name)
}

// command runs an external tool in the checkout and returns stdout.
func (p *Project) command(stdin []byte, name string, args ...string) ([]byte, error) {
	if p.Run != nil {
		return p.Run(p.Root, stdin, name, args...)
	}
	process := exec.Command(name, args...)
	process.Dir = p.Root
	if stdin != nil {
		process.Stdin = bytes.NewReader(stdin)
	}
	var stderr bytes.Buffer
	process.Stderr = &stderr
	output, err := process.Output()
	if err != nil {
		return output, fmt.Errorf("Command '%s' returned non-zero exit status: %v %s", name, err, stderr.String())
	}
	return output, nil
}

// Path resolves a repository-relative POSIX path.
func (p *Project) Path(relative string) string { return joinPosix(p.Root, relative) }

// ReadJSON parses a JSON file (the last of duplicate keys wins).
func ReadJSON(path string) (any, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	return pyjson.Decode(raw, pyjson.LastWins)
}

// LoadJSONObject parses a JSON file that must hold an object.
func LoadJSONObject(path string) (*Object, error) {
	value, err := ReadJSON(path)
	if err != nil {
		return nil, fmt.Errorf("nao foi possivel ler %s: %v", path, err)
	}
	object, ok := value.(*Object)
	if !ok {
		return nil, fmt.Errorf("o arquivo precisa conter um objeto JSON: %s", path)
	}
	return object, nil
}

// WriteJSON writes value in insertion order, mode 0644, atomically.
func WriteJSON(path string, value any) error {
	payload, err := pyjson.EncodeFile(value, false)
	if err != nil {
		return err
	}
	return writeAtomic(path, payload, 0o644)
}

// writeAtomic creates path's folders and replaces it with payload.
func writeAtomic(path string, payload []byte, mode os.FileMode) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	return atomicfile.Write(path, payload, mode)
}

// Policy is the component policy: name → declaration, in file order.
func (p *Project) Policy() (map[string]*Object, []string, error) {
	if p.policyDone {
		return p.policy, p.policyKeys, p.policyErr
	}
	p.policyDone = true
	p.policy, p.policyKeys, p.policyErr = loadPolicy(p.Path(PolicyPath))
	return p.policy, p.policyKeys, p.policyErr
}

func loadPolicy(path string) (map[string]*Object, []string, error) {
	value, err := ReadJSON(path)
	if err != nil {
		return nil, nil, err
	}
	document, ok := value.(*Object)
	if !ok || !pyjson.EqInt(document.Value("format"), 1) || !pyjson.Eq(document.Value("project"), "x86qw") {
		return nil, nil, fmt.Errorf("invalid component policy: %s", path)
	}
	components, ok := document.Value("components").(*Object)
	if !ok || components.Len() == 0 {
		return nil, nil, fmt.Errorf("component policy has no components: %s", path)
	}
	result := map[string]*Object{}
	for _, name := range components.Keys() {
		component, ok := components.Value(name).(*Object)
		if name == "" || !ok {
			return nil, nil, fmt.Errorf("invalid component declaration: %s", pyRepr(name))
		}
		consumers, ok := pyjson.StrList(component.Value("consumers"))
		if !ok || len(consumers) == 0 || contains(consumers, "") {
			return nil, nil, fmt.Errorf("component has no explicit consumer: %s", name)
		}
		prefixes, ok := pyjson.StrList(component.Value("distribution_prefixes"))
		valid := ok && len(prefixes) > 0
		for _, prefix := range prefixes {
			valid = valid && len(prefix) > 0 && prefix[len(prefix)-1] == '/'
		}
		if !valid {
			return nil, nil, fmt.Errorf("component has invalid distribution prefixes: %s", name)
		}
		result[name] = component
	}
	return result, components.Keys(), nil
}

func contains(values []string, wanted string) bool {
	for _, value := range values {
		if value == wanted {
			return true
		}
	}
	return false
}

func hasPrefixAny(value string, prefixes []string) bool {
	for _, prefix := range prefixes {
		if len(value) >= len(prefix) && value[:len(prefix)] == prefix {
			return true
		}
	}
	return false
}

// RequireComponent refuses a component the policy does not declare.
func (p *Project) RequireComponent(name string, path *string) error {
	policy, _, err := p.Policy()
	if err != nil {
		return err
	}
	component, ok := policy[name]
	if !ok {
		return fmt.Errorf("component is not consumed by x86QW: %s", name)
	}
	if path != nil {
		prefixes, _ := pyjson.StrList(component.Value("distribution_prefixes"))
		if !hasPrefixAny(*path, prefixes) {
			return fmt.Errorf("distribution path is outside the declared consumer scope for %s: %s", name, *path)
		}
	}
	return nil
}

// ComponentForDistributionPath is the component that consumes a
// distribution path.
func (p *Project) ComponentForDistributionPath(path string) (string, error) {
	policy, keys, err := p.Policy()
	if err != nil {
		return "", err
	}
	var matches []string
	for _, name := range keys {
		prefixes, _ := pyjson.StrList(policy[name].Value("distribution_prefixes"))
		if hasPrefixAny(path, prefixes) {
			matches = append(matches, name)
		}
	}
	if len(matches) > 1 {
		return "", fmt.Errorf("distribution path belongs to multiple components: %s", path)
	}
	if len(matches) == 0 {
		return "", nil
	}
	return matches[0], nil
}

// FirstConsumer returns the first declared consumer of a policy component.
func (p *Project) FirstConsumer(name string) (string, error) {
	policy, _, err := p.Policy()
	if err != nil {
		return "", err
	}
	component, ok := policy[name]
	if !ok {
		return "", fmt.Errorf("component is not consumed by x86QW: %s", name)
	}
	consumers, _ := pyjson.StrList(component.Value("consumers"))
	return consumers[0], nil
}
