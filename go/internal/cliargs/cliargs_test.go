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
	p.String(&target, "DESTINO", "pasta", "--target")
	p.Choice(&mode, []string{"duel", "ffa"}, "modo", "--mode")
	p.Bool(&dry, "simula", "--dry-run", "-n")
	p.Bool(&yes, "confirma", "--yes")
	p.Int(&count, 1, 3, "N", "quantos", "--count")
	if err := p.Parse([]string{"pasta", "--dry-run", "--mode=ffa", "--count", "2", "--", "--yes"}); err != nil {
		t.Fatal(err)
	}
	if !dry || yes || mode != "ffa" || count != 2 || strings.Join(p.Positional(), ",") != "pasta,--yes" {
		t.Fatalf("dry %v yes %v mode %q count %d positional %v", dry, yes, mode, count, p.Positional())
	}
	if !p.Given("-n") || p.Given("--yes") {
		t.Fatal("Given deve valer para qualquer nome da flag e só para o que veio")
	}
}

func TestMistakesAreUsageErrors(t *testing.T) {
	for _, args := range [][]string{{"--nope"}, {"--target"}, {"--dry-run=1"}, {"--mode", "ctf"}, {"--count", "9"}, {"--a", "x", "--b", "y"}} {
		var target, mode, a, b string
		var dry bool
		var count int
		p := New()
		p.String(&target, "DESTINO", "", "--target")
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
	p.Optional(&bundle, "padrão.zip", "ARQUIVO.zip", "grava um bundle", "--bundle")
	if err := p.Parse([]string{"--bundle", "--help"}); !errors.Is(err, ErrHelp) {
		t.Fatalf("--help: %v", err)
	}
	if bundle != "padrão.zip" {
		t.Fatalf("sem valor: %q", bundle)
	}
	p = New()
	p.Optional(&bundle, "padrão.zip", "ARQUIVO.zip", "grava um bundle", "--bundle")
	if err := p.Parse([]string{"--bundle", "meu.zip"}); err != nil || bundle != "meu.zip" {
		t.Fatalf("com valor: %q %v", bundle, err)
	}
	if help := p.Flags(); !strings.Contains(help, "--bundle [ARQUIVO.zip]  grava um bundle") {
		t.Fatalf("ajuda: %q", help)
	}
}

func TestAdoptKeepsPackageFlagWorking(t *testing.T) {
	fs := flag.NewFlagSet("x", flag.ContinueOnError)
	nick := fs.String("nick", "", "seu nick")
	volume := fs.Int("volume", -1, "volume")
	local := fs.Bool("local", false, "só esta máquina")
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
	if err := p.Parse([]string{"--volume", "alto"}); !IsUsage(err) {
		t.Fatalf("volume inválido: %v", err)
	}
}
