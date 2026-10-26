package install

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/archive"
	"github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/lock"
	"github.com/x86dx2/x86qw/go/internal/macos"
	"github.com/x86dx2/x86qw/go/internal/plan"
	"github.com/x86dx2/x86qw/go/internal/receipt"
	"github.com/x86dx2/x86qw/go/internal/state"
)

// Repair plans and restores managed content.

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
	changed   bool // the runtime differs from its receipt: repair keeps a copy
}

// RepairAssessment is what a repair found wrong.
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
	if strings.Contains(diagnostic, "runtime present without a receipt; kept") {
		return "advisory"
	}
	return "fatal"
}

func assessRepair(in plan.Input) (*RepairAssessment, error) {
	root := in.Root
	if err := CheckPaks(root); err != nil {
		return nil, err
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
	for id := range valid {
		if !generatedRecords[id] {
			installed[id] = true
		}
	}
	statePath := filepath.Join(root, state.Path)
	if info, err := os.Lstat(statePath); err == nil {
		current, readErr := state.Read(root)
		switch {
		case !info.Mode().IsRegular():
			assessment.diagnostics = append(assessment.diagnostics, fmt.Sprintf("state.json: invalid installation state: %s", statePath))
		case readErr != nil:
			assessment.diagnostics = append(assessment.diagnostics, fmt.Sprintf("state.json: invalid installation state: %s", statePath))
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
				assessment.diagnostics = append(assessment.diagnostics, "state.json records components without intact metadata: "+strings.Join(missing, ", "))
			} else if len(recorded) != len(installed) {
				assessment.recoveredState = current
			}
		}
	} else if len(assessment.diagnostics) > 0 {
		assessment.diagnostics = append(assessment.diagnostics, "state.json is missing and partial metadata prevents a safe inference")
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
		for _, channel := range catalog.Channels {
			runtime := filepath.Join(root, spec.Runtime(channel))
			receiptPath := filepath.Join(root, filepath.FromSlash(spec.Receipt(channel)))
			if _, err := os.Lstat(receiptPath); err != nil {
				if _, err := os.Lstat(runtime); err == nil {
					assessment.diagnostics = append(assessment.diagnostics, fmt.Sprintf("ezQuake %s %s: runtime present without a receipt; kept", spec.Label, channel))
				}
				continue
			}
			payload, err := os.ReadFile(receiptPath)
			var rec *receipt.Receipt
			if err == nil {
				rec, err = receipt.ParseClient(payload, spec.Context(channel))
			}
			if err != nil {
				assessment.diagnostics = append(assessment.diagnostics, fmt.Sprintf("ezQuake %s %s: invalid or partial receipt (%v)", spec.Label, channel, err))
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
				issue.reason = "runtime missing"
				if !available {
					issue.reason = "runtime missing; the payload needs the bootstrap"
				}
				assessment.clients = append(assessment.clients, issue)
				continue
			}
			if spec.Platform == "linux" && regular(runtime) && executableMissing(runtime) {
				if digest, _ := archive.FileSHA256(runtime); digest == rec.Get("binary_sha256") {
					issue.reason, issue.mode = "AppImage without execute permission", "permission"
					assessment.clients = append(assessment.clients, issue)
					continue
				}
			}
			if _, err := verifyClient(root, spec, channel); err != nil {
				issue.reason, issue.changed = "runtime missing, incomplete or different", true
				assessment.clients = append(assessment.clients, issue)
				continue
			}
			if spec.Platform == "macos" && channel == "stable" {
				if identities, ok := macos.StableBinaryIdentities[rec.Get("artifact_sha256")]; ok && identities.Legacy == rec.Get("binary_sha256") {
					issue.reason = "x86QW re-signed bundle; restore the intact upstream bundle"
					assessment.clients = append(assessment.clients, issue)
				}
			}
		}
	}
	return assessment, nil
}

// inferState rebuilds the state of an installation whose state.json is
// missing, from what is installed.
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
		rows = append(rows, plan.Row{Kind: "Diagnostic " + diagnosticCategory(diagnostic), Item: "Managed metadata", Installed: diagnostic, Available: "keep and reconcile through the bootstrap", Action: "Inspect"})
	}
	if assessment.recoveredState != nil || assessment.inferredState != nil {
		rows = append(rows, plan.Row{Kind: "Metadata", Item: "state.json", Installed: "missing or out of date", Available: "rebuilt from the intact receipts", Action: "Repair"})
	}
	for _, issue := range assessment.clients {
		rows = append(rows, plan.Row{Kind: "Client", Item: fmt.Sprintf("ezQuake %s %s", issue.spec.Label, issue.channel), Installed: issue.reason, Available: issue.selection, Action: "Repair"})
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
		rows = append(rows, plan.Row{Kind: "Component", Item: component.Label, Installed: record.Receipt.Get("selection"), Available: pkg.Version, Action: "Repair", Size: &size})
	}
	if assessment.supportInvalid {
		rows = append(rows, plan.Row{Kind: "Generated", Item: "Mod run support", Installed: "missing or different", Available: "derived from the installed components", Action: "Repair"})
	}
	for _, binary := range assessment.permissions {
		rows = append(rows, plan.Row{Kind: "Runtime", Item: filepath.Base(binary), Installed: "not executable", Available: "executable", Action: "Repair"})
	}
	if assessment.packageOrder {
		rows = append(rows, plan.Row{Kind: "Generated", Item: "PK3 order", Installed: "missing or different", Available: "installed catalog", Action: "Repair"})
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
			return nil, errors.New("Repair found incomplete or ambiguous metadata and did not change the installation. Keep the files and run the install.sh bootstrap again to reconcile the state.")
		}
	}
	for _, issue := range assessment.clients {
		if issue.mode == "payload" && !issue.available {
			return nil, errors.New("The plan needs a validated payload. The installed CLI downloads no content during repair; run the install.sh bootstrap again on the same target.")
		}
	}
	held, err := lock.Acquire(in.Root, "repair")
	if err != nil {
		return nil, err
	}
	defer held.Release()
	tx, err := NewTransaction(in.Root, ".x86qw-repair.")
	if tx != nil {
		tx.observe, tx.repair = observing(ctx), true
	}
	if err != nil {
		return nil, err
	}
	defer tx.Close()
	fail := func(err error) (*Result, error) {
		if undoErr := tx.Rollback(); undoErr != nil {
			return nil, fmt.Errorf("%w; incomplete rollback: %v", err, undoErr)
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
		if issue.changed {
			aside = keptCopy(runtime)
			tx.warn("Changed ezQuake restored; your copy is %s", aside)
		}
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
		return fail(fmt.Errorf("verification after the repair failed: %w", err))
	}
	result.Warnings = tx.Warnings
	return result, nil
}

// repairStateDocument writes state.json again with the state's own known
// components and capabilities.
func repairStateDocument(in plan.Input, base map[string]any) ([]byte, error) {
	recorded, err := recordedComponents(in.Root)
	if err != nil {
		return nil, err
	}
	document := map[string]any{
		"capabilities":          base["capabilities"],
		"component_fingerprint": state.Fingerprint(recorded),
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
