package install

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/lock"
	"github.com/x86dx2/x86qw/go/internal/plan"
	"github.com/x86dx2/x86qw/go/internal/receipt"
	"github.com/x86dx2/x86qw/go/internal/state"
)

// Ported from manager.py repair_plan / repair.

// legacyStableIdentities mirrors MACOS_STABLE_BINARY_IDENTITIES.
var legacyStableIdentities = map[string]string{
	"2ccea8f214c91e5fc92b5cb195c81ac584f75a551d11a58de56e5e7951eec7ed": "e24524761d8ff10c57a8ecbb2fdc7ce29d1bd78641cfaecf49644d8881e2422a",
}

// permissionRuntimes are executable server runtimes per component, in
// runtimes.json order.
var permissionRuntimes = []struct{ component, path string }{
	{"mvdsv", "mvdsv"}, {"qtv", "qtv/qtv"}, {"qwfwd", "qwfwd/qwfwd"},
}

type clientIssue struct {
	spec      catalog.ClientSpec
	channel   string
	selection string
	reason    string
	mode      string // payload or permission
	available bool
}

// RepairAssessment mirrors RepairAssessment.
type RepairAssessment struct {
	invalidComponents []string
	supportInvalid    bool
	permissions       []string
	packageOrder      bool
	clients           []clientIssue
	diagnostics       []string
	recoveredState    *state.State
	inferredState     map[string]any
}

func diagnosticCategory(diagnostic string) string {
	if strings.Contains(diagnostic, "runtime presente sem recibo; preservado") {
		return "advisory"
	}
	return "fatal"
}

func assessRepair(in plan.Input) (*RepairAssessment, error) {
	root := in.Root
	for _, pak := range []struct{ path, sha string }{{"id1/pak0.pak", pak0SHA256}, {"id1/pak1.pak", pak1SHA256}} {
		if err := checkPak(filepath.Join(root, pak.path), pak.sha); err != nil {
			return nil, err
		}
	}
	assessment := &RepairAssessment{}
	valid := map[string]*receipt.Record{}
	entries, _ := os.ReadDir(filepath.Join(root, receipt.ComponentsDir))
	for _, entry := range entries {
		record, err := receipt.Load(filepath.Join(root, receipt.ComponentsDir, entry.Name()))
		if err != nil {
			assessment.diagnostics = append(assessment.diagnostics, fmt.Sprintf("%s: %v", entry.Name(), err))
			continue
		}
		valid[entry.Name()] = record
	}
	installed := map[string]bool{}
	for _, id := range in.Inventory.IDs() {
		if valid[id] != nil {
			installed[id] = true
		}
	}
	statePath := filepath.Join(root, state.Path)
	if info, err := os.Lstat(statePath); err == nil {
		current, readErr := state.Read(root)
		switch {
		case !info.Mode().IsRegular():
			assessment.diagnostics = append(assessment.diagnostics, fmt.Sprintf("state.json: Estado da instalação inválido: %s", statePath))
		case readErr != nil:
			assessment.diagnostics = append(assessment.diagnostics, fmt.Sprintf("state.json: Estado da instalação inválido: %s", statePath))
		default:
			recorded := map[string]bool{}
			for _, id := range current.Installed() {
				recorded[id] = true
			}
			var missing []string
			for id := range recorded {
				if !installed[id] {
					missing = append(missing, id)
				}
			}
			sort.Strings(missing)
			if len(missing) > 0 {
				assessment.diagnostics = append(assessment.diagnostics, "state.json registra componentes sem metadados íntegros: "+strings.Join(missing, ", "))
			} else if len(recorded) != len(installed) {
				assessment.recoveredState = current
			}
		}
	} else if len(assessment.diagnostics) > 0 {
		assessment.diagnostics = append(assessment.diagnostics, "state.json ausente e metadados parciais impedem inferência segura")
	} else if len(installed) > 0 {
		assessment.inferredState = inferState(in, installed)
	}
	for _, id := range in.Inventory.IDs() {
		if record := valid[id]; record != nil {
			if _, err := verifyRecord(root, record); err != nil {
				assessment.invalidComponents = append(assessment.invalidComponents, id)
			}
		}
	}
	if games, err := LoadGames(); err == nil {
		issues, _, err := PlaySupportIssues(root, AvailableGames(root, games))
		if err != nil {
			assessment.diagnostics = append(assessment.diagnostics, "play-support: "+err.Error())
		}
		assessment.supportInvalid = len(issues) > 0
	}
	seen := map[string]bool{}
	for _, runtime := range permissionRuntimes {
		path := filepath.Join(root, filepath.FromSlash(runtime.path))
		if installed[runtime.component] && !seen[path] && regular(path) && executableMissing(path) {
			seen[path] = true
			assessment.permissions = append(assessment.permissions, path)
		}
	}
	assessment.packageOrder = verifyPackageOrder(root, valid["package-order"]) != nil
	for _, spec := range catalog.ClientSpecs {
		for _, channel := range []string{"stable", "nightly"} {
			runtime := filepath.Join(root, spec.Runtime(channel))
			receiptPath := filepath.Join(root, filepath.FromSlash(spec.Receipt(channel)))
			if _, err := os.Lstat(receiptPath); err != nil {
				if _, err := os.Lstat(runtime); err == nil {
					assessment.diagnostics = append(assessment.diagnostics, fmt.Sprintf("ezQuake %s %s: runtime presente sem recibo; preservado", spec.Label, channel))
				}
				continue
			}
			payload, err := os.ReadFile(receiptPath)
			var rec *receipt.Receipt
			if err == nil {
				rec, err = receipt.ParseClient(payload, spec.Context(channel))
			}
			if err != nil {
				assessment.diagnostics = append(assessment.diagnostics, fmt.Sprintf("ezQuake %s %s: recibo inválido ou parcial (%v)", spec.Label, channel, err))
				continue
			}
			available := false
			if releases, err := in.Catalog.ClientReleases(spec, channel); err == nil {
				for _, release := range releases {
					available = available || release.Version == rec.Get("selection")
				}
			}
			issue := clientIssue{spec: spec, channel: channel, selection: rec.Get("selection"), mode: "payload", available: available}
			if _, err := os.Lstat(runtime); err != nil {
				issue.reason = "runtime ausente"
				if !available {
					issue.reason = "runtime ausente; payload precisa do bootstrap"
				}
				assessment.clients = append(assessment.clients, issue)
				continue
			}
			if spec.Platform == "linux" && regular(runtime) && executableMissing(runtime) {
				if digest, _ := fileSHA256(runtime); digest == rec.Get("binary_sha256") {
					issue.reason, issue.mode = "AppImage sem permissão de execução", "permission"
					assessment.clients = append(assessment.clients, issue)
					continue
				}
			}
			if _, err := verifyClient(root, spec, channel); err != nil {
				issue.reason = "runtime ausente, incompleto ou divergente"
				assessment.clients = append(assessment.clients, issue)
				continue
			}
			if spec.Platform == "macos" && channel == "stable" {
				if legacy, ok := legacyStableIdentities[rec.Get("artifact_sha256")]; ok && legacy == rec.Get("binary_sha256") {
					issue.reason = "bundle x86QW re-assinado; restaurar o bundle upstream integral"
					assessment.clients = append(assessment.clients, issue)
				}
			}
		}
	}
	return assessment, nil
}

// inferState mirrors infer_install_state for a missing state.json.
func inferState(in plan.Input, installed map[string]bool) map[string]any {
	var ids []string
	for _, id := range in.Inventory.IDs() {
		if installed[id] {
			ids = append(ids, id)
		}
	}
	profile, requested := "custom", ids
	fingerprint := state.Fingerprint(ids)
	for _, candidate := range []string{"essential", "recommended", "complete"} {
		for _, known := range in.Inventory.ProfileHistory[candidate] {
			if known == fingerprint && profile == "custom" {
				profile, requested = candidate, nil
			}
		}
	}
	toAny := func(values []string) []any {
		result := []any{}
		for _, value := range values {
			result = append(result, value)
		}
		return result
	}
	return map[string]any{
		"profile": profile, "requested_components": toAny(requested), "known_components": toAny(in.Inventory.IDs()),
		"capabilities": []any{},
	}
}

// RepairPlan returns the dry-run rows of `repair`.
func RepairPlan(in plan.Input) (*plan.Plan, *RepairAssessment, error) {
	assessment, err := assessRepair(in)
	if err != nil {
		return nil, nil, err
	}
	var rows []plan.Row
	for _, diagnostic := range assessment.diagnostics {
		rows = append(rows, plan.Row{Kind: "Diagnóstico " + diagnosticCategory(diagnostic), Item: "Metadados gerenciados", Installed: diagnostic, Available: "preservar e reconciliar pelo bootstrap", Action: "Inspecionar"})
	}
	if assessment.recoveredState != nil || assessment.inferredState != nil {
		rows = append(rows, plan.Row{Kind: "Metadados", Item: "state.json", Installed: "ausente ou desatualizado", Available: "reconstruído dos recibos íntegros", Action: "Reparar"})
	}
	for _, issue := range assessment.clients {
		rows = append(rows, plan.Row{Kind: "Cliente", Item: fmt.Sprintf("ezQuake %s %s", issue.spec.Label, issue.channel), Installed: issue.reason, Available: issue.selection, Action: "Reparar"})
	}
	for _, id := range assessment.invalidComponents {
		record, err := receipt.Load(filepath.Join(in.Root, receipt.ComponentsDir, id))
		if err != nil {
			return nil, nil, err
		}
		pkg, err := in.Catalog.ComponentPackage(id, in.Inventory.ContentNamespaces)
		if err != nil {
			return nil, nil, err
		}
		component, _ := in.Inventory.Get(id)
		size := pkg.Size
		rows = append(rows, plan.Row{Kind: "Componente", Item: component.Label, Installed: record.Receipt.Get("selection"), Available: pkg.Version, Action: "Reparar", Size: &size})
	}
	if assessment.supportInvalid {
		rows = append(rows, plan.Row{Kind: "Gerado", Item: "Suporte de execução dos mods", Installed: "ausente ou divergente", Available: "derivado dos componentes instalados", Action: "Reparar"})
	}
	for _, binary := range assessment.permissions {
		rows = append(rows, plan.Row{Kind: "Runtime", Item: filepath.Base(binary), Installed: "sem execução", Available: "executável", Action: "Reparar"})
	}
	if assessment.packageOrder {
		rows = append(rows, plan.Row{Kind: "Gerado", Item: "Ordem de PK3", Installed: "ausente ou divergente", Available: "catálogo instalado", Action: "Reparar"})
	}
	if rows == nil {
		rows = []plan.Row{}
	}
	status := "noop"
	if len(rows) > 0 {
		status = "planned"
	}
	return &plan.Plan{Target: in.Root, Operations: rows, Status: status}, assessment, nil
}

// Repair applies the repair plan; it refuses fatal diagnostics.
func Repair(ctx context.Context, in plan.Input) (*Result, error) {
	planned, assessment, err := RepairPlan(in)
	if err != nil {
		return nil, err
	}
	result := &Result{}
	if len(planned.Operations) == 0 {
		return result, nil
	}
	for _, diagnostic := range assessment.diagnostics {
		if diagnosticCategory(diagnostic) == "fatal" {
			return nil, errors.New("O repair encontrou metadados incompletos ou ambíguos e não alterou a instalação. Preserve os arquivos e reexecute o bootstrap install.sh para reconciliar o estado.")
		}
	}
	for _, issue := range assessment.clients {
		if issue.mode == "payload" && !issue.available {
			return nil, errors.New("O plano exige payload validado. A CLI instalada não baixa conteúdo durante repair; reexecute o bootstrap install.sh no mesmo destino.")
		}
	}
	held, err := lock.Acquire(in.Root, "repair")
	if err != nil {
		return nil, err
	}
	defer held.Release()
	tx, err := NewTransaction(in.Root, ".x86qw-repair.")
	if err != nil {
		return nil, err
	}
	defer tx.Close()
	fail := func(err error) (*Result, error) {
		if undoErr := tx.Rollback(); undoErr != nil {
			return nil, fmt.Errorf("%w; rollback incompleto: %v", err, undoErr)
		}
		return nil, err
	}
	chmod := func(path string) error {
		info, err := os.Stat(path)
		if err != nil {
			return err
		}
		previous := info.Mode().Perm()
		if err := os.Chmod(path, previous|0o100); err != nil {
			return err
		}
		tx.undo = append(tx.undo, func() error { return os.Chmod(path, previous) })
		return nil
	}
	for _, issue := range assessment.clients {
		if issue.mode == "permission" {
			if err := chmod(filepath.Join(in.Root, issue.spec.Runtime(issue.channel))); err != nil {
				return fail(err)
			}
		}
	}
	for _, binary := range assessment.permissions {
		if err := chmod(binary); err != nil {
			return fail(err)
		}
	}
	for _, issue := range assessment.clients {
		if issue.mode != "payload" {
			continue
		}
		runtime := filepath.Join(in.Root, issue.spec.Runtime(issue.channel))
		aside := filepath.Join(tx.Stage, "previous-runtime-"+issue.channel)
		if _, err := os.Lstat(runtime); err == nil {
			if err := os.Rename(runtime, aside); err != nil {
				return fail(err)
			}
			tx.undo = append(tx.undo, func() error {
				os.RemoveAll(runtime)
				return os.Rename(aside, runtime)
			})
		}
		if _, err := tx.InstallClientVersion(ctx, in.Catalog, issue.spec, issue.channel, issue.selection); err != nil {
			return fail(err)
		}
	}
	if len(assessment.invalidComponents) > 0 {
		count, err := installBatch(ctx, tx, in, assessment.invalidComponents)
		if err != nil {
			return fail(err)
		}
		result.Installed, result.Files = assessment.invalidComponents, count
	} else if assessment.supportInvalid {
		if err := tx.ReconcilePlaySupport(); err != nil {
			return fail(err)
		}
	}
	if assessment.packageOrder {
		if err := tx.RefreshPackageOrder(); err != nil {
			return fail(err)
		}
	}
	var base map[string]any
	if assessment.recoveredState != nil {
		base = assessment.recoveredState.Migrate("")
	} else if assessment.inferredState != nil {
		base = assessment.inferredState
	} else {
		current, err := state.Read(in.Root)
		if err != nil {
			return fail(err)
		}
		base = current.Migrate("")
	}
	payload, err := repairStateDocument(in, base)
	if err != nil {
		return fail(err)
	}
	if err := tx.WriteState(payload); err != nil {
		return fail(err)
	}
	if _, err := Verify(in.Root); err != nil {
		return fail(fmt.Errorf("verificação após o reparo falhou: %w", err))
	}
	result.Warnings = tx.Warnings
	return result, nil
}

// repairStateDocument mirrors write_install_state with the state's own
// known components and capabilities.
func repairStateDocument(in plan.Input, base map[string]any) ([]byte, error) {
	recorded := []any{}
	var recordedIDs []string
	for _, id := range in.Inventory.IDs() {
		if hasRecord(in.Root, id) {
			recorded = append(recorded, id)
			recordedIDs = append(recordedIDs, id)
		}
	}
	document := map[string]any{
		"capabilities":          base["capabilities"],
		"component_fingerprint": state.Fingerprint(recordedIDs),
		"format":                2,
		"known_components":      base["known_components"],
		"min_cli_version":       "0.7.0",
		"profile":               base["profile"],
		"project":               "x86qw",
		"recorded_components":   recorded,
		"requested_components":  base["requested_components"],
		"state_version":         2,
	}
	return state.MarshalDocument(document)
}
