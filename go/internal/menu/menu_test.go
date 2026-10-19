package menu

import (
	"bufio"
	"bytes"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

var sample = []Option{
	{Key: "play", Label: "Jogar", Description: "mods locais e modos KTX", Detail: "Escolha jogo, modo, mapa e regras.", Group: "Partida"},
	{Key: "hub", Label: "Encontrar servidor", Description: "jogar, observar ou assistir QTV", Group: "Partida"},
	{Key: "off", Label: "Desativado", Description: "não disponível", Disabled: true, DisabledReason: "sem rede", Group: "Instalação"},
	{Key: "exit", Label: "Sair", Description: "encerrar o menu", Aliases: []string{"quit"}, Group: "Instalação"},
}

func fallbackUI(input string) (*UI, *bytes.Buffer) {
	var out bytes.Buffer
	return &UI{In: bufio.NewReader(strings.NewReader(input)), Out: &out}, &out
}

// pythonSelect runs the real Python fallback with the same options and input.
func pythonSelect(t *testing.T, input, call string) (string, string) {
	t.Helper()
	repo, _ := filepath.Abs(filepath.Join("..", "..", ".."))
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("sem python3")
	}
	script := `
import sys
from x86qw_runtime.ui.menu import MenuOption as O, select_one, select_many, confirm, MenuCancelled, MenuExit
opts = (
  O("play","Jogar","mods locais e modos KTX","Escolha jogo, modo, mapa e regras.",group="Partida"),
  O("hub","Encontrar servidor","jogar, observar ou assistir QTV",group="Partida"),
  O("off","Desativado","não disponível",enabled=False,disabled_reason="sem rede",group="Instalação"),
  O("exit","Sair","encerrar o menu",aliases=("quit",),group="Instalação"),
)
try:
    r = ` + call + `
except MenuCancelled:
    r = "CANCELLED"
except MenuExit:
    r = "EXIT"
print("RESULT=" + repr(r))
`
	command := exec.Command(python, "-c", script)
	command.Dir = repo
	command.Env = append(os.Environ(), "PYTHONPATH="+repo, "COLUMNS=88", "LINES=24")
	command.Stdin = strings.NewReader(input)
	output, err := command.CombinedOutput()
	if err != nil {
		if strings.Contains(string(output), "ModuleNotFoundError") {
			t.Skip("x86qw_runtime indisponível")
		}
		t.Fatalf("python: %v\n%s", err, output)
	}
	text := string(output)
	index := strings.LastIndex(text, "RESULT=")
	return text[:index], strings.TrimSpace(text[index+len("RESULT="):])
}

func TestFallbackMatchesPython(t *testing.T) {
	cases := []struct {
		input, python, want string
		settings            Settings
	}{
		{"2\n", `select_one("O que você quer fazer?", opts, breadcrumb="x86QW 1.0.16", searchable=True, allow_back=True, interactive=False)`, "'hub'", Settings{Breadcrumb: "x86QW 1.0.16", Searchable: true, AllowBack: true}},
		{"\n", `select_one("Título", opts, default=1, interactive=False)`, "'hub'", Settings{Default: 1}},
		{"3\nquit\n", `select_one("Título", opts, interactive=False)`, "'exit'", Settings{}},
		{"serv\n", `select_one("Título", opts, searchable=True, interactive=False)`, "'hub'", Settings{Searchable: true}},
		{"jog\n/\n4\n", `select_one("Título", opts, searchable=True, interactive=False)`, "'exit'", Settings{Searchable: true}},
		{"zzz\nb\n", `select_one("Título", opts, allow_back=True, interactive=False)`, "None", Settings{AllowBack: true}},
		{"q\n", `select_one("Título", opts, interactive=False)`, "'EXIT'", Settings{}},
		{"", `select_one("Título", opts, interactive=False)`, "'CANCELLED'", Settings{}},
	}
	for _, c := range cases {
		pyOut, pyResult := pythonSelect(t, c.input, c.python)
		if pyResult != c.want {
			t.Fatalf("python devolveu %s, esperado %s", pyResult, c.want)
		}
		ui, out := fallbackUI(c.input)
		key, back, err := ui.SelectOne(func() string {
			if strings.Contains(c.python, "O que você") {
				return "O que você quer fazer?"
			}
			return "Título"
		}(), sample, c.settings)
		got := "'" + key + "'"
		switch {
		case errors.Is(err, ErrExit):
			got = "'EXIT'"
		case errors.Is(err, ErrCancelled):
			got = "'CANCELLED'"
		case back:
			got = "None"
		}
		if got != c.want {
			t.Fatalf("%q: go devolveu %s, esperado %s", c.input, got, c.want)
		}
		// Python's input() writes the prompt without the user's echo; so do we.
		if out.String() != pyOut {
			t.Fatalf("%q: saída difere\n--- go\n%s\n--- python\n%s", c.input, out.String(), pyOut)
		}
	}
}

func TestSelectManyFallbackMatchesPython(t *testing.T) {
	pyOut, pyResult := pythonSelect(t, "1,zz\n2,4\n", `select_many("Componentes", opts, selected=("play",), allow_back=True, interactive=False)`)
	if pyResult != "('hub', 'exit')" {
		t.Fatalf("python: %s", pyResult)
	}
	ui, out := fallbackUI("1,zz\n2,4\n")
	keys, back, err := ui.SelectMany("Componentes", sample, Settings{AllowBack: true}, []string{"play"}, false)
	if err != nil || back || strings.Join(keys, ",") != "hub,exit" {
		t.Fatalf("go: %v %v %v", keys, back, err)
	}
	if out.String() != pyOut {
		t.Fatalf("saída difere\n--- go\n%s\n--- python\n%s", out.String(), pyOut)
	}
}

func TestConfirmFallbackMatchesPython(t *testing.T) {
	pyOut, pyResult := pythonSelect(t, "talvez\nnão\n", `confirm("Confirma?", interactive=False, default=True)`)
	if pyResult != "False" {
		t.Fatalf("python: %s", pyResult)
	}
	ui, out := fallbackUI("talvez\nnão\n")
	yes, back, err := ui.Confirm("Confirma?", Settings{}, "", true)
	if err != nil || back || yes {
		t.Fatalf("go: %v %v %v", yes, back, err)
	}
	if out.String() != pyOut {
		t.Fatalf("saída difere\n--- go\n%s\n--- python\n%s", out.String(), pyOut)
	}
}

func keys(sequence ...string) func() (string, error) {
	return func() (string, error) {
		if len(sequence) == 0 {
			return "", errors.New("fim")
		}
		key := sequence[0]
		sequence = sequence[1:]
		return key, nil
	}
}

func TestNavigationKeys(t *testing.T) {
	run := func(settings Settings, sequence ...string) (string, bool, error) {
		var out bytes.Buffer
		ui := &UI{Out: &out, Navigation: true, ReadKey: keys(sequence...), Width: 88, Lines: 24}
		return ui.SelectOne("Título", sample, settings)
	}
	if key, _, _ := run(Settings{}, "down", "down", "enter"); key != "exit" {
		t.Fatalf("setas devem pular desabilitados: %s", key)
	}
	if key, _, _ := run(Settings{}, "up", "right"); key != "exit" {
		t.Fatalf("seta para cima circular: %s", key)
	}
	if key, _, _ := run(Settings{Searchable: true}, "/", "s", "a", "i", "enter", "enter"); key != "exit" {
		t.Fatalf("busca: %s", key)
	}
	if key, _, _ := run(Settings{}, "2"); key != "hub" {
		t.Fatalf("atalho numérico: %s", key)
	}
	if _, back, _ := run(Settings{AllowBack: true}, "left"); !back {
		t.Fatal("← deve voltar")
	}
	if _, _, err := run(Settings{}, "left"); !errors.Is(err, ErrCancelled) {
		t.Fatalf("← sem voltar cancela: %v", err)
	}
	if _, _, err := run(Settings{}, "escape"); !errors.Is(err, ErrExit) {
		t.Fatalf("esc sai: %v", err)
	}
	if _, _, err := run(Settings{}, "interrupt"); !errors.Is(err, ErrInterrupt) {
		t.Fatalf("ctrl-c: %v", err)
	}
	var out bytes.Buffer
	ui := &UI{Out: &out, Navigation: true, ReadKey: keys(" ", "down", " ", "enter"), Width: 88, Lines: 24}
	chosen, _, err := ui.SelectMany("Componentes", sample, Settings{}, nil, false)
	if err != nil || strings.Join(chosen, ",") != "play,hub" {
		t.Fatalf("seleção múltipla: %v %v", chosen, err)
	}
}
