package main

import (
	"bufio"
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/x86dx2/x86qw/go/internal/menu"
	"github.com/x86dx2/x86qw/go/internal/play"
)

// fakeSession installs a line-based UI over input as the process UI.
func fakeSession(t *testing.T, input string) *bytes.Buffer {
	t.Helper()
	var out bytes.Buffer
	sessionUI = &menu.UI{In: bufio.NewReader(strings.NewReader(input)), Out: &out}
	t.Cleanup(func() { sessionUI = nil })
	return &out
}

func flowFor(t *testing.T, input string) (*playFlow, *bytes.Buffer) {
	out := fakeSession(t, input)
	return &playFlow{ui: sessionUI, con: console{out: out, err: out}, target: t.TempDir()}, out
}

func TestRulesetPickerSavesPythonJSON(t *testing.T) {
	flow, out := flowFor(t, "2\n")
	ruleset, err := flow.resolveRuleset("", true)
	if err != nil || ruleset != "qcon" {
		t.Fatalf("%q %v", ruleset, err)
	}
	payload, err := os.ReadFile(filepath.Join(flow.target, ".x86qw", "launcher", "client-ruleset.json"))
	if err != nil {
		t.Fatal(err)
	}
	if want := "{\n  \"format\": 1,\n  \"project\": \"x86qw\",\n  \"ruleset\": \"qcon\"\n}\n"; string(payload) != want {
		t.Fatalf("payload %q", payload)
	}
	if !strings.Contains(out.String(), "Como você quer jogar?") || !strings.HasSuffix(out.String(), "✓ Experiência salva: QuakeCon.\n") {
		t.Fatalf("saída:\n%s", out)
	}
	// The saved preference is reused without prompting.
	if again, err := flow.resolveRuleset("", true); err != nil || again != "qcon" {
		t.Fatalf("%q %v", again, err)
	}
	// Back at the ruleset picker returns without launching anything.
	other, _ := flowFor(t, "b\n")
	if _, err := other.resolveRuleset("", true); !errors.Is(err, errBack) {
		t.Fatalf("voltar: %v", err)
	}
}

func TestModePickerGroupsAndBack(t *testing.T) {
	modes, err := play.LoadModes()
	if err != nil {
		t.Fatal(err)
	}
	flow, _ := flowFor(t, "b\n")
	if _, back, err := flow.chooseMode(modes, ""); !back || err != nil {
		t.Fatalf("voltar no grupo: %v %v", back, err)
	}
	// A back in the mode list returns to the groups; "all" lists every mode.
	flow, out := flowFor(t, "1\nb\n6\nctf\n1\n")
	mode, back, err := flow.chooseMode(modes, "")
	if err != nil || back || mode.Key != "ctf" {
		t.Fatalf("%v %v %v", mode, back, err)
	}
	if !strings.Contains(out.String(), "x86QW › Jogar › KTX › Todos os modos") {
		t.Fatalf("breadcrumb ausente:\n%s", out)
	}
	if _, _, err := flow.chooseMode(modes, "nosuch"); err == nil || !strings.HasPrefix(err.Error(), "Modo KTX desconhecido: nosuch.") {
		t.Fatalf("modo desconhecido: %v", err)
	}
}

func TestOptionsWizard(t *testing.T) {
	modes, _ := play.LoadModes()
	find := func(key string) play.Mode {
		mode, err := play.FindMode(modes, key)
		if err != nil {
			t.Fatal(err)
		}
		return mode
	}
	flow, _ := flowFor(t, "4\n2\ns\n")
	o, back, err := flow.chooseOptions(find("ctf"), play.DefaultOptions())
	if err != nil || back || *o.CTFHook != "crhook" || *o.CTFRunes != "off" || !o.CTFBasedSpawn {
		t.Fatalf("ctf: %+v %v %v", o, back, err)
	}
	// race: match → back from scoring → solo → pacemaker 2 → back → none → hide.
	flow, _ = flowFor(t, "3\nb\n1\n3\nb\n1\ns\n")
	o, _, err = flow.chooseOptions(find("race"), play.DefaultOptions())
	if err != nil || *o.RaceStyle != "solo" || o.RaceScoring != nil || o.RacePacemaker != nil || !o.RaceHidePlayers {
		t.Fatalf("race: %+v %v", o, err)
	}
	// duel: custom count is not offered (fixed roster), one bot, skill 8.
	flow, out := flowFor(t, "2\n9\n2\n")
	o, _, err = flow.chooseOptions(find("duel"), play.DefaultOptions())
	if err != nil || o.Bots != 1 || o.BotSkill != "8" || o.BotNamesProfile != "x86qw" {
		t.Fatalf("duel: %+v %v", o, err)
	}
	if strings.Contains(out.String(), "Quantidade personalizada") {
		t.Fatal("duel não oferece quantidade personalizada")
	}
	if got := strings.Join(play.OptionsCLIArguments(o, false), " "); got != "--bots 1 --bot-skill 8 --bot-names x86qw" {
		t.Fatalf("cli: %s", got)
	}
	// "Sem bots" clears every bot option.
	flow, _ = flowFor(t, "1\n")
	start := play.DefaultOptions()
	start.Bots, start.BotSkill = 3, "9"
	o, _, _ = flow.chooseOptions(find("ffa"), start)
	if o.Bots != 0 || o.BotSkill != "5" {
		t.Fatalf("sem bots: %+v", o)
	}
}

func TestMapPickerDefaultsAndRequested(t *testing.T) {
	flow, out := flowFor(t, "\n")
	maps := filepath.Join(flow.target, "id1", "maps")
	os.MkdirAll(maps, 0o755)
	for _, name := range []string{"dm2", "dm6", "e1m1", "aerowalk"} {
		os.WriteFile(filepath.Join(maps, name+".bsp"), []byte("x"), 0o644)
	}
	game := play.Game{ID: "ktx", Label: "KTX", Gamedir: "qw", DefaultMap: "dm2", SuggestedMaps: []string{"dm6", "dm2"}}
	name, back, err := flow.chooseMap(game, nil, "", nil, nil, "x86QW › Jogar › KTX › Mapa")
	if err != nil || back || name != "dm2" {
		t.Fatalf("%q %v %v", name, back, err)
	}
	if !strings.Contains(out.String(), "  1) dm6          · sugerido  < mapa instalado") || !strings.Contains(out.String(), "4 mapa(s) compatível(is)") {
		t.Fatalf("saída:\n%s", out)
	}
	if name, _, _ := flow.chooseMap(game, nil, "DM6", nil, nil, ""); name != "dm6" {
		t.Fatalf("requested: %q", name)
	}
	if _, _, err := flow.chooseMap(game, nil, "nosuch", nil, nil, ""); err == nil || err.Error() != "O mapa nosuch não está disponível ou não é compatível com KTX." {
		t.Fatalf("requested inválido: %v", err)
	}
}

func TestSummaryTextMatchesPython(t *testing.T) {
	modes, _ := play.LoadModes()
	duel, _ := play.FindMode(modes, "duel")
	o := play.DefaultOptions()
	o.Bots = 1
	text := play.SummaryText(play.Game{ID: "ktx", Label: "KTX"}, &duel, "dm6", "ezQuake nightly 20260616-101233_a86996a", o, "qcon")
	want := `Resumo da partida
  Jogo    | KTX
  Modo    | Duel
  Mapa    | dm6
  Experiência | QuakeCon
  Cliente | ezQuake nightly 20260616-101233_a86996a
  Bots    | 1 · habilidade 5
  Nomes   | KTX Default

Comando equivalente:
  ./x86qw.sh play ktx --ruleset qcon --mode duel --bots 1 --bot-skill 5 --map dm6`
	if text != want {
		t.Fatalf("got\n%s", text)
	}
	if play.ShellQuote("it's") != `'it'"'"'s'` || play.ShellQuote("") != "''" || play.ShellQuote("a-b.c") != "a-b.c" {
		t.Fatal("ShellQuote")
	}
	if got := play.ListToCommandLine([]string{"x86qw.cmd", "a b", `c\"d`, ""}); got != `x86qw.cmd "a b" c\\\"d ""` {
		t.Fatalf("list2cmdline: %s", got)
	}
}

func TestPlayExitContracts(t *testing.T) {
	target := t.TempDir()
	var stdout, stderr bytes.Buffer
	fakeSession(t, "")
	sessionUI.Out = &stdout
	// EOF at the ruleset picker: MenuCancelled → 130.
	if code := run([]string{"play", "--target", target, "--menu"}, &stdout, &stderr); code != 130 {
		t.Fatalf("exit %d\n%s%s", code, stdout.String(), stderr.String())
	}
	if !strings.HasSuffix(stdout.String(), "· Operação cancelada; o jogo não foi iniciado.\n") {
		t.Fatalf("saída:\n%s", stdout.String())
	}
	// q exits with 0 and its own message.
	stdout.Reset()
	fakeSession(t, "q\n")
	sessionUI.Out = &stdout
	if code := run([]string{"play", "--target", target, "--menu"}, &stdout, &stderr); code != 0 || !strings.HasSuffix(stdout.String(), "· Menu encerrado; o jogo não foi iniciado.\n") {
		t.Fatalf("exit %d\n%s", code, stdout.String())
	}
	// --configure-ruleset: back keeps the preference untouched.
	stdout.Reset()
	fakeSession(t, "b\n")
	sessionUI.Out = &stdout
	if code := run([]string{"play", "--target", target, "--configure-ruleset"}, &stdout, &stderr); code != 0 || !strings.HasSuffix(stdout.String(), "· Experiência inalterada; nenhuma preferência foi gravada.\n") {
		t.Fatalf("exit %d\n%s", code, stdout.String())
	}
	// A ruleset is chosen, then the installation has no PAKs: error, exit 1.
	stdout.Reset()
	stderr.Reset()
	fakeSession(t, "1\n")
	sessionUI.Out = &stdout
	if code := run([]string{"play", "--target", target, "--menu"}, &stdout, &stderr); code != 1 || !strings.Contains(stderr.String(), "Execute novamente com --verbose") {
		t.Fatalf("exit %d\n%s", code, stderr.String())
	}
}

func TestMainMenuContracts(t *testing.T) {
	stdinIsTerminal = func() bool { return false }
	t.Cleanup(func() { stdinIsTerminal = func() bool { return isTerminalFile(os.Stdin) } })
	target := t.TempDir()
	cases := []struct {
		input, suffix string
		code          int
		tty           bool
	}{
		{"", "Uso: ./x86qw.sh <comando> [opções]\nExemplo: ./x86qw.sh play\n" + commandList + "\n", 0, false},
		{"", "· Operação cancelada; nenhuma seleção pendente foi aplicada.\n", 130, true},
		{"8\n", "\nAté a próxima partida.\n", 0, false},
		{"b\n", "\nAté a próxima partida.\n", 0, false},
		{"q\n", "· Menu encerrado; nenhuma seleção pendente foi aplicada.\n", 0, false},
		{"7\n\n8\n", "\nAté a próxima partida.\n", 0, false},
		// play from the menu: q inside play ends the whole menu.
		{"1\nq\n", "· Menu encerrado; nenhuma seleção pendente foi aplicada.\n", 0, false},
		// experience, then back to the main menu and exit.
		{"2\n3\n\n8\n", "\nAté a próxima partida.\n", 0, false},
		{"6\n1\n\nb\n", "\nAté a próxima partida.\n", 0, false},
	}
	for _, c := range cases {
		tty := c.tty
		stdinIsTerminal = func() bool { return tty }
		var stdout, stderr bytes.Buffer
		fakeSession(t, c.input)
		sessionUI.Out = &stdout
		if code := run([]string{"menu", target}, &stdout, &stderr); code != c.code || !strings.HasSuffix(stdout.String(), c.suffix) {
			t.Fatalf("%q: exit %d\n%s\n%s", c.input, code, stdout.String(), stderr.String())
		}
	}
	saved, err := readRuleset(target)
	if err != nil || saved != "smackdown" {
		t.Fatalf("experiência: %q %v", saved, err)
	}
}
