package main

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/x86dx2/x86qw/go/internal/play"
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

// A dry run shows the command of a demo and writes nothing: no copy, no
// old copy pruned, not even a folder.
func TestOpenDryRunLeavesTheInstallationAlone(t *testing.T) {
	demo := filepath.Join(t.TempDir(), "final.mvd")
	os.WriteFile(demo, []byte("demo bytes"), 0o644)
	target := t.TempDir()
	var stdout, stderr bytes.Buffer
	if code := run([]string{"open", "--target", target, "--dry-run", demo}, &stdout, &stderr); code != 0 {
		t.Fatalf("%d %s", code, stderr.String())
	}
	if entries, _ := os.ReadDir(target); len(entries) != 0 {
		t.Fatalf("the dry run wrote %v", entries)
	}
	demos := filepath.Join(target, "qw", "demos")
	os.MkdirAll(demos, 0o755)
	old, week := filepath.Join(demos, "x86qw-open-old.mvd"), time.Now().Add(-8*24*time.Hour)
	os.WriteFile(old, []byte("old"), 0o644)
	os.Chtimes(old, week, week)
	stdout.Reset()
	if code := run([]string{"open", "--target", target, "--dry-run", demo}, &stdout, &stderr); code != 0 {
		t.Fatalf("%d %s", code, stderr.String())
	}
	if entries, _ := os.ReadDir(demos); len(entries) != 1 || entries[0].Name() != "x86qw-open-old.mvd" {
		t.Fatalf("the dry run changed qw/demos: %v", entries)
	}
	// What it shows is what opening the demo runs.
	if args, err := play.OpenArguments(target, demo); err != nil || !strings.HasSuffix(strings.TrimSpace(stdout.String()), strings.Join(args, " ")) {
		t.Fatalf("%s: %v %v", stdout.String(), args, err)
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
