package install

import (
	"os"
	"path/filepath"
	"runtime"

	"github.com/x86dx2/x86qw/go/internal/buildinfo"
	"github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/plan"
)

// Installed reports whether root holds an x86QW installation: its state or
// an ezQuake receipt. The CLI alone, which a cancelled first install can
// leave behind, is not one.
func Installed(root string) bool {
	if info, err := os.Stat(filepath.Join(root, ".x86qw", "state.json")); err == nil && info.Mode().IsRegular() {
		return true
	}
	for _, spec := range catalog.ClientSpecs {
		for _, channel := range catalog.Channels {
			if _, err := os.Stat(filepath.Join(root, filepath.FromSlash(spec.Receipt(channel)))); err == nil {
				return true
			}
		}
	}
	return false
}

// DefaultTarget is the installation a command uses when none is given: the
// one holding the running executable (the CLI lives in .x86qw/cli), else
// ~/Games/x86qw, where the installer puts a new one.
func DefaultTarget() string {
	if executable, err := os.Executable(); err == nil {
		if resolved, err := filepath.EvalSymlinks(executable); err == nil {
			dir := filepath.Dir(resolved)
			for _, candidate := range []string{dir, filepath.Join(dir, "..", "..")} {
				if info, err := os.Stat(filepath.Join(candidate, ".x86qw")); err == nil && info.IsDir() {
					return filepath.Clean(candidate)
				}
			}
		}
	}
	home, _ := os.UserHomeDir()
	return filepath.Join(home, "Games", "x86qw")
}

// PlanInput is what update, upgrade and the app plan against for root on
// this machine.
func PlanInput(root string, cat *catalog.Catalog, inv *catalog.Inventory) plan.Input {
	return plan.Input{Root: root, Catalog: cat, Inventory: inv, ClientAction: ClientAction(root),
		CLIVersion: buildinfo.Version, GOOS: runtime.GOOS, GOARCH: runtime.GOARCH}
}
