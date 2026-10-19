package catalog

import (
	_ "embed"
	"encoding/json"
	"fmt"
)

// ComponentsJSON is maintenance/inventory/components.json, which the Python
// bundle ships as _x86qw/components.json. A test keeps the copies identical.
//
//go:embed components.json
var ComponentsJSON []byte

// Component is one installable component.
type Component struct {
	ID             string          `json:"id"`
	Label          string          `json:"label"`
	Kind           string          `json:"kind"`
	Requires       []string        `json:"requires"`
	ProjectSources []ProjectSource `json:"project_sources"`
}

// ProjectSource maps a packaged file to its install path; entries with a
// platform are per-platform executables.
type ProjectSource struct {
	Destination        string `json:"destination"`
	InstallDestination string `json:"install_destination"`
	Platform           string `json:"platform"`
}

// PlatformFiles returns the per-platform executables of a component.
func (c Component) PlatformFiles() []ProjectSource {
	var files []ProjectSource
	for _, source := range c.ProjectSources {
		if source.Platform != "" {
			files = append(files, source)
		}
	}
	return files
}

// Inventory holds profiles and the dependency graph.
type Inventory struct {
	ContentNamespaces []string            `json:"content_namespaces"`
	Profiles          map[string][]string `json:"profiles"`
	ProfileHistory    map[string][]string `json:"profile_history"`
	Components        []Component         `json:"components"`
	byID              map[string]Component
}

// LoadInventory parses the embedded inventory.
func LoadInventory() (*Inventory, error) {
	return ParseInventory(ComponentsJSON)
}

// ParseInventory parses components.json and rejects unknown dependencies and cycles.
func ParseInventory(raw []byte) (*Inventory, error) {
	var inventory Inventory
	if err := json.Unmarshal(raw, &inventory); err != nil {
		return nil, fmt.Errorf("inventário de componentes inválido: %w", err)
	}
	inventory.byID = map[string]Component{}
	for _, component := range inventory.Components {
		if _, dup := inventory.byID[component.ID]; dup {
			return nil, fmt.Errorf("componente duplicado: %s", component.ID)
		}
		inventory.byID[component.ID] = component
	}
	for _, component := range inventory.Components {
		if _, err := inventory.Resolve([]string{component.ID}); err != nil {
			return nil, err
		}
	}
	return &inventory, nil
}

// Get returns a component by id.
func (i *Inventory) Get(id string) (Component, bool) {
	component, ok := i.byID[id]
	return component, ok
}

// IDs lists component ids in catalog order.
func (i *Inventory) IDs() []string {
	ids := make([]string, len(i.Components))
	for index, component := range i.Components {
		ids[index] = component.ID
	}
	return ids
}

// Resolve returns selected components plus dependencies, dependencies
// first, preserving selection order (catalogs.resolve_dependencies).
func (i *Inventory) Resolve(selected []string) ([]string, error) {
	var resolved []string
	done := map[string]bool{}
	visiting := map[string]bool{}
	var add func(string) error
	add = func(id string) error {
		component, ok := i.byID[id]
		if !ok {
			return fmt.Errorf("componente desconhecido: %s", id)
		}
		if done[id] {
			return nil
		}
		if visiting[id] {
			return fmt.Errorf("dependência circular em %s", id)
		}
		visiting[id] = true
		for _, dependency := range component.Requires {
			if err := add(dependency); err != nil {
				return err
			}
		}
		visiting[id] = false
		done[id] = true
		resolved = append(resolved, id)
		return nil
	}
	for _, id := range selected {
		if err := add(id); err != nil {
			return nil, err
		}
	}
	return resolved, nil
}

// Desired returns the components a profile asks for.
func (i *Inventory) Desired(profile string, requested []string) ([]string, error) {
	switch profile {
	case "none":
		return nil, nil
	case "custom":
		return i.Resolve(requested)
	}
	selection, ok := i.Profiles[profile]
	if !ok {
		return nil, fmt.Errorf("perfil desconhecido: %s", profile)
	}
	return i.Resolve(selection)
}
