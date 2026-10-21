package cli

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
)

// StillLegacy lists what still runs root's CLI through the layout before
// 2.5: the command link, the Linux entries, the macOS applet, the Windows
// shortcuts and handlers. Retire waits until the list is empty, so a
// repoint that failed never leaves something pointing at nothing.
func StillLegacy(root, home string) []string {
	var found []string
	if runtime.GOOS != "windows" {
		command := filepath.Join(home, ".local", "bin", "x86qw")
		if samePath(LinkTarget(command), filepath.Join(root, "x86qw.sh")) {
			found = append(found, command)
		}
	}
	if runtime.GOOS == "linux" {
		shim := filepath.Join(root, "x86qw.sh")
		for _, entry := range []string{DesktopEntryPath(home), handlerEntryPath(home)} {
			payload, err := os.ReadFile(entry)
			if err == nil && (strings.Contains(string(payload), "Exec="+execArgument(shim)+" ") || strings.Contains(string(payload), `Exec="`+shim+`"`)) {
				found = append(found, entry)
			}
		}
	}
	return append(found, legacyHandlers(root)...)
}
