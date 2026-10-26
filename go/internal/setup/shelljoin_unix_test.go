//go:build !windows

package setup

import (
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// The command that repeats an installation, pasted into sh or into zsh,
// macOS's shell, runs with the same arguments: zsh reads a word starting
// with "=" as a command to look up, and clan tags start with one.
func TestPrintedCommandsRunInPOSIXShells(t *testing.T) {
	o := Defaults(Both, filepath.Join(t.TempDir(), "My Games", "it's $HOME"))
	o.Nick, o.Server.Name = "=BB=Nelson", "=BB= duel #1"
	args := o.Recipe()
	for _, shell := range [][]string{{"sh", "-c"}, {"zsh", "-f", "-c"}} {
		path, err := exec.LookPath(shell[0])
		if err != nil {
			continue
		}
		script := `x86qw() { printf '%s\n' "$@"; }; ` + ShellJoin(args)
		output, err := exec.Command(path, append(shell[1:], script)...).CombinedOutput()
		if got := strings.TrimSuffix(string(output), "\n"); err != nil || got != strings.Join(args[1:], "\n") {
			t.Fatalf("%s: %s\n%v\n%s", shell[0], ShellJoin(args), err, output)
		}
	}
}
