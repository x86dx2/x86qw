package setup

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"slices"
	"strings"
	"sync"
	"time"

	"github.com/x86dx2/x86qw/go/internal/buildinfo"
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

	mu      sync.Mutex
	cat     *catalog.Catalog
	inv     *catalog.Inventory
	roles   map[string]trust.RoleState
	fetched time.Duration // how long authenticating the catalog took
}

var _ Backend = (*Real)(nil)

const (
	corePackage   = "x86qw-core-id1"
	mvdsvPathRoom = 88
)

// load authenticates the catalog once per run.
func (r *Real) load() error {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.cat != nil {
		return nil
	}
	started := time.Now()
	var raw []byte
	if r.CatalogFile != "" {
		data, err := os.ReadFile(r.CatalogFile)
		if err != nil {
			return err
		}
		raw = data
	} else {
		authenticated, err := trust.LoadCatalog(r.Trust)
		if err != nil {
			return err
		}
		raw, r.roles = authenticated.Raw, authenticated.Roles
	}
	cat, err := catalog.Parse(raw)
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
	return catalog.ClientSpec{}, fmt.Errorf("plataforma desconhecida: %s", r.platform())
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
func (r *Real) Catalog(ctx context.Context) (*Catalog, error) {
	if err := r.load(); err != nil {
		return nil, err
	}
	out := &Catalog{Profiles: map[string][]string{}, ClientSize: map[string]int64{}}
	for _, component := range r.inv.Components {
		pkg, err := r.cat.ComponentPackage(component.ID, r.inv.ContentNamespaces)
		if err != nil {
			continue // not published for this catalog: nothing to install
		}
		out.Components = append(out.Components, Component{
			ID: component.ID, Label: component.Label, Description: component.Description,
			Kind: kind(component), Size: pkg.Size, Requires: component.Requires,
		})
	}
	for name, ids := range r.inv.Profiles {
		out.Profiles[name] = append([]string(nil), ids...)
	}
	if core, err := r.cat.ComponentPackage(corePackage, []string{"core"}); err == nil {
		out.CoreSize = core.Size
	}
	for _, spec := range catalog.ClientSpecs {
		if releases, err := r.cat.ClientReleases(spec, "stable"); err == nil && len(releases) > 0 {
			out.ClientSize[spec.Platform] = releases[0].Size
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
func Installed(target string) bool {
	if _, err := os.Stat(filepath.Join(target, ".x86qw", "state.json")); err == nil {
		return true
	}
	for _, spec := range catalog.ClientSpecs {
		for _, channel := range []string{"stable", "nightly"} {
			if _, err := os.Stat(filepath.Join(target, filepath.FromSlash(spec.Receipt(channel)))); err == nil {
				return true
			}
		}
	}
	return false
}

func (r *Real) planInput(target string) plan.Input {
	return plan.Input{Root: target, Catalog: r.cat, Inventory: r.inv, ClientAction: install.ClientAction(target),
		CLIVersion: buildinfo.Version, GOOS: runtime.GOOS, GOARCH: runtime.GOARCH}
}

// Existing describes the installation at target, nil when there is none.
func (r *Real) Existing(ctx context.Context, target string) (*Existing, error) {
	if !Installed(target) {
		return nil, nil
	}
	if err := r.load(); err != nil {
		return nil, err
	}
	existing := &Existing{Target: target, CLIVersion: cliVersion(target)}
	components, _ := receipt.LoadComponents(target)
	existing.Components = len(components)
	for _, record := range components {
		existing.Installed = append(existing.Installed, record.ID)
	}
	choices, err := LoadChoices(target)
	if err != nil {
		return nil, err
	}
	if choices != nil {
		existing.Options = *choices
	} else {
		// Installed before the setup recorded choices: start from the
		// defaults with the profile the installation has.
		existing.Options = Defaults(Play, target)
		if current, err := state.Read(target); err == nil && current.Profile != "" {
			existing.Options.Content = current.Profile
			if current.Profile == "none" {
				existing.Options.Content = "essential"
			}
			existing.Options.Components = current.RequestedComponents
		}
	}
	planned, err := plan.Update(r.planInput(target))
	if err != nil {
		return nil, err
	}
	ids := r.idsByLabel()
	for _, row := range planned.Operations {
		existing.Updates = append(existing.Updates, Update{ID: ids[row.Item], Label: row.Item, From: row.Installed, To: row.Available, Note: row.Kind})
	}
	return existing, nil
}

func (r *Real) idsByLabel() map[string]string {
	ids := map[string]string{}
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

// required is what an installation of options downloads, in bytes.
func (r *Real) required(options Options) int64 {
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
// the catalog and, for a server, its ports. Every value is measured.
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
		checks := []func(Options) Check{r.checkFolder, r.checkDisk, r.checkNetwork, r.checkCatalog}
		if options.Intent.WantsServer() {
			checks = append(checks, r.checkPorts)
		}
		for _, run := range checks {
			if !emit(run(options)) {
				return
			}
		}
	}()
	return out
}

func (r *Real) checkFolder(o Options) Check {
	check := Check{ID: "folder", Title: "Pasta de instalação", Value: o.Target, State: CheckOK}
	if !filepath.IsAbs(o.Target) {
		check.State, check.Detail = CheckFailed, "O caminho precisa ser absoluto."
		return check
	}
	if Installed(o.Target) {
		check.Detail = "Já tem uma instalação x86QW: ela recebe as mudanças."
		if held, err := lock.Acquire(o.Target, "setup"); err != nil {
			check.State, check.Detail = CheckFailed, err.Error()
			check.Fix = "Espere a outra operação terminar ou encerre o servidor com x86qw status --stop."
		} else {
			held.Release()
		}
		return check
	}
	entries, err := os.ReadDir(o.Target)
	switch {
	case errors.Is(err, os.ErrNotExist):
		check.Detail = "Será criada."
	case err != nil:
		check.State, check.Detail = CheckFailed, err.Error()
	case len(entries) > 0:
		check.State, check.Value = CheckWarning, "não está vazia"
		check.Detail = fmt.Sprintf("%s tem %d itens que não são do x86QW.", shortPath(o.Target), len(entries))
		check.Fix = "Os arquivos são preservados; prefira uma pasta vazia em Pasta."
	default:
		check.Detail = "Vazia."
	}
	if check.State == CheckFailed {
		return check
	}
	// MVDSV cuts paths at 128 bytes; its deepest files sit about 40 below
	// the folder, so a deeper folder hides maps and progs from it.
	if o.Intent.WantsServer() && len(o.Target) > mvdsvPathRoom && check.State == CheckOK {
		check.State, check.Value = CheckWarning, fmt.Sprintf("%d caracteres", len(o.Target))
		check.Detail = "O MVDSV não enxerga arquivos em caminhos tão longos e o servidor não sobe."
		check.Fix = fmt.Sprintf("Escolha uma pasta com até %d caracteres em Pasta.", mvdsvPathRoom)
	}
	if parent := existingParent(o.Target); parent != "" {
		probe, err := os.CreateTemp(parent, ".x86qw-setup-*")
		if err != nil {
			check.State, check.Value = CheckFailed, "sem permissão"
			check.Detail = "Não dá para escrever em " + shortPath(parent) + "."
			check.Fix = "Escolha outra pasta em Pasta."
			return check
		}
		probe.Close()
		os.Remove(probe.Name())
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

func (r *Real) checkDisk(o Options) Check {
	check := Check{ID: "disk", Title: "Espaço em disco", State: CheckOK}
	free, err := doctor.FreeBytes(existingParent(o.Target))
	if err != nil {
		check.State, check.Value, check.Detail = CheckWarning, "desconhecido", err.Error()
		return check
	}
	// The download is kept in the cache and extracted into the folder.
	need := r.required(o) * 2
	check.Value = humanSize(free) + " livres"
	switch {
	case free < need:
		check.State = CheckFailed
		check.Detail = fmt.Sprintf("A instalação precisa de %s.", humanSize(need))
		check.Fix = "Libere espaço ou escolha uma pasta em outro disco."
	case free < need*2:
		check.State = CheckWarning
		check.Detail = fmt.Sprintf("Cabe, mas sobra pouco: a instalação ocupa %s.", humanSize(need))
	default:
		check.Detail = fmt.Sprintf("A instalação ocupa %s.", humanSize(need))
	}
	return check
}

func (r *Real) checkNetwork(Options) Check {
	check := Check{ID: "net", Title: "Conexão", State: CheckOK}
	if err := r.load(); err != nil {
		check.State, check.Value = CheckFailed, "sem acesso"
		check.Detail = "qw.x86.com.br não respondeu: " + err.Error()
		check.Fix = "Confira a conexão e tente de novo."
		return check
	}
	check.Value = fmt.Sprintf("%d ms", r.fetched.Milliseconds())
	check.Detail = "Tempo para autenticar o catálogo em qw.x86.com.br."
	return check
}

func (r *Real) checkCatalog(Options) Check {
	check := Check{ID: "catalog", Title: "Catálogo assinado", State: CheckOK}
	if err := r.load(); err != nil {
		check.State, check.Value, check.Detail = CheckFailed, "inválido", err.Error()
		return check
	}
	check.Value = fmt.Sprintf("%d pacotes", len(r.cat.Packages))
	if role, ok := r.roles["timestamp"]; ok {
		check.Detail = fmt.Sprintf("TUF v%d, válido até %s.", r.roles["targets"].Version, role.Expires.Local().Format("02/01/2006 15:04"))
	} else {
		check.Detail = "Lido de um arquivo local."
	}
	return check
}

func (r *Real) checkPorts(o Options) Check {
	ports := o.Server.Ports()
	check := Check{ID: "ports", Title: "Portas do servidor", State: CheckOK, Value: "livres"}
	var requests []services.PortRequest
	for index, port := range ports {
		requests = append(requests, services.PortRequest{Label: fmt.Sprintf("instância #%d", index+1), Address: "0.0.0.0", Port: port, Kind: "udp"})
	}
	if o.Server.QTVPort != 0 {
		requests = append(requests, services.PortRequest{Label: "QTV", Address: "0.0.0.0", Port: o.Server.QTVPort, Kind: "tcp"})
	}
	if o.Server.QWFWDPort != 0 {
		requests = append(requests, services.PortRequest{Label: "QWFWD", Address: "0.0.0.0", Port: o.Server.QWFWDPort, Kind: "udp"})
	}
	if err := services.PreflightPorts(requests); err != nil {
		check.State, check.Value, check.Detail = CheckWarning, "em uso", err.Error()
		check.Fix = "Mude a porta inicial em Servidor, ou instale mesmo assim e libere-a antes de iniciar."
		return check
	}
	check.Detail = fmt.Sprintf("UDP %d–%d livres.", ports[0], ports[len(ports)-1])
	return check
}

func humanSize(b int64) string {
	switch {
	case b >= 1e9:
		return fmt.Sprintf("%.1f GB", float64(b)/1e9)
	case b >= 1e6:
		return fmt.Sprintf("%.0f MB", float64(b)/1e6)
	}
	return fmt.Sprintf("%d KB", b/1000)
}

func shortPath(path string) string {
	if home, err := os.UserHomeDir(); err == nil && strings.HasPrefix(path, home) {
		return "~" + strings.TrimPrefix(path, home)
	}
	return path
}

// labels names every package an installation reports.
func (r *Real) labels() map[string]string {
	labels := map[string]string{corePackage: "Dados do Quake", "client": "ezQuake", "cli": "CLI x86QW"}
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
// already there to them: new components, newer versions and the choices.
func (r *Real) Install(ctx context.Context, options Options, control <-chan bool) <-chan Progress {
	if err := r.load(); err != nil {
		return failed(err)
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
	if Installed(target) {
		return r.apply(ctx, options, selected, labels, control)
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
	return track(ctx, newTracker(lanes, labels), control, func(ctx context.Context) ([]string, error) {
		result, err := install.Install(ctx, target, r.cat, r.inv, install.InstallOptions{
			Platform: r.platform(), Channel: "stable", Profile: profile, Requested: requested,
		})
		if err != nil {
			return nil, err
		}
		return append(result.Warnings, r.finish(target, options)...), nil
	})
}

// apply adds what options select to an existing installation, updates
// what is outdated and records the choices.
func (r *Real) apply(ctx context.Context, options Options, selected []string, labels map[string]string, control <-chan bool) <-chan Progress {
	target := options.Target
	in := r.planInput(target)
	planned, err := plan.Update(in)
	if err != nil {
		return failed(err)
	}
	have := map[string]bool{}
	if components, err := receipt.LoadComponents(target); err == nil {
		for _, record := range components {
			have[record.ID] = true
		}
	}
	resolved, err := r.inv.Resolve(selected)
	if err != nil {
		return failed(err)
	}
	for _, id := range resolved {
		if have[id] {
			continue
		}
		pkg, err := r.cat.ComponentPackage(id, r.inv.ContentNamespaces)
		if err != nil {
			return failed(err)
		}
		size := pkg.Size
		planned.Operations = append(planned.Operations, plan.Row{Kind: "Componente", Item: labels[id], Action: "Adicionar", Available: pkg.Version, Size: &size})
	}
	return track(ctx, newTracker(nil, labels), control, func(ctx context.Context) ([]string, error) {
		var warnings []string
		if len(planned.Operations) > 0 {
			result, err := install.Apply(ctx, "upgrade", in, planned)
			if err != nil {
				return nil, err
			}
			warnings = result.Warnings
		}
		return append(warnings, r.finish(target, options)...), nil
	})
}

// finish records the choices and applies everything that is not a
// package: client settings, server presets, the CLI and the system
// integration. What fails here comes back as a warning; the files are in.
func (r *Real) finish(target string, options Options) []string {
	var warnings []string
	note := func(what string, err error) {
		if err != nil {
			warnings = append(warnings, what+": "+err.Error())
		}
	}
	note("escolhas", SaveChoices(target, options))
	if options.Intent.WantsClient() {
		note("configuração do cliente", WriteClientConfig(target, options, time.Now().Unix()))
	}
	if options.Intent.WantsServer() {
		note("presets do servidor", SaveServerPresets(target, options))
	}
	if r.Publish != nil {
		note("comando x86qw", r.Publish(target))
	}
	if r.Integrate != nil {
		note("integração", r.Integrate(target, options.Integration))
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
		return Report{Problem: found}, err
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
