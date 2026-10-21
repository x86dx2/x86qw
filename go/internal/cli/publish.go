package cli

import (
	"os"
	"path/filepath"
)

// Publish installs the running executable as root's CLI when version
// replaces what is there: never a development build, never an older
// version, the same one only with sameToo (which refreshes the launcher
// and receipt). With link it then links the user command, or creates the
// shortcuts on Windows, and returns where, "" when nothing was linked.
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
	if err != nil || !link {
		return "", err
	}
	home, _ := os.UserHomeDir()
	command, err := LinkUserCommand(root, home)
	if err != nil {
		layer.Rollback()
		return "", err
	}
	return command, nil
}
