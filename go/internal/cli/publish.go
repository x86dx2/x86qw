package cli

import (
	"os"
	"path/filepath"
	"strings"
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

// TidyAside removes the executables earlier updates moved aside on
// Windows, once nothing runs them any more: next to it, or in .x86qw/cli
// for the CLI at the installation's root. Elsewhere there are none.
func TidyAside() {
	if executable, err := os.Executable(); err == nil {
		for _, aside := range []string{executable + asideSuffix,
			filepath.Join(filepath.Dir(executable), filepath.FromSlash(cliDir), BinaryName()+asideSuffix)} {
			entries, _ := os.ReadDir(filepath.Dir(aside))
			for _, entry := range entries {
				if strings.HasPrefix(entry.Name(), filepath.Base(aside)) {
					os.Remove(filepath.Join(filepath.Dir(aside), entry.Name()))
				}
			}
		}
	}
}
