package content

import (
	"fmt"
	"os"

	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
)

// Public product facts (maintenance/tools/product_catalog.py).

func object(pairs ...any) *Object {
	result := pyjson.NewObject()
	for index := 0; index+1 < len(pairs); index += 2 {
		result.Set(pairs[index].(string), pairs[index+1])
	}
	return result
}

// field mirrors mapping[key] (KeyError when missing).
func field(source *Object, key string) (any, error) {
	if source == nil {
		return nil, fmt.Errorf("KeyError: %s", pyRepr(key))
	}
	value, ok := source.Get(key)
	if !ok {
		return nil, fmt.Errorf("KeyError: %s", pyRepr(key))
	}
	return value, nil
}

func project(source *Object, keys ...string) (*Object, error) {
	result := pyjson.NewObject()
	for _, key := range keys {
		value, err := field(source, key)
		if err != nil {
			return nil, err
		}
		result.Set(key, value)
	}
	return result, nil
}

// BuildProductCatalog mirrors build_product_catalog.
func (p *Project) BuildProductCatalog() (*Object, error) {
	publicValue, err := ReadJSON(p.Path(CatalogPath))
	if err != nil {
		return nil, err
	}
	if _, err := p.ValidateCatalog(publicValue); err != nil {
		return nil, err
	}
	public := publicValue.(*Object)
	packageCount, err := PublishedPackageCount(public)
	if err != nil {
		return nil, err
	}
	components, err := p.Components()
	if err != nil {
		return nil, err
	}
	inventory, err := p.LoadInventory(components, public)
	if err != nil {
		return nil, err
	}
	var installers []*Object
	for _, item := range public.Value("packages").([]any) {
		pkg := item.(*Object)
		if pyjson.Eq(pkg.Value("package"), "x86qw-installer") && pkg.Value("current") == true {
			installers = append(installers, pkg)
		}
	}
	if len(installers) != 1 {
		return nil, fmt.Errorf("public catalog must declare exactly one current installer")
	}
	current := installers[0]
	versionBytes, err := os.ReadFile(p.Path("dist/installer/VERSION"))
	if err != nil {
		return nil, err
	}
	version := pyStrip(string(versionBytes))
	if !pyjson.Eq(current.Value("version"), version) {
		return nil, fmt.Errorf("installer VERSION differs from the public current package")
	}

	var runtimes []any
	for _, runtime := range objects(inventory.Runtimes.Value("runtimes")) {
		projected, err := project(runtime, "id", "label", "kind", "protocols", "capabilities", "component")
		if err != nil {
			return nil, err
		}
		var platforms []any
		for _, platform := range objects(runtime.Value("platforms")) {
			entry, err := project(platform, "system", "architecture", "variant", "format", "origin", "support", "validation", "test_required")
			if err != nil {
				return nil, err
			}
			if pyjson.Truthy(platform.Value("support_targets")) {
				var targets []any
				for _, target := range objects(platform.Value("support_targets")) {
					item, err := project(target, "variant", "support", "validation")
					if err != nil {
						return nil, err
					}
					targets = append(targets, item)
				}
				entry.Set("support_targets", orEmpty(targets))
			}
			platforms = append(platforms, entry)
		}
		projected.Set("platforms", orEmpty(platforms))
		runtimes = append(runtimes, projected)
	}
	var games []any
	for _, game := range objects(inventory.Games.Value("games")) {
		entry, err := project(game, "id", "label")
		if err != nil {
			return nil, err
		}
		entry.Set("version", game.Value("version"))
		rest, err := project(game, "protocol", "component", "client_runtimes", "server_runtimes", "smoke_test")
		if err != nil {
			return nil, err
		}
		for _, key := range rest.Keys() {
			entry.Set(key, rest.Value(key))
		}
		games = append(games, entry)
	}
	installer, err := project(current, "filename", "distribution_path", "sha256", "size", "urls")
	if err != nil {
		return nil, err
	}
	commands, err := field(inventory.Capabilities, "commands")
	if err != nil {
		return nil, err
	}
	return object(
		"format", pyjson.Number("1"),
		"project", "x86qw",
		"release_audience", "owner-only",
		"external_public", false,
		"release_truth_url", "/api/v1/release-truth.json",
		"version", version,
		"package_count", pyjson.Number(fmt.Sprint(packageCount)),
		"component_count", pyjson.Number(fmt.Sprint(len(components.Value("components").([]any)))),
		"commands", commands,
		"installer", installer,
		"runtimes", orEmpty(runtimes),
		"games", orEmpty(games),
	), nil
}

func orEmpty(values []any) []any {
	if values == nil {
		return []any{}
	}
	return values
}

// EncodedProductCatalog mirrors encoded_product_catalog.
func (p *Project) EncodedProductCatalog() ([]byte, error) {
	catalog, err := p.BuildProductCatalog()
	if err != nil {
		return nil, err
	}
	return pyjson.EncodeFile(catalog, true)
}

// ProductMismatch is the verify error for a stale product.json.
const ProductMismatch = "site/public/api/v1/product.json diverge dos inventarios canonicos; execute 'x86qw-content product --write'"

// CheckProduct reports whether product.json matches the inventories.
func (p *Project) CheckProduct() error {
	expected, err := p.EncodedProductCatalog()
	if err != nil {
		return err
	}
	path := p.Path(ProductPath)
	actual, readErr := os.ReadFile(path)
	if !isFile(path) || isSymlink(path) || readErr != nil || string(actual) != string(expected) {
		return fmt.Errorf("%s", ProductMismatch)
	}
	return nil
}

// WriteProduct regenerates product.json.
func (p *Project) WriteProduct() error {
	expected, err := p.EncodedProductCatalog()
	if err != nil {
		return err
	}
	return writeAtomic(p.Path(ProductPath), expected, 0o644)
}
