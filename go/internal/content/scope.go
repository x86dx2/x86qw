package content

import (
	"fmt"
	"sort"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
)

// Every component of the distribution is checked and updated on its own:
// a component that is not selected, whose upstream is unavailable or that
// needs a reviewed definition keeps exactly the files the manifest records,
// so it neither changes nor disappears while the others move.

// UpdatableIDs are the updatable units: every release component plus the
// ezQuake clients.
func UpdatableIDs(releases *Object) map[string]bool {
	ids := map[string]bool{"ezquake": true}
	if components, ok := releases.Value("components").(*Object); ok {
		for _, id := range components.Keys() {
			ids[id] = true
		}
	}
	return ids
}

// ValidateSelection rejects unknown component names.
func ValidateSelection(selected []string, ids map[string]bool) error {
	var unknown []string
	for _, id := range selected {
		if !ids[id] {
			unknown = append(unknown, id)
		}
	}
	if len(unknown) > 0 {
		known := make([]string, 0, len(ids))
		for id := range ids {
			known = append(known, id)
		}
		sort.Strings(known)
		return managerError("componente(s) desconhecido(s): %s. Disponíveis: %s", strings.Join(unknown, ", "), strings.Join(known, ", "))
	}
	return nil
}

// assetOwner is the component an asset belongs to; "" means a pinned file
// that no component update can change.
func assetOwner(asset Asset, ids map[string]bool) string {
	if asset.Component == "ezquake" {
		return "ezquake"
	}
	if ids[asset.Subcomponent] {
		return asset.Subcomponent
	}
	return ""
}

func manifestOwner(metadata *Object, ids map[string]bool) string {
	if pyjson.Eq(metadata.Value("component"), "ezquake") {
		return "ezquake"
	}
	if pkg, ok := metadata.Value("package").(string); ok && ids[pkg] {
		return pkg
	}
	return ""
}

// NQuakeOwners are the components that own files of the nQuake reference.
func NQuakeOwners(manifest *Object, ids map[string]bool) map[string]bool {
	owners := map[string]bool{}
	files, _ := manifest.Value("files").(*Object)
	if files == nil {
		return owners
	}
	for _, relative := range files.Keys() {
		metadata, ok := files.Value(relative).(*Object)
		if _, snapshot := nquakeSnapshotParts(relative); ok && snapshot {
			if owner := manifestOwner(metadata, ids); owner != "" {
				owners[owner] = true
			}
		}
	}
	return owners
}

// KeepCurrent replaces the discovered assets of every owner that keep
// selects with the files the manifest already records for it.
func KeepCurrent(assets []Asset, manifest *Object, ids map[string]bool, keep func(owner string) bool) ([]Asset, error) {
	files, ok := manifest.Value("files").(*Object)
	if !ok {
		return nil, fmt.Errorf("manifesto sem arquivos")
	}
	kept := map[string]bool{}
	var result []Asset
	for _, relative := range files.Keys() {
		metadata, ok := files.Value(relative).(*Object)
		if !ok {
			continue
		}
		owner := manifestOwner(metadata, ids)
		if owner == "" || !keep(owner) {
			continue
		}
		kept[Casefold(relative)] = true
		subcomponent := ""
		if pyjson.Truthy(metadata.Value("package")) {
			subcomponent = pyjson.Repr(metadata.Value("package"))
		}
		result = append(result, Asset{pyjson.Repr(metadata.Value("component")), pyjson.Repr(metadata.Value("url")), relative,
			int64Of(metadata.Value("size")), subcomponent, pyjson.Repr(metadata.Value("sha256")), ""})
	}
	for _, asset := range assets {
		owner := assetOwner(asset, ids)
		if (owner != "" && keep(owner)) || kept[Casefold(asset.Path)] {
			continue
		}
		result = append(result, asset)
	}
	sort.SliceStable(result, func(i, j int) bool { return result[i].Path < result[j].Path })
	return result, nil
}
