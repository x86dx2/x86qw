package cliargs

import (
	"errors"
	"flag"
	"strings"
	"testing"
)

func TestFlagsGoAnywhere(t *testing.T) {
	var target, mode string
	var dry, yes bool
	var count int
	p := New()
	p.String(&target, "TARGET", "folder", "--target")
	p.Choice(&mode, []string{"duel", "ffa"}, "mode", "--mode")
	p.Bool(&dry, "simulates", "--dry-run", "-n")
	p.Bool(&yes, "confirms", "--yes")
	p.Int(&count, 1, 3, "N", "how many", "--count")
	if err := p.Parse([]string{"folder", "--dry-run", "--mode=ffa", "--count", "2", "--", "--yes"}); err != nil {
		t.Fatal(err)
	}
	if !dry || yes || mode != "ffa" || count != 2 || strings.Join(p.Positional(), ",") != "folder,--yes" {
		t.Fatalf("dry %v yes %v mode %q count %d positional %v", dry, yes, mode, count, p.Positional())
	}
	if !p.Given("-n") || p.Given("--yes") {
		t.Fatal("Given must hold for any name of the flag and only for what was given")
	}
}

func TestMistakesAreUsageErrors(t *testing.T) {
	for _, args := range [][]string{{"--nope"}, {"--target"}, {"--dry-run=1"}, {"--mode", "ctf"}, {"--count", "9"}, {"--a", "x", "--b", "y"}} {
		var target, mode, a, b string
		var dry bool
		var count int
		p := New()
		p.String(&target, "TARGET", "", "--target")
		p.Bool(&dry, "", "--dry-run")
		p.Choice(&mode, []string{"duel"}, "", "--mode")
		p.Int(&count, 1, 3, "N", "", "--count")
		p.String(&a, "A", "", "--a")
		p.String(&b, "B", "", "--b")
		p.Exclusive("--a", "--b")
		if err := p.Parse(args); !IsUsage(err) {
			t.Errorf("%v: %v", args, err)
		}
	}
}

func TestHelpAndOptionalValues(t *testing.T) {
	var bundle string
	p := New()
	p.Optional(&bundle, "default.zip", "FILE.zip", "writes a bundle", "--bundle")
	if err := p.Parse([]string{"--bundle", "--help"}); !errors.Is(err, ErrHelp) {
		t.Fatalf("--help: %v", err)
	}
	if bundle != "default.zip" {
		t.Fatalf("without a value: %q", bundle)
	}
	p = New()
	p.Optional(&bundle, "default.zip", "FILE.zip", "writes a bundle", "--bundle")
	if err := p.Parse([]string{"--bundle", "my.zip"}); err != nil || bundle != "my.zip" {
		t.Fatalf("with a value: %q %v", bundle, err)
	}
	if help := p.Flags(); !strings.Contains(help, "--bundle [FILE.zip]  writes a bundle") {
		t.Fatalf("help: %q", help)
	}
}

func TestAdoptKeepsPackageFlagWorking(t *testing.T) {
	fs := flag.NewFlagSet("x", flag.ContinueOnError)
	nick := fs.String("nick", "", "your nick")
	volume := fs.Int("volume", -1, "volume")
	local := fs.Bool("local", false, "this machine only")
	p := New()
	p.Adopt(fs)
	if err := p.Parse([]string{"--nick", "tester", "dir", "--local", "--volume=40"}); err != nil {
		t.Fatal(err)
	}
	given := map[string]bool{}
	fs.Visit(func(f *flag.Flag) { given[f.Name] = true })
	if *nick != "tester" || *volume != 40 || !*local || !given["nick"] || len(p.Positional()) != 1 {
		t.Fatalf("%q %d %v %v", *nick, *volume, *local, given)
	}
	if err := p.Parse([]string{"--volume", "loud"}); !IsUsage(err) {
		t.Fatalf("invalid volume: %v", err)
	}
	// Package flag would let the last of two win without a word.
	twice := New()
	servers := flag.NewFlagSet("z", flag.ContinueOnError)
	servers.String("servers", "", "instances")
	twice.Adopt(servers)
	if err := twice.Parse([]string{"--servers", "duel:dm6", "--servers", "ctf:e2m2"}); !IsUsage(err) {
		t.Fatalf("an adopted flag given twice: %v", err)
	}
}
