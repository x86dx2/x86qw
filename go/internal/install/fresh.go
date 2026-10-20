package install

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"

	"github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/lock"
	"github.com/x86dx2/x86qw/go/internal/plan"
	"github.com/x86dx2/x86qw/go/internal/state"
)

const corePackage = "x86qw-core-id1"

// InstallOptions selects what a fresh installation contains.
type InstallOptions struct {
	Platform  string   // macos, linux or windows
	Channel   string   // stable or nightly
	Release   string   // client version; "" or "latest" is the newest
	Profile   string   // essential, recommended, complete, custom or none (client only)
	Requested []string // the components chosen for the custom profile
}

// HostPlatform maps GOOS to the installer's platform keys.
func HostPlatform() string {
	switch runtime.GOOS {
	case "darwin":
		return "macos"
	case "windows":
		return "windows"
	}
	return "linux"
}

// Install creates a new installation like `x86qw install --non-interactive`:
// base PAKs from the core package, the ezQuake client, the profile's
// components and state.json, then verifies the result.
func Install(ctx context.Context, target string, cat *catalog.Catalog, inventory *catalog.Inventory, options InstallOptions) (*Result, error) {
	var spec *catalog.ClientSpec
	for index := range catalog.ClientSpecs {
		if catalog.ClientSpecs[index].Platform == options.Platform {
			spec = &catalog.ClientSpecs[index]
		}
	}
	if spec == nil {
		return nil, fmt.Errorf("plataforma desconhecida: %s", options.Platform)
	}
	requested := []string{}
	switch options.Profile {
	case "essential", "recommended", "complete", "none":
	case "custom":
		if len(options.Requested) == 0 {
			return nil, errors.New("Nenhum componente x86QW foi selecionado.")
		}
		requested = options.Requested
	default:
		return nil, fmt.Errorf("perfil inválido: %s", options.Profile)
	}
	selected, err := inventory.Desired(options.Profile, requested)
	if err != nil {
		return nil, err
	}
	// Like macos_game_directory_reset_required: only a target without any
	// managed ezQuake gets its macOS directory preferences cleared. Adding a
	// channel to (or reinstalling) an existing installation is allowed.
	// X86QW_NO_MACOS_PREFERENCES keeps tests and oracles from touching the
	// user's real ezQuake preferences.
	resetMacPreferences := runtime.GOOS == "darwin" && spec.Platform == "macos" && os.Getenv("X86QW_NO_MACOS_PREFERENCES") == ""
	for _, channel := range []string{"stable", "nightly"} {
		for _, relative := range []string{spec.Runtime(channel), spec.Receipt(channel), legacyClientReceipt(*spec, channel)} {
			if _, err := os.Lstat(filepath.Join(target, filepath.FromSlash(relative))); err == nil {
				resetMacPreferences = false
			}
		}
	}
	held, err := lock.Acquire(target, "install")
	if err != nil {
		return nil, err
	}
	defer held.Release()
	target = lock.Resolve(target)
	if err := os.Mkdir(filepath.Join(target, "id1"), 0o755); err != nil && !errors.Is(err, os.ErrExist) {
		return nil, err
	}
	tx, err := NewTransaction(target, ".quake-install.")
	if err != nil {
		return nil, err
	}
	defer tx.Close()
	tx.observe = observing(ctx)
	fail := func(err error) (*Result, error) {
		if undoErr := tx.Rollback(); undoErr != nil {
			return nil, fmt.Errorf("%w; rollback incompleto: %v", err, undoErr)
		}
		return nil, err
	}
	if err := tx.provisionPaks(ctx, cat); err != nil {
		return fail(err)
	}
	if _, err := tx.InstallClientRelease(ctx, cat, *spec, options.Channel, options.Release); err != nil {
		return fail(err)
	}
	tx.observer().files("client", 1)
	tx.observer().file("client", spec.Runtime(options.Channel), 0, false)
	if resetMacPreferences {
		for _, key := range []string{"basedir", "version", "NSOSPLastRootDirectory"} {
			exec.Command("defaults", "delete", "com.ezquake.ezQuake", key).Run()
		}
	}
	in := plan.Input{Root: target, Catalog: cat, Inventory: inventory}
	count, err := installBatch(ctx, tx, in, selected)
	if err != nil {
		return fail(err)
	}
	payload, err := stateDocument(in, &state.State{Profile: options.Profile, RequestedComponents: requested})
	if err != nil {
		return fail(err)
	}
	if err := tx.WriteState(payload); err != nil {
		return fail(err)
	}
	if _, err := Verify(target); err != nil {
		return fail(fmt.Errorf("verificação após a instalação falhou: %w", err))
	}
	return &Result{Installed: selected, Files: count, Warnings: tx.Warnings}, nil
}

// provisionPaks installs id1/pak0.pak and pak1.pak from the core package.
func (t *Transaction) provisionPaks(ctx context.Context, cat *catalog.Catalog) error {
	required := map[string]string{"pak0.pak": pak0SHA256, "pak1.pak": pak1SHA256}
	missing := false
	for name, digest := range required {
		path := t.path("id1/" + name)
		if _, err := os.Lstat(path); err == nil {
			if err := checkPak(path, digest); err != nil {
				return err
			}
			continue
		}
		missing = true
	}
	if !missing {
		return nil
	}
	pkg, err := cat.ComponentPackage(corePackage, []string{"core"})
	if err != nil {
		return err
	}
	if err := t.observer().begin(ctx, corePackage, pkg.Size); err != nil {
		return err
	}
	artifact, err := fetchPackage(ctx, t, corePackage, pkg)
	if err != nil {
		return err
	}
	prepared, err := PreparePackage(artifact, pkg, t.Stage)
	if err != nil {
		return err
	}
	files, err := regularFiles(prepared.Managed)
	if err != nil {
		return err
	}
	if len(prepared.Defaults) > 0 || len(files) != 2 || files[0] != "id1/pak0.pak" || files[1] != "id1/pak1.pak" {
		return errors.New("o pacote de dados base contém arquivos inesperados")
	}
	var created []string
	t.observer().files(corePackage, 2)
	for _, name := range []string{"pak0.pak", "pak1.pak"} {
		source := filepath.Join(prepared.Managed, "id1", name)
		if err := checkPak(source, required[name]); err != nil {
			return err
		}
		destination := t.path("id1/" + name)
		if _, err := os.Lstat(destination); err == nil {
			continue
		}
		if err := copyFile(source, destination, 0o644); err != nil {
			return err
		}
		created = append(created, destination)
		if info, err := os.Stat(destination); err == nil {
			t.observer().file(corePackage, "id1/"+name, info.Size(), false)
		}
	}
	t.undo = append(t.undo, func() error {
		var errs []error
		for _, path := range created {
			errs = append(errs, ignoreMissing(os.Remove(path)))
		}
		return errors.Join(errs...)
	})
	return nil
}
