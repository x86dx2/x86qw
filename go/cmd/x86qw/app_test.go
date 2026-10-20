package main

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/x86dx2/x86qw/go/internal/play"
)

// Without a terminal nothing opens the interface: x86qw prints its usage
// and the commands that would ask say what they need instead.
func TestWithoutATerminalNothingAsks(t *testing.T) {
	stdinIsTerminal = func() bool { return false }
	t.Cleanup(func() { stdinIsTerminal = func() bool { return isTerminalFile(os.Stdin) } })
	target := filepath.Join(t.TempDir(), "x86qw")
	for _, c := range []struct {
		args []string
		code int
		want string
	}{
		{nil, 0, "Uso: " + play.Launcher() + " <comando> [opções]\nExemplo: " + play.Launcher() + " play\n" + commandList},
		{[]string{"play", "--target", target}, 2, "informe o modo (--mode)"},
		{[]string{"install", target}, 2, "informe --profile"},
		{[]string{"setup", "--target", target}, 2, "uso: x86qw setup --profile"},
		{[]string{"host", "--menu", "--target", target}, 2, "sem terminal"},
		{[]string{"play", "--bots", "zero", "--mode", "duel"}, 2, "argumento --bots"},
		{[]string{"play", "--target", target, "--mode", "duel"}, 1, ""},
	} {
		var stdout, stderr bytes.Buffer
		code := run(c.args, &stdout, &stderr)
		if code != c.code || !strings.Contains(stdout.String()+stderr.String(), c.want) {
			t.Fatalf("x86qw %v: %d\n%s%s", c.args, code, stdout.String(), stderr.String())
		}
	}
}
