package main

import (
	"bytes"
	"path/filepath"
	"strings"
	"testing"
)

func TestOpenDryRunShowsTheRulesetAndTheLink(t *testing.T) {
	target := t.TempDir()
	var stdout, stderr bytes.Buffer
	if code := run([]string{"open", "--target", target, "--dry-run", "qw://qw.x86.com.br:27500"}, &stdout, &stderr); code != 0 {
		t.Fatalf("%d %s", code, stderr.String())
	}
	if got := stdout.String(); !strings.Contains(got, "+set x86qw_ruleset 1") || !strings.HasSuffix(strings.TrimSpace(got), "+qwurl qw://qw.x86.com.br:27500") {
		t.Fatalf("command: %s", got)
	}
}

// The qw:// handler on Windows runs `x86qw open --target ROOT -- "%1"`. A
// link with a raw quote splits into more arguments after "--", which open
// refuses instead of reading a second --target from them.
func TestOpenRefusesALinkSplitAfterTheOptions(t *testing.T) {
	target := t.TempDir()
	var stdout, stderr bytes.Buffer
	if code := run([]string{"open", "--target", target, "--dry-run", "--", "qw://x", "--target", `\\evil\share`}, &stdout, &stderr); code != 2 {
		t.Fatalf("code %d: %s %s", code, stdout.String(), stderr.String())
	}
	stdout.Reset()
	if code := run([]string{"open", "--target", target, "--dry-run", "--", "qw://qw.x86.com.br:27500"}, &stdout, &stderr); code != 0 || !strings.Contains(stdout.String(), "-basedir "+target+" ") {
		t.Fatalf("code %d: %s %s", code, stdout.String(), stderr.String())
	}
}

// A bad address is a usage mistake (2); a demo that is not there is a
// failure (1).
func TestOpenExitCodes(t *testing.T) {
	target := t.TempDir()
	for item, want := range map[string]int{
		"qw://host;quit": 2,
		"notes.txt":      2,
		filepath.Join(t.TempDir(), "missing.mvd"): 1,
	} {
		var stdout, stderr bytes.Buffer
		if code := run([]string{"open", "--target", target, "--dry-run", item}, &stdout, &stderr); code != want {
			t.Errorf("%s: code %d, want %d: %s", item, code, want, stderr.String())
		}
	}
}
