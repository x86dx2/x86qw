package main

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/x86dx2/x86qw/go/internal/setup/tui"
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
		{nil, 0, "Usage: x86qw [COMMAND] [options]"},
		{[]string{"--target", target}, 0, "Usage: x86qw [COMMAND] [options]"},
		{[]string{"play", "--target", target}, 2, "give the mode (--mode)"},
		{[]string{"install", target}, 2, "give --profile"},
		{[]string{"setup", "--target", target}, 2, "give --profile"},
		{[]string{"host", "--menu", "--target", target}, 2, "needs a terminal"},
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

// English unless the locale is Portuguese; LC_ALL wins over LANG.
func TestInterfaceLanguageFollowsTheLocale(t *testing.T) {
	for locale, want := range map[string]tui.Lang{"pt_BR.UTF-8": tui.PT, "pt-BR": tui.PT, "en_US.UTF-8": tui.EN, "de_DE": tui.EN, "": tui.EN} {
		if got := languageOf(locale); got != want {
			t.Errorf("%q: %s, want %s", locale, got, want)
		}
	}
	t.Setenv("LC_MESSAGES", "")
	t.Setenv("LANG", "pt_BR.UTF-8")
	t.Setenv("LC_ALL", "en_US.UTF-8")
	if got := interfaceLanguage(); got != tui.EN {
		t.Fatalf("LC_ALL=en_US with LANG=pt_BR: %s", got)
	}
	t.Setenv("LC_ALL", "")
	if got := interfaceLanguage(); got != tui.PT {
		t.Fatalf("LANG=pt_BR: %s", got)
	}
}
