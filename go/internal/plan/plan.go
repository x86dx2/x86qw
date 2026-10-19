// Package plan computes update and upgrade plans for an installation, row
// for row like manager.py update()/upgrade() in dry-run mode.
package plan

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/receipt"
	"github.com/x86dx2/x86qw/go/internal/semver"
	"github.com/x86dx2/x86qw/go/internal/state"
)

// Row is one planned operation; JSON field order matches the sorted keys
// the Python CLI emits.
type Row struct {
	Action    string `json:"action"`
	Available string `json:"available"`
	Installed string `json:"installed"`
	Item      string `json:"item"`
	Kind      string `json:"kind"`
	Size      *int64 `json:"size"`
}

// Plan is the dry-run data of update/upgrade.
type Plan struct {
	Operations []Row  `json:"operations"`
	Status     string `json:"status"`
	Target     string `json:"target"`
}

var (
	legacyReplacements = []struct{ legacy, replacement string }{
		{"nquake-bootstrap", "x86qw-client-bootstrap"}, {"nquake-ktx", "ktx"},
	}
	legacyRemovals = []string{"nquake-sounds"}
	overlay        = regexp.MustCompile(`^(.+)\+x86qw\.(\d+)$`)
)

// macOS runtime actions (manager.py macos_runtime_action).
const (
	ActionPrepareNightly  = "prepare-nightly"
	ActionRestoreUpstream = "restore-upstream"
)

// Input bundles what a plan reads.
type Input struct {
	Root      string
	Catalog   *catalog.Catalog
	Inventory *catalog.Inventory
	// CLIVersion is the running native CLI; when set, a newer x86qw-cli
	// package for GOOS/GOARCH adds a "CLI" row (Python's self-update row).
	CLIVersion   string
	GOOS, GOARCH string
	// ClientAction verifies an installed client and returns its macOS
	// action ("", ActionPrepareNightly or ActionRestoreUpstream). Nil skips both.
	ClientAction func(spec catalog.ClientSpec, channel string, rec *receipt.Receipt) (string, error)
}

type installation struct {
	state   *state.State
	stale   bool
	records map[string]*receipt.Record
}

func load(in Input) (*installation, error) {
	records, err := receipt.LoadComponents(in.Root)
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return nil, err
	}
	result := &installation{records: map[string]*receipt.Record{}}
	for _, record := range records {
		result.records[record.ID] = record
	}
	current, err := state.Read(in.Root)
	switch {
	case errors.Is(err, os.ErrNotExist):
		result.stale = true
	case err != nil:
		return nil, err
	default:
		result.state = current
		if current.Format != state.CurrentFormat {
			result.stale = true
		}
		for _, id := range current.RecordedComponents {
			for _, legacy := range legacyReplacements {
				if id == legacy.legacy {
					result.stale = true
				}
			}
			for _, removed := range legacyRemovals {
				if id == removed {
					result.stale = true
				}
			}
		}
	}
	return result, nil
}

func size(pkg catalog.Package) *int64 {
	if pkg.Size <= 0 {
		return nil
	}
	value := pkg.Size
	return &value
}

func label(in Input, id string) string {
	if component, ok := in.Inventory.Get(id); ok {
		return component.Label
	}
	return id
}

// installed lists catalog components with records, in catalog order.
func (i *installation) installed(in Input) []string {
	var ids []string
	for _, id := range in.Inventory.IDs() {
		if _, ok := i.records[id]; ok {
			ids = append(ids, id)
		}
	}
	return ids
}

// Update returns the rows `update --dry-run` plans.
func Update(in Input) (*Plan, error) {
	current, err := load(in)
	if err != nil {
		return nil, err
	}
	var rows []Row
	if row, err := cliRow(in); err != nil {
		return nil, err
	} else if row != nil {
		rows = append(rows, *row)
	}
	if current.stale {
		rows = append(rows, Row{Kind: "Sistema", Item: "Estado da instalação", Installed: "ausente ou formato histórico", Available: "formato 2", Action: "Migrar"})
	}
	clients := 0
	for _, spec := range catalog.ClientSpecs {
		for _, channel := range []string{"stable", "nightly"} {
			payload, err := os.ReadFile(filepath.Join(in.Root, filepath.FromSlash(spec.Receipt(channel))))
			if errors.Is(err, os.ErrNotExist) {
				continue
			}
			if err != nil {
				return nil, err
			}
			rec, err := receipt.ParseClient(payload, spec.Context(channel))
			if err != nil {
				return nil, fmt.Errorf("recibo ezQuake inválido %s %s: %w", spec.Platform, channel, err)
			}
			clients++
			releases, err := in.Catalog.ClientReleases(spec, channel)
			if err != nil {
				return nil, err
			}
			action := ""
			if in.ClientAction != nil {
				if action, err = in.ClientAction(spec, channel, rec); err != nil {
					return nil, err
				}
			}
			item := fmt.Sprintf("ezQuake %s %s", spec.Label, channel)
			available, installed := releases[0].Version, rec.Get("selection")
			// Same order as manager.py update_runtime.
			if available == installed && action == "" {
				continue
			}
			if available == installed && action == ActionPrepareNightly {
				rows = append(rows, Row{Kind: "Cliente", Item: item, Installed: "área segura", Available: "tela inteira", Action: "Reparar"})
				continue
			}
			if available != installed && !catalog.ReleaseIsNewer(available, installed, channel) {
				continue
			}
			row := Row{Kind: "Cliente", Item: item, Installed: installed, Available: available, Action: "Atualizar", Size: size(releases[0])}
			if action == ActionRestoreUpstream {
				row.Installed, row.Available, row.Action = "bundle re-assinado localmente", "bundle upstream integral", "Restaurar"
			}
			rows = append(rows, row)
		}
	}
	if clients == 0 {
		return nil, fmt.Errorf("nenhum ezQuake gerenciado foi encontrado em %s; use install.sh para instalar o x86QW", in.Root)
	}
	for _, id := range legacyRemovals {
		if record, ok := current.records[id]; ok {
			rows = append(rows, Row{Kind: "Componente", Item: "Sons redundantes nQuake", Installed: record.Receipt.Get("selection"), Available: "incorporado ao KTX", Action: "Remover"})
		}
	}
	for _, legacy := range legacyReplacements {
		record, ok := current.records[legacy.legacy]
		if !ok {
			continue
		}
		pkg, err := in.Catalog.ComponentPackage(legacy.replacement, in.Inventory.ContentNamespaces)
		if err != nil {
			return nil, err
		}
		rows = append(rows, Row{Kind: "Componente", Item: label(in, legacy.replacement), Installed: record.Receipt.Get("selection"), Available: pkg.Version, Action: "Migrar", Size: size(pkg)})
	}
	for _, id := range current.installed(in) {
		pkg, err := in.Catalog.ComponentPackage(id, in.Inventory.ContentNamespaces)
		if err != nil {
			return nil, err
		}
		installed := current.records[id].Receipt.Get("selection")
		if installed == pkg.Version || newerOverlay(installed, pkg.Version) {
			continue
		}
		rows = append(rows, Row{Kind: "Componente", Item: label(in, id), Installed: installed, Available: pkg.Version, Action: "Atualizar", Size: size(pkg)})
	}
	return finish(in.Root, rows), nil
}

// Upgrade returns update rows plus components the profile still misses.
func Upgrade(in Input) (*Plan, error) {
	result, err := Update(in)
	if err != nil {
		return nil, err
	}
	current, err := load(in)
	if err != nil {
		return nil, err
	}
	profile, requested := "none", []string(nil)
	if current.state != nil {
		profile, requested = current.state.Profile, current.state.RequestedComponents
	}
	desired, err := in.Inventory.Desired(profile, requested)
	if err != nil {
		return nil, err
	}
	present := map[string]bool{}
	for _, id := range current.installed(in) {
		present[id] = true
	}
	for _, legacy := range legacyReplacements {
		if _, ok := current.records[legacy.legacy]; ok {
			present[legacy.replacement] = true
		}
	}
	rows := result.Operations
	for _, id := range desired {
		if present[id] {
			continue
		}
		pkg, err := in.Catalog.ComponentPackage(id, in.Inventory.ContentNamespaces)
		if err != nil {
			return nil, err
		}
		rows = append(rows, Row{Kind: "Componente", Item: label(in, id), Installed: "não instalado", Available: pkg.Version, Action: "Adicionar", Size: size(pkg)})
	}
	return finish(in.Root, rows), nil
}

// newerOverlay keeps an installed "<base>+x86qw.N" newer than the catalog's.
func newerOverlay(installed, available string) bool {
	left, right := overlay.FindStringSubmatch(installed), overlay.FindStringSubmatch(available)
	if left == nil || right == nil || left[1] != right[1] {
		return false
	}
	l, errL := strconv.Atoi(left[2])
	r, errR := strconv.Atoi(right[2])
	return errL == nil && errR == nil && l > r
}

func finish(root string, rows []Row) *Plan {
	plan := &Plan{Target: root, Operations: rows, Status: "noop"}
	if plan.Operations == nil {
		plan.Operations = []Row{}
	}
	if len(plan.Operations) > 0 {
		plan.Status = "planned"
	}
	return plan
}

// cliRow plans the native CLI self-update.
func cliRow(in Input) (*Row, error) {
	// Development builds are never replaced by the self-update.
	if in.CLIVersion == "" || !semver.Valid(in.CLIVersion) || strings.HasSuffix(in.CLIVersion, "-dev") {
		return nil, nil
	}
	pkg, ok, err := in.Catalog.CLIPackage(in.GOOS, in.GOARCH)
	if err != nil || !ok {
		return nil, err
	}
	newer, err := semver.CompareStrings(pkg.Version, in.CLIVersion)
	if err != nil || newer <= 0 {
		return nil, err
	}
	return &Row{Kind: "CLI", Item: "x86QW", Installed: in.CLIVersion, Available: pkg.Version, Action: "Atualizar", Size: size(pkg)}, nil
}
