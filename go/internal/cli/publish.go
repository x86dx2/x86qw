package cli

import (
	"os"
	"path/filepath"
)

// Publish installs the running executable as root's CLI when version
// replaces what is there: never a development build, never an older
// version, the same one only with sameToo (which refreshes the binary
// and receipt). The Linux entries root already has are rewritten in the
// current format. With link it then links the user command, or creates
// the shortcuts on Windows, and returns where, "" when nothing was linked.
func Publish(root, version string, sameToo, link bool) (string, error) {
	if !Replaces(version, root, sameToo) {
		return "", nil
	}
	executable, err := os.Executable()
	if err != nil {
		return "", err
	}
	if resolved, err := filepath.EvalSymlinks(executable); err == nil {
		executable = resolved
	}
	// Install reads the whole executable before writing, so it can
	// replace the very file that is running.
	layer, err := Install(root, executable, version)
	if err != nil {
		return "", err
	}
	home, _ := os.UserHomeDir()
	RefreshEntries(root, home)
	if !link {
		return "", nil
	}
	command, err := LinkUserCommand(root, home)
	if err != nil {
		layer.Rollback()
		return "", err
	}
	return command, nil
}

// asideSuffix names the Windows executable an update moved aside.
const asideSuffix = ".old"

// TidyAside removes the executable an earlier update moved aside on
// Windows, once nothing runs it any more: next to it, or in .x86qw/cli
// for the CLI at the installation's root. Elsewhere there is none.
func TidyAside() {
	if executable, err := os.Executable(); err == nil {
		os.Remove(executable + asideSuffix)
		os.Remove(filepath.Join(filepath.Dir(executable), filepath.FromSlash(cliDir), BinaryName()+asideSuffix))
	}
}
