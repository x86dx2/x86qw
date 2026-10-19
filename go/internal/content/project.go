// Package content ports the maintainer content tooling (maintenance/manage.py
// and the maintenance/tools modules it uses): data model, validators,
// deterministic package builders and mirror publication.
package content

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"

	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
)

// Repository-relative locations used by manage.py.
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
	TrustMetadataDir  = "site/public/api/v1/trust/metadata"
	TrustTargetsDir   = "site/public/api/v1/trust/targets"
	ManifestPath      = "dist/manifest.json"
	PrimaryRepository = "x86dx2/x86qw"
)

// Object is the ordered JSON object used throughout the package.
type Object = pyjson.Object

// Project is one checkout. Documents are loaded once and cached; manage.py
// re-reads them on every call, which is equivalent for a static tree.
type Project struct {
	Root string

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
}

// NewProject returns a project rooted at root.
func NewProject(root string) *Project { return &Project{Root: root} }

// Path resolves a repository-relative POSIX path.
func (p *Project) Path(relative string) string { return joinPosix(p.Root, relative) }

// ReadJSON mirrors json.loads(path.read_text()) (last duplicate wins).
func ReadJSON(path string) (any, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	return pyjson.Decode(raw, pyjson.LastWins)
}

// LoadJSONObject mirrors manage.load_json.
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

// WriteJSON mirrors manage.write_json (insertion order, mode 0644, atomic).
func WriteJSON(path string, value any) error {
	payload, err := pyjson.EncodeFile(value, false)
	if err != nil {
		return err
	}
	return writeAtomic(path, payload, 0o644)
}

func writeAtomic(path string, payload []byte, mode os.FileMode) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	stem := pathStem(filepath.Base(path))
	file, err := os.CreateTemp(filepath.Dir(path), "."+stem+"-*.json")
	if err != nil {
		return err
	}
	temporary := file.Name()
	defer os.Remove(temporary)
	_, writeErr := file.Write(payload)
	if err := errors.Join(writeErr, file.Close()); err != nil {
		return err
	}
	if err := os.Chmod(temporary, mode); err != nil {
		return err
	}
	return os.Rename(temporary, path)
}

// Policy mirrors load_component_policy: name → declaration, in file order.
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

// RequireComponent mirrors component_policy.require_component.
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

// ComponentForDistributionPath mirrors component_for_distribution_path.
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
