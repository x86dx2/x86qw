package setup

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"time"

	"github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/doctor"
	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/lock"
	"github.com/x86dx2/x86qw/go/internal/maint"
	"github.com/x86dx2/x86qw/go/internal/plan"
	"github.com/x86dx2/x86qw/go/internal/play"
	"github.com/x86dx2/x86qw/go/internal/receipt"
	"github.com/x86dx2/x86qw/go/internal/services"
	"github.com/x86dx2/x86qw/go/internal/state"
	"github.com/x86dx2/x86qw/go/internal/trust"
)

// Real installs for real: the catalog authenticated with TUF, the
// installer's transactions, the services' ports and the CLI's own files.
type Real struct {
	// Trust locates the TUF metadata cache; CatalogFile, when set, is an
	// already authenticated catalog read instead (tests).
	Trust       trust.Options
	CatalogFile string
	// Publish installs the running executable as the installation's CLI
	// and links the x86qw command; the command line provides it.
	Publish func(target string) error
	// Platform is the client to install, install.HostPlatform() normally.
	Platform string
	// Integrate applies the operating system integration.
	Integrate func(target string, integration Integration) error
	// Run runs an x86qw command (play, open, host) and reports how it
	// ended; the command line provides it.
	Run func(args []string) error
	// Capture runs an x86qw command and returns what it printed; the
	// command line provides it.
	Capture func(args []string) (string, error)

	mu      sync.Mutex
	cat     *catalog.Catalog
	inv     *catalog.Inventory
	roles   map[string]trust.RoleState
	fetched time.Duration // how long authenticating the catalog took
}

var _ Backend = (*Real)(nil)

const corePackage = "x86qw-core-id1"

// load authenticates the catalog once per run; a failure is tried again
// the next time.
func (r *Real) load() error {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.cat != nil {
		return nil
	}
	started := time.Now()
	var payload []byte
	if r.CatalogFile != "" {
		data, err := os.ReadFile(r.CatalogFile)
		if err != nil {
			return err
		}
		payload = data
	} else {
		authenticated, err := trust.LoadCatalog(r.Trust)
		if err != nil {
			return err
		}
		payload, r.roles = authenticated.Raw, authenticated.Roles
	}
	cat, err := catalog.Parse(payload)
	if err != nil {
		return err
	}
	inv, err := catalog.LoadInventory()
	if err != nil {
		return err
	}
	r.cat, r.inv, r.fetched = cat, inv, time.Since(started)
	return nil
}

// unavailable is the error the interface shows when the published catalog
// does not answer.
func unavailable(err error) error { return problem("", "err.catalog", err.Error()) }

func (r *Real) platform() string {
	if r.Platform != "" {
		return r.Platform
	}
	return install.HostPlatform()
}

func (r *Real) spec() (catalog.ClientSpec, error) {
	for _, spec := range catalog.ClientSpecs {
		if spec.Platform == r.platform() {
			return spec, nil
		}
	}
	return catalog.ClientSpec{}, problem("", "err.platform", r.platform())
}

// kind is how the interface groups a component: the programs a server
// runs are "server".
func kind(component catalog.Component) string {
	if component.Kind == "runtime" || component.Kind == "service" {
		return "server"
	}
	return component.Kind
}

// Catalog lists the inventory with the sizes of the catalog's packages.
// When the published catalog does not answer, the inventory alone comes
// back, without sizes, with the reason in Unavailable: an installation
// still opens to play and host.
func (r *Real) Catalog(ctx context.Context) (*Catalog, error) {
	out := &Catalog{Profiles: map[string][]string{}, ClientSize: map[string]int64{}}
	var inv *catalog.Inventory
	if err := r.load(); err != nil {
		out.Unavailable = unavailable(err)
		if inv, err = catalog.LoadInventory(); err != nil {
			return nil, err
		}
	} else {
		inv = r.inv
	}
	for _, component := range inv.Components {
		var size int64
		if out.Unavailable == nil {
			pkg, err := r.cat.ComponentPackage(component.ID, inv.ContentNamespaces)
			if err != nil {
				continue // not published for this catalog: nothing to install
			}
			size = pkg.Size
		}
		out.Components = append(out.Components, Component{
			ID: component.ID, Label: component.Label, Description: component.Description,
			Kind: kind(component), Size: size, Requires: component.Requires,
		})
	}
	for name, ids := range inv.Profiles {
		out.Profiles[name] = append([]string(nil), ids...)
	}
	if out.Unavailable == nil {
		if core, err := r.cat.ComponentPackage(corePackage, []string{"core"}); err == nil {
			out.CoreSize = core.Size
		}
		for _, spec := range catalog.ClientSpecs {
			if releases, err := r.cat.ClientReleases(spec, "stable"); err == nil && len(releases) > 0 {
				out.ClientSize[spec.Platform] = releases[0].Size
			}
		}
	}
	modes, err := play.LoadModes()
	if err != nil {
		return nil, err
	}
	for _, mode := range modes {
		out.Modes = append(out.Modes, modeOf(mode))
	}
	return out, nil
}

// Installed reports whether target holds an x86QW installation: its state
// or a client receipt. What a rolled-back install leaves in .x86qw is not.
func Installed(target string) bool { return install.Installed(target) }

func (r *Real) planInput(target string) plan.Input { return install.PlanInput(target, r.cat, r.inv) }

// Existing describes the installation at target, nil when there is none.
// It reads the installation alone; the updates need the published
// catalog, and when it does not answer UpdatesErr says so.
func (r *Real) Existing(ctx context.Context, target string) (*Existing, error) {
	if !Installed(target) {
		return nil, nil
	}
	existing := &Existing{Target: target, CLIVersion: cliVersion(target)}
	components, _ := receipt.LoadComponents(target)
	existing.Components = len(components)
	for _, record := range components {
		existing.Installed = append(existing.Installed, record.ID)
	}
	choices, err := LoadChoices(target)
	if err != nil {
		// Unreadable choices do not lock the installation: the defaults
		// stand in, and the interface says so.
		existing.Note = Raw(err.Error())
		var p *Problem
		if errors.As(err, &p) {
			existing.Note = p.Text
		}
	}
	if choices != nil {
		existing.Options = *choices
	} else {
		// Installed before the setup recorded choices: start from the
		// defaults with the profile the installation has; with the server
		// programs in, it hosts too.
		intent := Play
		if slices.Contains(existing.Installed, "mvdsv") {
			intent = Both
		}
		existing.Options = Defaults(intent, target)
		if current, err := state.Read(target); err == nil && current.Profile != "" {
			existing.Options.Content = current.Profile
			if current.Profile == "none" {
				existing.Options.Content = "essential"
			}
			existing.Options.Components = current.RequestedComponents
		}
	}
	if err := r.load(); err != nil {
		existing.UpdatesErr = unavailable(err)
		return existing, nil
	}
	planned, err := plan.Update(r.planInput(target))
	if err != nil {
		existing.UpdatesErr = err
		return existing, nil
	}
	ids := r.idsByLabel()
	for _, row := range planned.Operations {
		existing.Updates = append(existing.Updates, Update{ID: ids[row.Item], Label: row.Item, From: planText(row.Installed), To: row.Available, Note: planText(row.Kind)})
	}
	return existing, nil
}

// idsByLabel maps what the update plan calls a package to its lane.
func (r *Real) idsByLabel() map[string]string {
	ids := map[string]string{"x86QW": "cli"}
	for _, component := range r.inv.Components {
		ids[component.Label] = component.ID
	}
	return ids
}

// cliVersion reads the version the installed CLI's receipt records.
func cliVersion(target string) string {
	payload, err := os.ReadFile(filepath.Join(target, ".x86qw", "cli", "receipt"))
	if err != nil {
		return ""
	}
	var cli struct {
		Version string `json:"version"`
	}
	if json.Unmarshal(payload, &cli) != nil {
		return ""
	}
	return cli.Version
}

// Network detects the addresses.
func (r *Real) Network(ctx context.Context) Network { return DetectNetwork(ctx) }

// CheckHostname resolves hostname.
func (r *Real) CheckHostname(ctx context.Context, hostname string, network Network) DNSCheck {
	return CheckHostname(ctx, hostname, network)
}

// required is what installing options downloads, in bytes: everything for
// a new folder, only what the plan adds or updates for an installation.
func (r *Real) required(options Options) int64 {
	if Installed(options.Target) {
		planned, err := r.applyPlan(options)
		if err != nil {
			return 0
		}
		var total int64
		for _, row := range planned.Operations {
			if row.Size != nil {
				total += *row.Size
			}
		}
		return total
	}
	cat, err := r.Catalog(context.Background())
	if err != nil {
		return 0
	}
	total := cat.CoreSize + cat.ClientSize[r.platform()]
	for _, id := range Selected(cat, options) {
		for _, component := range cat.Components {
			if component.ID == id {
				total += component.Size
			}
		}
	}
	return total
}

// Preflight checks the machine against options: the folder, the disk,
// the catalog and, for a server, its path and ports. Every value is
// measured; each check says it started (CheckRunning) before it says how
// it ended.
func (r *Real) Preflight(ctx context.Context, options Options) <-chan Check {
	out := make(chan Check)
	go func() {
		defer close(out)
		emit := func(check Check) bool {
			select {
			case out <- check:
				return true
			case <-ctx.Done():
				return false
			}
		}
		for _, step := range r.steps(options) {
			if !emit(found{ID: step.id, Title: Say("check." + step.id), State: CheckRunning}.check()) {
				return
			}
			if !emit(step.run(options).check()) {
				return
			}
		}
	}()
	return out
}

type checkStep struct {
	id  string
	run func(Options) found
}

// steps are the preflight's checks for options, in order.
func (r *Real) steps(options Options) []checkStep {
	steps := []checkStep{{"folder", r.checkFolder}}
	if options.Intent.WantsServer() {
		steps = append(steps, checkStep{"path", checkPath})
	}
	steps = append(steps, checkStep{"disk", r.checkDisk}, checkStep{"net", r.checkNetwork}, checkStep{"catalog", r.checkCatalog})
	if options.Intent.WantsServer() {
		steps = append(steps, checkStep{"ports", checkPorts})
	}
	return steps
}

func (r *Real) checkFolder(o Options) found {
	check := found{ID: "folder", Title: Say("check.folder"), Value: Raw(o.Target), State: CheckOK}
	if !filepath.IsAbs(o.Target) {
		check.State, check.Detail = CheckFailed, Say("folder.relative")
		return check
	}
	if Installed(o.Target) {
		check.Detail = Say("folder.existing")
		if held, err := lock.Acquire(o.Target, "setup"); err != nil {
			check.State, check.Detail = CheckFailed, Raw(err.Error())
			check.Fix = Say("folder.busy.fix")
		} else {
			held.Release()
		}
		return check
	}
	entries, err := os.ReadDir(o.Target)
	switch {
	case errors.Is(err, os.ErrNotExist):
		check.Detail = Say("folder.new")
	case err != nil:
		check.State, check.Detail = CheckFailed, Raw(err.Error())
	case len(entries) > 0:
		check.State, check.Value = CheckWarning, Say("folder.full")
		check.Detail = Say("folder.full.d", shortPath(o.Target), len(entries))
		check.Fix = Say("folder.full.fix")
	default:
		check.Detail = Say("folder.empty")
	}
	if check.State == CheckFailed {
		return check
	}
	if parent := existingParent(o.Target); parent != "" {
		probe, err := os.CreateTemp(parent, ".x86qw-setup-*")
		if err != nil {
			check.State, check.Value = CheckFailed, Say("folder.denied")
			check.Detail = Say("folder.denied.d", shortPath(parent))
			check.Fix = Say("folder.other")
			return check
		}
		probe.Close()
		os.Remove(probe.Name())
	}
	return check
}

// checkPath warns a server whose folder is too deep for MVDSV, whether the
// installation is new or gains the server now.
func checkPath(o Options) found {
	check := found{ID: "path", Title: Say("check.path"), State: CheckOK, Value: Say("path.chars", len(o.Target)), Detail: Say("path.ok.d")}
	if PathTooLong(o.Target) {
		check.State = CheckWarning
		check.Detail = Say("path.long.d")
		check.Fix = Say("path.long.fix", MVDSVPathRoom)
	}
	return check
}

// existingParent is path or its nearest ancestor that exists.
func existingParent(path string) string {
	for current := path; ; current = filepath.Dir(current) {
		if _, err := os.Stat(current); err == nil {
			return current
		}
		if filepath.Dir(current) == current {
			return ""
		}
	}
}

func (r *Real) checkDisk(o Options) found {
	return diskCheck(existingParent(o.Target), r.required(o))
}

// diskCheck compares the free space at dir with what a download of the
// given size needs: the cache and the extracted files together.
func diskCheck(dir string, download int64) found {
	check := found{ID: "disk", Title: Say("check.disk"), State: CheckOK}
	free, err := doctor.FreeBytes(dir)
	if err != nil {
		check.State, check.Value, check.Detail = CheckWarning, Say("disk.unknown"), Raw(err.Error())
		return check
	}
	need := DiskNeeded(download)
	check.Value = Say("disk.free", Size(free))
	switch {
	case free < need:
		check.State = CheckFailed
		check.Detail = Say("disk.short", Size(need))
		check.Fix = Say("disk.short.fix")
	case free < need*2:
		check.State = CheckWarning
		check.Detail = Say("disk.tight", Size(need))
	default:
		check.Detail = Say("disk.ok", Size(need))
	}
	return check
}

func (r *Real) checkNetwork(Options) found {
	check := found{ID: "net", Title: Say("check.net"), State: CheckOK}
	if err := r.load(); err != nil {
		check.State, check.Value = CheckFailed, Say("net.down")
		check.Detail = Say("net.down.d", err.Error())
		check.Fix = Say("net.down.fix")
		return check
	}
	check.Value = Say("net.ms", r.fetched.Milliseconds())
	check.Detail = Say("net.ok.d")
	return check
}

func (r *Real) checkCatalog(Options) found {
	check := found{ID: "catalog", Title: Say("check.catalog"), State: CheckOK}
	if err := r.load(); err != nil {
		check.State, check.Value, check.Detail = CheckFailed, Say("catalog.bad"), Raw(err.Error())
		return check
	}
	check.Value = Say("catalog.count", len(r.cat.Packages))
	if role, ok := r.roles["timestamp"]; ok {
		check.Detail = Say("catalog.tuf", r.roles["targets"].Version, Date(role.Expires))
	} else {
		check.Detail = Say("catalog.local")
	}
	return check
}

// checkPorts asks the system whether the server's ports are free.
func checkPorts(o Options) found {
	ports := o.Server.Ports()
	check := found{ID: "ports", Title: Say("check.ports"), State: CheckOK, Value: Say("ports.free")}
	var requests []services.PortRequest
	for index, port := range ports {
		requests = append(requests, services.PortRequest{Label: Say("instance", index+1).String(), Address: "0.0.0.0", Port: port, Kind: "udp"})
	}
	if o.Server.QTVPort != 0 {
		requests = append(requests, services.PortRequest{Label: "QTV", Address: "0.0.0.0", Port: o.Server.QTVPort, Kind: "tcp"})
	}
	if o.Server.QWFWDPort != 0 {
		requests = append(requests, services.PortRequest{Label: "QWFWD", Address: "0.0.0.0", Port: o.Server.QWFWDPort, Kind: "udp"})
	}
	if err := services.PreflightPorts(requests); err != nil {
		check.State, check.Value, check.Detail = CheckWarning, Say("ports.busy"), Raw(err.Error())
		check.Fix = Say("ports.busy.fix")
		return check
	}
	check.Detail = Say("ports.ok.d", ports[0], ports[len(ports)-1])
	return check
}

func shortPath(path string) string {
	if home, err := os.UserHomeDir(); err == nil && strings.HasPrefix(path, home) {
		return "~" + strings.TrimPrefix(path, home)
	}
	return path
}

// ExpandHome turns a leading ~ into the home folder, as a shell would.
func ExpandHome(path string) string {
	if path != "~" && !strings.HasPrefix(path, "~/") && !strings.HasPrefix(path, `~\`) {
		return path
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return path
	}
	return filepath.Join(home, path[1:])
}

// labels names every package an installation reports.
func (r *Real) labels() map[string]string {
	labels := map[string]string{corePackage: "Quake data", "client": "ezQuake", "cli": "x86QW CLI"}
	for _, component := range r.inv.Components {
		labels[component.ID] = component.Label
	}
	if spec, err := r.spec(); err == nil {
		if releases, err := r.cat.ClientReleases(spec, "stable"); err == nil && len(releases) > 0 {
			labels["client"] = "ezQuake " + releases[0].Version
		}
	}
	return labels
}

// profileFor is how the installer receives a selection: the profile when
// it is exactly one, else a custom list.
func (r *Real) profileFor(selected []string, options Options) (string, []string) {
	if options.Intent == Play && options.Content != "custom" {
		if desired, err := r.inv.Desired(options.Content, nil); err == nil && slices.Equal(sorted(desired), sorted(selected)) {
			return options.Content, nil
		}
	}
	return "custom", selected
}

func sorted(ids []string) []string {
	out := append([]string(nil), ids...)
	slices.Sort(out)
	return out
}

// Install installs options into a new folder, or brings the installation
// already there to them: the components added and the choices, and with
// Upgrade every newer version as well.
func (r *Real) Install(ctx context.Context, options Options, control <-chan bool) <-chan Progress {
	if err := r.load(); err != nil {
		return failed(unavailable(err))
	}
	if Installed(options.Target) {
		return r.apply(ctx, options, control)
	}
	cat, err := r.Catalog(ctx)
	if err != nil {
		return failed(err)
	}
	target := options.Target
	selected := Selected(cat, options)
	labels := r.labels()
	sizes := map[string]int64{}
	for _, component := range cat.Components {
		sizes[component.ID] = component.Size
	}
	lanes := []Lane{{ID: corePackage, Label: labels[corePackage], Size: cat.CoreSize}, {ID: "client", Label: labels["client"], Size: cat.ClientSize[r.platform()]}}
	resolved, err := r.inv.Resolve(selected)
	if err != nil {
		return failed(err)
	}
	for _, id := range resolved {
		lanes = append(lanes, Lane{ID: id, Label: labels[id], Size: sizes[id]})
	}
	profile, requested := r.profileFor(selected, options)
	return track(ctx, newTracker(lanes, labels), control, func(ctx context.Context) ([]Text, error) {
		result, err := install.Install(ctx, target, r.cat, r.inv, install.InstallOptions{
			Platform: r.platform(), Channel: "stable", Profile: profile, Requested: requested,
		})
		if err != nil {
			return nil, err
		}
		return append(rawTexts(result.Warnings), r.finish(target, options)...), nil
	})
}

// applyPlan is what installing options does to the installation at their
// target: add the components it lacks and, with Upgrade, everything the
// update plan holds.
func (r *Real) applyPlan(options Options) (*plan.Plan, error) {
	cat, err := r.Catalog(context.Background())
	if err != nil {
		return nil, err
	}
	if cat.Unavailable != nil {
		return nil, cat.Unavailable
	}
	planned := &plan.Plan{}
	if options.Upgrade {
		if planned, err = plan.Update(r.planInput(options.Target)); err != nil {
			return nil, err
		}
	}
	have := map[string]bool{}
	if components, err := receipt.LoadComponents(options.Target); err == nil {
		for _, record := range components {
			have[record.ID] = true
		}
	}
	resolved, err := r.inv.Resolve(Selected(cat, options))
	if err != nil {
		return nil, err
	}
	labels := r.labels()
	for _, id := range resolved {
		if have[id] || slices.ContainsFunc(planned.Operations, func(row plan.Row) bool { return row.Item == labels[id] }) {
			continue
		}
		pkg, err := r.cat.ComponentPackage(id, r.inv.ContentNamespaces)
		if err != nil {
			return nil, err
		}
		size := pkg.Size
		planned.Operations = append(planned.Operations, plan.Row{Kind: "Component", Item: labels[id], Action: "Add", Available: pkg.Version, Size: &size})
	}
	return planned, nil
}

// apply brings an existing installation to options and records them. The
// plan's packages are its lanes from the start, so the total holds still.
func (r *Real) apply(ctx context.Context, options Options, control <-chan bool) <-chan Progress {
	target := options.Target
	planned, err := r.applyPlan(options)
	if err != nil {
		return failed(err)
	}
	labels, ids := r.labels(), r.idsByLabel()
	var lanes []Lane
	for _, row := range planned.Operations {
		id := ids[row.Item]
		if row.Kind == "Client" {
			id = "client"
		}
		if id == "" || row.Size == nil {
			continue
		}
		label := labels[id]
		if label == "" {
			label = row.Item
		}
		lanes = append(lanes, Lane{ID: id, Label: label, Size: *row.Size})
	}
	in := r.planInput(target)
	return track(ctx, newTracker(lanes, labels), control, func(ctx context.Context) ([]Text, error) {
		var warnings []Text
		if len(planned.Operations) > 0 {
			result, err := install.Apply(ctx, "upgrade", in, planned)
			if err != nil {
				return nil, err
			}
			warnings = rawTexts(result.Warnings)
		}
		return append(warnings, r.finish(target, options)...), nil
	})
}

func rawTexts(lines []string) []Text {
	var out []Text
	for _, line := range lines {
		out = append(out, Raw(line))
	}
	return out
}

// finish records the choices and applies everything that is not a
// package: client settings, server presets, the CLI and the system
// integration. What fails here comes back as a warning; the files are in.
func (r *Real) finish(target string, options Options) []Text {
	var warnings []Text
	note := func(what string, err error) {
		if err == nil {
			return
		}
		detail := Raw(err.Error())
		var p *Problem
		if errors.As(err, &p) {
			detail = p.Text
		}
		warnings = append(warnings, Say("warn.of", Say(what), detail))
	}
	note("warn.choices", SaveChoices(target, options))
	if options.Intent.WantsClient() {
		note("warn.client", WriteClientConfig(target, options, time.Now().Unix()))
	}
	if options.Intent.WantsServer() {
		note("warn.presets", SaveServerPresets(target, options))
	}
	if r.Publish != nil {
		note("warn.cli", r.Publish(target))
	}
	if r.Integrate != nil {
		note("warn.integ", r.Integrate(target, options.Integration))
	}
	return warnings
}

// Repair verifies target and, when something is wrong, repairs it.
func (r *Real) Repair(ctx context.Context, target string) (Report, error) {
	report, err := install.Verify(target)
	if err == nil {
		return Report{Files: report.Files, Components: len(report.Components)}, nil
	}
	found := err.Error()
	if err := r.load(); err != nil {
		return Report{Problem: found}, unavailable(err)
	}
	if _, err := install.Repair(ctx, r.planInput(target)); err != nil {
		return Report{Problem: found}, err
	}
	report, err = install.Verify(target)
	if err != nil {
		return Report{Problem: found}, err
	}
	return Report{Files: report.Files, Components: len(report.Components), Problem: found, Repaired: true}, nil
}

// Uninstall removes the managed content, or everything without keepPersonal.
func (r *Real) Uninstall(ctx context.Context, target string, keepPersonal bool) error {
	if keepPersonal {
		_, err := maint.Uninstall(target)
		return err
	}
	_, err := maint.Purge(target)
	return err
}

// failed is a progress stream that ends with err at once.
func failed(err error) <-chan Progress {
	out := make(chan Progress, 1)
	out <- Progress{Err: err}
	close(out)
	return out
}
