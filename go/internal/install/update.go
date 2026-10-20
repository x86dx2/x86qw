package install

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/cli"
	"github.com/x86dx2/x86qw/go/internal/download"
	"github.com/x86dx2/x86qw/go/internal/lock"
	"github.com/x86dx2/x86qw/go/internal/plan"
	"github.com/x86dx2/x86qw/go/internal/privfs"
	"github.com/x86dx2/x86qw/go/internal/state"
)

const defaultPreset = "s_raw_volume \"0.2\"\n"

var (
	textureLimit       = regexp.MustCompile(`(?m)^([ \t]*gl_max_size[ \t]+)"?32768"?([ \t]*)(\r?)$`)
	aliasLine          = regexp.MustCompile(`(?im)^\s*alias\s+([^\s]+).*(?:\r?\n|$)`)
	brokenCapabilities = regexp.MustCompile(`(?im)^\s*cl_remote_capabilities\s+"\$cl_remote_capabilities,[^"]*"\s*(?:\r?\n|$)`)
	tempAlias          = regexp.MustCompile(`(?i)^\s*tempalias\s+([^\s]+)`)
)

// Result reports an applied plan.
type Result struct {
	Installed  []string
	Clients    []string
	Files      int
	Warnings   []string
	CLIVersion string // set when the native CLI was replaced
}

// Apply runs a plan computed by plan.Update/plan.Upgrade. Only component
// rows are supported; client, system and legacy rows are left to the Python
// CLI so this never writes something it cannot reproduce byte for byte.
func Apply(ctx context.Context, command string, in plan.Input, planned *plan.Plan) (*Result, error) {
	var outdated, missing []string
	var cliPackage *catalog.Package
	type clientStep struct {
		spec    catalog.ClientSpec
		channel string
		action  string
	}
	var clients []clientStep
	labels := map[string]string{}
	for _, component := range in.Inventory.Components {
		labels[component.Label] = component.ID
	}
	for _, row := range planned.Operations {
		id, known := labels[row.Item]
		switch {
		case row.Kind == "Cliente":
			spec, channel, ok := clientRowTarget(row.Item)
			if !ok {
				return nil, fmt.Errorf("cliente desconhecido no plano: %s", row.Item)
			}
			clients = append(clients, clientStep{spec, channel, row.Action})
		case row.Kind == "Componente" && row.Action == "Atualizar" && known:
			outdated = append(outdated, id)
		case row.Kind == "Componente" && row.Action == "Adicionar" && known:
			missing = append(missing, id)
		case row.Kind == "CLI" && row.Action == "Atualizar":
			pkg, ok, err := in.Catalog.CLIPackage(in.GOOS, in.GOARCH)
			if err != nil || !ok {
				return nil, fmt.Errorf("pacote da CLI x86QW indisponível: %v", err)
			}
			cliPackage = &pkg
		default:
			return nil, fmt.Errorf("esta versão ainda não aplica %q (%s)", row.Item, row.Action)
		}
	}
	result := &Result{}
	if len(outdated)+len(missing)+len(clients) == 0 && cliPackage == nil {
		return result, nil
	}
	if err := refuseConfigMigrations(in.Root, append(append([]string{}, outdated...), missing...)); err != nil {
		return nil, err
	}
	current, err := state.Read(in.Root)
	if err != nil {
		return nil, fmt.Errorf("estado da instalação ilegível: %w", err)
	}
	held, err := lock.Acquire(in.Root, command)
	if err != nil {
		return nil, err
	}
	defer held.Release()
	paks := map[string]string{}
	for _, name := range []string{"id1/pak0.pak", "id1/pak1.pak"} {
		if paks[name], err = fileSHA256(filepath.Join(in.Root, name)); err != nil {
			return nil, err
		}
	}
	tx, err := NewTransaction(in.Root, ".x86qw-components-"+command+".")
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
	// Clients first, like manager.py update(): update_runtime per runtime.
	for _, step := range clients {
		var err error
		switch step.action {
		case "Reparar":
			err = tx.RepairNightly(step.spec)
		default: // Atualizar or Restaurar reinstall the catalog release.
			_, err = tx.InstallClient(ctx, in.Catalog, step.spec, step.channel)
		}
		if err != nil {
			return fail(err)
		}
		result.Clients = append(result.Clients, "ezQuake "+step.spec.Label+" "+step.channel)
	}
	for _, batch := range [][]string{outdated, missing} {
		if len(batch) == 0 {
			continue
		}
		count, err := installBatch(ctx, tx, in, batch)
		if err != nil {
			return fail(err)
		}
		result.Installed = append(result.Installed, batch...)
		result.Files += count
	}
	for name, digest := range paks {
		if actual, _ := fileSHA256(filepath.Join(in.Root, name)); actual != digest {
			return fail(fmt.Errorf("o PAK registrado %s foi alterado durante a atualização", name))
		}
	}
	payload, err := stateDocument(in, current)
	if err != nil {
		return fail(err)
	}
	if err := tx.WriteState(payload); err != nil {
		return fail(err)
	}
	if _, err := Verify(in.Root); err != nil {
		return fail(fmt.Errorf("verificação após a atualização falhou: %w", err))
	}
	if cliPackage != nil {
		// Last: the content is already committed and verified.
		artifact, err := fetchPackage(ctx, tx, *cliPackage)
		if err != nil {
			return nil, fmt.Errorf("conteúdo atualizado, mas a CLI não pôde ser baixada: %w", err)
		}
		if _, err := cli.Install(in.Root, artifact, cliPackage.Version); err != nil {
			return nil, fmt.Errorf("conteúdo atualizado, mas a CLI não pôde ser substituída: %w", err)
		}
		result.CLIVersion = cliPackage.Version
	}
	result.Warnings = tx.Warnings
	return result, nil
}

func installBatch(ctx context.Context, tx *Transaction, in plan.Input, batch []string) (int, error) {
	variant, variantErr := HostVariant()
	total := 0
	for _, id := range batch {
		component, _ := in.Inventory.Get(id)
		pkg, err := in.Catalog.ComponentPackage(id, in.Inventory.ContentNamespaces)
		if err != nil {
			return 0, err
		}
		artifact, err := fetchPackage(ctx, tx, pkg)
		if err != nil {
			return 0, err
		}
		prepared, err := PreparePackage(artifact, pkg, tx.Stage)
		if err != nil {
			return 0, err
		}
		if len(component.PlatformFiles()) > 0 {
			if variantErr != nil {
				return 0, variantErr
			}
			if err := NormalizePlatform(component, prepared.Managed, variant); err != nil {
				return 0, err
			}
		}
		count, err := tx.InstallOverlay(id, prepared.Managed, pkg.Version, pkg.OriginURL, ValidateManagedPath)
		if err != nil {
			return 0, err
		}
		total += count
		for _, item := range prepared.Defaults {
			if err := tx.InstallDefault(item.Source, item.Destination); err != nil {
				return 0, err
			}
		}
	}
	for _, id := range batch {
		if id != "x86qw-client-bootstrap" {
			continue
		}
		if !regular(filepath.Join(tx.Root, "ezquake/configs/preset.cfg")) {
			staged := filepath.Join(tx.Stage, "nquake-default-preset.cfg")
			if err := os.WriteFile(staged, []byte(defaultPreset), 0o644); err != nil {
				return 0, err
			}
			if err := tx.InstallDefault(staged, "ezquake/configs/preset.cfg"); err != nil {
				return 0, err
			}
		}
	}
	if err := tx.RefreshPackageOrder(); err != nil {
		return 0, err
	}
	if err := tx.ReconcilePlaySupport(); err != nil {
		return 0, err
	}
	return total, nil
}

// fetchPackage returns the cached artifact, downloading it into the shared
// installer cache when absent.
func fetchPackage(ctx context.Context, tx *Transaction, pkg catalog.Package) (string, error) {
	pin := download.Artifact{URLs: pkg.URLs, Size: pkg.Size, SHA256: pkg.SHA256}
	root, err := download.CacheDir()
	if err != nil {
		return "", err
	}
	if err := prepareCache(root); err != nil {
		return "", err
	}
	cached := filepath.Join(root, "components", pkg.Filename)
	if regular(cached) {
		if !download.Cached(cached, pin) {
			return "", fmt.Errorf("o pacote %s em cache é inválido; execute cleanup e tente novamente", pkg.Package)
		}
		return cached, nil
	}
	temporary := filepath.Join(tx.Stage, pkg.Package+".download")
	if err := download.Fetch(ctx, pin, temporary, download.Options{Deadline: 30 * time.Minute}); err != nil {
		return "", fmt.Errorf("download de %s falhou: %w", pkg.Package, err)
	}
	if err := os.MkdirAll(filepath.Dir(cached), 0o755); err != nil {
		return "", err
	}
	if err := copyFile(temporary, cached, 0o644); err != nil {
		return "", err
	}
	if !download.Cached(cached, pin) {
		return "", fmt.Errorf("o pacote %s falhou na verificação após a cópia", pkg.Package)
	}
	return cached, nil
}

const cacheMarker = ".x86qw-cache"

// prepareCache creates the private cache with its marker, refusing a
// non-empty directory that the installer does not own.
func prepareCache(root string) error {
	if info, err := os.Lstat(root); err == nil {
		if !info.IsDir() {
			return fmt.Errorf("o caminho reservado ao cache não é um diretório: %s", root)
		}
		if marker, err := os.ReadFile(filepath.Join(root, cacheMarker)); err == nil {
			if string(marker) != "x86qw-cache-v1\n" {
				return fmt.Errorf("marcador do cache inválido: %s", root)
			}
			return nil
		}
		entries, _ := os.ReadDir(root)
		if len(entries) > 0 && !download.AdoptableCache(root) {
			return fmt.Errorf("o diretório de cache contém arquivos que não pertencem ao instalador e foi preservado: %s", root)
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if err := privfs.MkdirAll(root); err != nil {
		return err
	}
	return atomicWrite(filepath.Join(root, cacheMarker), []byte("x86qw-cache-v1\n"), 0o600)
}

// refuseConfigMigrations stops when the Python installer would also rewrite
// personal configs (texture limit, saved aliases, Pro-X base); those
// migrations are not ported yet.
func refuseConfigMigrations(root string, batch []string) error {
	configs, _ := filepath.Glob(filepath.Join(root, "*", "configs", "config.cfg"))
	aliases := map[string]bool{}
	for _, relative := range []string{"qw/autoexec.cfg", "qw/x86qw-ktx.cfg", "arena/x86qw-arena.cfg", "prox/x86qw-prox.cfg", "fortress/x86qw-fortress.cfg", "td2/x86qw-td2.cfg"} {
		data, err := os.ReadFile(filepath.Join(root, relative))
		if err != nil {
			continue
		}
		for _, line := range strings.Split(string(data), "\n") {
			if match := tempAlias.FindStringSubmatch(line); match != nil {
				aliases[strings.ToLower(match[1])] = true
			}
		}
	}
	bootstrap := false
	for _, id := range batch {
		bootstrap = bootstrap || id == "x86qw-client-bootstrap"
	}
	for _, config := range configs {
		data, err := os.ReadFile(config)
		if err != nil {
			return err
		}
		needs := brokenCapabilities.Match(data) || (bootstrap && textureLimit.Match(data))
		for _, match := range aliasLine.FindAllSubmatch(data, -1) {
			needs = needs || aliases[strings.ToLower(string(match[1]))]
		}
		if strings.HasSuffix(filepath.ToSlash(config), "prox/configs/config.cfg") && strings.Contains(string(data), "// Niclas's config") {
			needs = true
		}
		if needs {
			return fmt.Errorf("a configuração pessoal %s usa um formato antigo que esta versão não migra; reinstale o x86QW", config)
		}
	}
	return nil
}

// stateDocument builds state.json like manager.py write_install_state.
func stateDocument(in plan.Input, current *state.State) ([]byte, error) {
	var recorded []string
	for _, id := range in.Inventory.IDs() {
		if hasRecord(in.Root, id) {
			recorded = append(recorded, id)
		}
	}
	requested := current.RequestedComponents
	if requested == nil {
		requested = []string{}
	}
	if recorded == nil {
		recorded = []string{}
	}
	document := map[string]any{
		"capabilities":          []string{},
		"component_fingerprint": state.Fingerprint(recorded),
		"format":                2,
		"known_components":      in.Inventory.IDs(),
		"min_cli_version":       "0.7.0",
		"profile":               current.Profile,
		"project":               "x86qw",
		"recorded_components":   recorded,
		"requested_components":  requested,
		"state_version":         2,
	}
	return state.MarshalDocument(document)
}
