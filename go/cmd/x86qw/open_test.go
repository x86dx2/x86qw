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
		t.Fatalf("comando: %s", got)
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
			t.Errorf("%s: código %d, esperado %d: %s", item, code, want, stderr.String())
		}
	}
}
