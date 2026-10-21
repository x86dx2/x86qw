package main

import (
	"bytes"
	"path/filepath"
	"strings"
	"testing"
)

// Every listed command answers --help and `x86qw help NAME` with its
// usage, and the listing names every one of them.
func TestEveryCommandHasHelp(t *testing.T) {
	var listing, stderr bytes.Buffer
	if code := run([]string{"help"}, &listing, &stderr); code != 0 {
		t.Fatalf("help: %d %s", code, stderr.String())
	}
	names := publicCommandNames()
	if len(names) < 20 {
		t.Fatalf("poucos comandos: %v", names)
	}
	for _, name := range names {
		if !strings.Contains(listing.String(), "\n  "+name) {
			t.Errorf("x86qw help não lista %s", name)
		}
		for _, args := range [][]string{{name, "--help"}, {name, "-h"}, {"help", name}} {
			var stdout, stderr bytes.Buffer
			if code := run(args, &stdout, &stderr); code != 0 || !strings.HasPrefix(stdout.String(), "Uso: x86qw "+name) {
				t.Errorf("x86qw %v: %d\n%s%s", args, code, stdout.String(), stderr.String())
			}
		}
	}
}

// Flags go before or after the installation, and mistakes say where to
// look, with exit 2.
func TestFlagsGoAnywhere(t *testing.T) {
	target := t.TempDir()
	for _, args := range [][]string{
		{"verify", target, "--json"},
		{"verify", "--json", target},
		{"verify", "--json", "--target", target},
	} {
		var stdout, stderr bytes.Buffer
		run(args, &stdout, &stderr)
		if !strings.HasPrefix(stdout.String(), `{"command":"verify"`) {
			t.Errorf("x86qw %v: %s%s", args, stdout.String(), stderr.String())
		}
	}
	var stdout, stderr bytes.Buffer
	if code := run([]string{"update", target, "--dry-run", "--nope"}, &stdout, &stderr); code != 2 ||
		!strings.Contains(stderr.String(), "x86qw update: erro: opção desconhecida: --nope") || !strings.Contains(stderr.String(), "x86qw update --help") {
		t.Fatalf("%d %s", code, stderr.String())
	}
	stderr.Reset()
	if code := run([]string{"verify", target, "--target", filepath.Join(target, "outra")}, &stdout, &stderr); code != 2 {
		t.Fatalf("destinos divergentes: %d %s", code, stderr.String())
	}
}

// What changes or stops something asks on a terminal; without one it
// needs --yes and exits 2.
func TestChangesNeedYesWithoutATerminal(t *testing.T) {
	withoutTerminal(t)
	target := t.TempDir()
	for _, args := range [][]string{
		{"uninstall", target},
		{"uninstall", "--purge", target},
	} {
		var stdout, stderr bytes.Buffer
		code := run(args, &stdout, &stderr)
		if code != 2 || !strings.Contains(stderr.String(), "Repita com --yes") {
			t.Errorf("x86qw %v: %d\n%s%s", args, code, stdout.String(), stderr.String())
		}
	}
}

func TestConfirmAsksOnATerminal(t *testing.T) {
	stdinIsTerminal = func() bool { return true }
	t.Cleanup(func() { stdinIsTerminal = func() bool { return false } })
	for answer, want := range map[string]bool{"s\n": true, "sim\n": true, "\n": false, "n\n": false} {
		stdin = strings.NewReader(answer)
		var stdout, stderr bytes.Buffer
		if proceed, code := confirm("Seguir?", "x86qw x", false, &stdout, &stderr); proceed != want || code != 0 {
			t.Errorf("%q: %v %d", answer, proceed, code)
		}
	}
}
