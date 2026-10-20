package main

import (
	"errors"
	"strings"
	"testing"

	"github.com/x86dx2/x86qw/go/internal/menu"
	"github.com/x86dx2/x86qw/go/internal/play"
	"github.com/x86dx2/x86qw/go/internal/services"
)

func TestQtvMenuAndSummary(t *testing.T) {
	// Rede: todas IPv4; porta: outra 28010; upstream: MVDSV 127.0.0.1:28501;
	// senha: sim; execução: segundo plano.
	fakeSession(t, "2\n2\n28010\n2\n\ns\n2\n")
	op := &services.Operation{Action: "qtv", Qtv: &services.QtvOptions{Bind: "127.0.0.1", Port: 28000, Hostname: "x86QW QTV"}}
	ok, err := configureQtv(sessionUI, op.Qtv)
	if err != nil || !ok {
		t.Fatalf("%v %v", ok, err)
	}
	o := op.Qtv
	if o.Bind != "0.0.0.0" || o.Port != 28010 || o.Upstream != "127.0.0.1:28501" || !o.PromptQtvPassword || !o.Background {
		t.Fatalf("%+v", o)
	}
	want := `Resumo do serviço
  Serviço   | QTV
  Nome      | x86QW QTV
  HTTP      | http://0.0.0.0:28010/
  Upstream  | 127.0.0.1:28501
  Segurança | segredo do upstream configurado; valor redigido
  Execução  | segundo plano

Comando equivalente seguro:
  ` + play.PublicCommand([]string{"qtv", "--bind", "0.0.0.0", "--port", "28010", "--hostname", "x86QW QTV",
		"--upstream", "127.0.0.1:28501", "--prompt-qtv-password", "--background"})
	if got := serviceSummaryText(op); got != want {
		t.Fatalf("got\n%s\nwant\n%s", got, want)
	}
}

func TestProxyMenuBackAndInvalidPort(t *testing.T) {
	// IPv6, outra porta (inválida, depois válida), voltar da execução volta
	// ao início; então loopback, porta padrão e primeiro plano.
	out := fakeSession(t, "3\n2\n80\n30001\nb\n1\n1\n1\n")
	o := &services.ProxyOptions{ProxyBind: "127.0.0.1", ProxyPort: 30000}
	ok, err := configureProxy(sessionUI, o)
	if err != nil || ok {
		t.Fatalf("voltar da execução deveria encerrar o fluxo: %v %v", ok, err)
	}
	if !strings.Contains(out.String(), "deve estar entre 1024 e 65535") {
		t.Fatalf("porta inválida aceita:\n%s", out)
	}
	ok, err = configureProxy(sessionUI, o)
	if err != nil || !ok || o.ProxyBind != "127.0.0.1" || o.ProxyPort != 30001 || o.Background {
		t.Fatalf("%v %v %+v", ok, err, o)
	}
}

func TestAdvancedHostMenu(t *testing.T) {
	// MVDSV em 0.0.0.0 porta padrão, 24 clientes, sem MVD, com QTV (IPv6,
	// porta padrão), sem QWFWD, senhas ocultas, primeiro plano.
	fakeSession(t, "2\n1\n3\nn\ns\n3\n1\nn\ns\n1\n")
	o := &services.HostOptions{Bind: "127.0.0.1", Port: 28501, Maxclients: 16, QtvBind: "127.0.0.1", QtvPort: 28000, ProxyBind: "127.0.0.1", ProxyPort: 30000}
	ok, err := configureAdvancedHost(sessionUI, o)
	if err != nil || !ok {
		t.Fatalf("%v %v", ok, err)
	}
	if o.Bind != "0.0.0.0" || o.Port != 28501 || o.Maxclients != 24 || !o.NoMVD || !o.WithQTV || o.QtvBind != "::" ||
		o.WithProxy || !o.PromptPassword || !o.PromptRconPassword || !o.PromptQtvPassword || o.Background {
		t.Fatalf("%+v", o)
	}
	game := &play.Game{ID: "final-arena", Label: "Final Arena"}
	summary := hostSummaryText(o, game, nil, "arena1", play.DefaultOptions(), "advanced")
	for _, line := range []string{
		"  Jogo       | Final Arena", "  Mapa       | arena1", "  Perfil     | Avançado", "  Execução   | primeiro plano",
		"  MVDSV      | 0.0.0.0:28501 · 24 clientes", "  Gravação   | desativada", "  QTV        | ativo em [::]:28000",
		"  QWFWD      | desativado", "  Segurança  | 4 segredo(s) configurado(s); valores redigidos",
	} {
		if !strings.Contains(summary, line+"\n") {
			t.Fatalf("linha ausente %q em\n%s", line, summary)
		}
	}
	if !strings.HasSuffix(summary, " host final-arena --map arena1 --bind 0.0.0.0 --port 28501 --maxclients 24 --no-mvd --with-qtv --qtv-bind :: --qtv-port 28000 --prompt-password --prompt-spectator-password --prompt-rcon-password --prompt-qtv-password") {
		t.Fatalf("comando: %s", summary)
	}
}

func TestQuickDefaultsDropAdvancedChoices(t *testing.T) {
	o := &services.HostOptions{Bind: "0.0.0.0", Port: 1, WithQTV: true, PromptPassword: true, Password: "x", PasswordFile: "f", Background: true}
	applyQuickHostDefaults(o)
	if o.Bind != "127.0.0.1" || o.Port != 28501 || o.Maxclients != 16 || o.WithQTV || o.PromptPassword || o.Password != "" || o.PasswordFile != "" || o.Background {
		t.Fatalf("%+v", o)
	}
}

func TestServiceMenuExitAndCancel(t *testing.T) {
	fakeSession(t, "q\n")
	op := &services.Operation{Action: "proxy", Proxy: &services.ProxyOptions{ProxyBind: "127.0.0.1", ProxyPort: 30000}}
	if err := serviceMenu(sessionUI, op); !errors.Is(err, menu.ErrExit) {
		t.Fatalf("q: %v", err)
	}
	fakeSession(t, "b\n")
	if err := serviceMenu(sessionUI, op); !errors.Is(err, errCancelledByUser) {
		t.Fatalf("voltar: %v", err)
	}
	// Proxy completo e recusa na confirmação.
	fakeSession(t, "1\n1\n1\nn\n")
	if err := serviceMenu(sessionUI, op); !errors.Is(err, errCancelledByUser) {
		t.Fatalf("recusa: %v", err)
	}
}
