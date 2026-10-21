package main

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/x86dx2/x86qw/go/internal/buildinfo"
	"github.com/x86dx2/x86qw/go/internal/cli"
	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/lock"
	"github.com/x86dx2/x86qw/go/internal/setup"
)

// Since 2.5 everything runs root/x86qw[.exe]; before, x86qw.sh or x86qw.cmd
// launched .x86qw/cli/x86qw[.exe]. An installation moves over when a newer
// CLI publishes itself or, after an older CLI's update left the new binary
// in .x86qw/cli, when that binary first runs.

// layoutAttempt marks the last time settleLayout tried, so a move that
// keeps failing does not slow every command down.
const layoutAttempt = ".x86qw/cli/layout-attempt"

// retireLegacy points what the setup integrated at root's CLI again and
// then removes the layout before 2.5. Whatever still points at the old
// launchers keeps them, still working, for a later run.
func retireLegacy(target string, out io.Writer) {
	if _, err := os.Stat(cli.Command(target)); err != nil || !cli.Legacy(target) {
		return
	}
	if choices, _ := setup.LoadChoices(target); choices != nil {
		// Errors about what belongs to other programs do not matter here:
		// StillLegacy only looks at what is this installation's.
		setup.Integrate(target, choices.Integration)
	}
	home, _ := os.UserHomeDir()
	if left := cli.StillLegacy(target, home); len(left) > 0 {
		fmt.Fprintf(out, "! The old x86qw launcher stays while these still use it: %s\n", strings.Join(left, ", "))
		return
	}
	if err := cli.Retire(target); err != nil {
		fmt.Fprintf(out, "! The old x86qw launcher could not be removed: %v\n", err)
		return
	}
	os.Remove(filepath.Join(target, filepath.FromSlash(layoutAttempt)))
}

// settleLayout moves this CLI's installation to the layout of 2.5 when it
// still has the old one and this is its installed version. It never fails
// the command: a running server holds the lock, and the move waits.
func settleLayout(stderr io.Writer) {
	executable, err := os.Executable()
	if err != nil {
		return
	}
	if resolved, err := filepath.EvalSymlinks(executable); err == nil {
		executable = resolved
	}
	root, ok := layoutRoot(executable)
	if !ok || !cli.Legacy(root) || cli.InstalledVersion(root) != buildinfo.Version || !cli.Replaces(buildinfo.Version, root, true) {
		return
	}
	marker := filepath.Join(root, filepath.FromSlash(layoutAttempt))
	if info, err := os.Stat(marker); err == nil && time.Since(info.ModTime()) < 24*time.Hour {
		return
	}
	held, err := lock.Acquire(root, "install")
	if err != nil {
		return
	}
	defer held.Release()
	os.WriteFile(marker, nil, 0o644)
	link := true
	if choices, _ := setup.LoadChoices(root); choices != nil {
		link = choices.Integration.Path
	}
	if err := publishCLI(root, link, io.Discard); err != nil {
		fmt.Fprintf(stderr, "! x86QW could not move to its new layout yet: %v\n", err)
	}
}

// layoutRoot is the installation executable is the CLI of: as
// root/x86qw[.exe], or as root/.x86qw/cli/x86qw[.exe] before 2.5.
func layoutRoot(executable string) (string, bool) {
	if filepath.Base(executable) != cli.BinaryName() {
		return "", false
	}
	dir := filepath.Dir(executable)
	for _, root := range []string{dir, filepath.Clean(filepath.Join(dir, "..", ".."))} {
		if install.Installed(root) {
			return root, true
		}
	}
	return "", false
}
