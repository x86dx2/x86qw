package main

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The wizard tests always decline the final confirmation or cancel earlier,
// so nothing is downloaded or installed.
func TestInstallWizard(t *testing.T) {
	catalogFile, _ := filepath.Abs(filepath.Join("..", "..", "..", "site", "public", "api", "v1", "catalog.json"))
	if _, err := os.Stat(catalogFile); err != nil {
		t.Skip("catálogo publicado indisponível")
	}
	target := filepath.Join(t.TempDir(), "x86qw")
	cases := []struct {
		name, input string
		code        int
		want        []string
	}{
		{"cancel-channel", "", 130, []string{"Qual canal deseja instalar?", "· Operação cancelada; nenhuma seleção pendente foi aplicada.\n"}},
		{"release-all-and-decline", "2\nt\nzz\n1\n1\nn\n", 0, []string{
			"✓ Canal selecionado: nightly", "Versões nightly disponíveis (mais recente primeiro):",
			"! Versão não encontrada.", "✓ Versão selecionada: ", "Perfil: Recomendado",
			"· Operação cancelada; nenhum arquivo do jogo foi alterado.\n",
		}},
		{"custom", "1\n1\n2\n3\nzz\n\n", 1, []string{"Quais componentes deseja instalar?", "✗ Nenhum componente x86QW foi selecionado."}},
		{"client-only", "1\n1\n2\n4\nn\n4\ns\nn\n", 0, []string{
			"Instalar somente o cliente ezQuake?", "! Somente cliente: Jogar recusará até instalar ao menos KTX.",
			"Perfil: Somente cliente · 0 componentes", "nenhum arquivo do jogo foi alterado",
		}},
	}
	for _, c := range cases {
		var stdout, stderr bytes.Buffer
		fakeSession(t, c.input)
		sessionUI.Out = &stdout
		code := run([]string{"install", "--catalog-file", catalogFile, "--platform", "linux", target}, &stdout, &stderr)
		all := stdout.String() + stderr.String()
		if code != c.code {
			t.Fatalf("%s: exit %d\n%s", c.name, code, all)
		}
		for _, want := range c.want {
			if !strings.Contains(all, want) {
				t.Fatalf("%s: falta %q\n%s", c.name, want, all)
			}
		}
		if _, err := os.Lstat(target); err == nil {
			t.Fatalf("%s: o destino não deveria existir", c.name)
		}
	}
	// No target and no input: the target prompt cannot be read.
	t.Setenv("HOME", t.TempDir())
	var stdout, stderr bytes.Buffer
	fakeSession(t, "")
	sessionUI.Out = &stdout
	if code := run([]string{"install", "--catalog-file", catalogFile}, &stdout, &stderr); code != 1 || !strings.Contains(stderr.String(), "Não foi possível ler o diretório de instalação.") {
		t.Fatalf("exit %d\n%s%s", code, stdout.String(), stderr.String())
	}
}

func TestSetupWithoutProfileNeedsATerminal(t *testing.T) {
	stdinIsTerminal = func() bool { return false }
	t.Cleanup(func() { stdinIsTerminal = func() bool { return isTerminalFile(os.Stdin) } })
	var stdout, stderr bytes.Buffer
	if code := run([]string{"setup", "--target", t.TempDir()}, &stdout, &stderr); code != 2 || !strings.Contains(stderr.String(), "--profile") {
		t.Fatalf("exit %d: %s", code, stderr.String())
	}
}
