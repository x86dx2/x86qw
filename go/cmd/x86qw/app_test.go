package main

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// withoutTerminal makes the commands believe nobody can answer.
func withoutTerminal(t *testing.T) {
	stdinIsTerminal = func() bool { return false }
	t.Cleanup(func() { stdinIsTerminal = func() bool { return isTerminal(os.Stdin) } })
}

// Without a terminal nothing opens the interface: x86qw prints its usage
// and the commands that would ask say what they need instead.
func TestWithoutATerminalNothingAsks(t *testing.T) {
	withoutTerminal(t)
	target := filepath.Join(t.TempDir(), "x86qw")
	for _, c := range []struct {
		args []string
		code int
		want string
	}{
		{nil, 0, "Uso: x86qw [COMANDO] [opções]"},
		{[]string{"--target", target}, 0, "Uso: x86qw [COMANDO] [opções]"},
		{[]string{"play", "--target", target}, 2, "informe o modo (--mode)"},
		{[]string{"install", target}, 2, "informe --profile"},
		{[]string{"setup", "--target", target}, 2, "informe --profile"},
		{[]string{"host", "--menu", "--target", target}, 2, "precisa de um terminal"},
		{[]string{"play", "--bots", "zero", "--mode", "duel"}, 2, "--bots"},
		{[]string{"play", "--target", target, "--mode", "duel"}, 1, ""},
	} {
		var stdout, stderr bytes.Buffer
		code := run(c.args, &stdout, &stderr)
		if code != c.code || !strings.Contains(stdout.String()+stderr.String(), c.want) {
			t.Fatalf("x86qw %v: %d\n%s%s", c.args, code, stdout.String(), stderr.String())
		}
	}
}
