package tui

import (
	"testing"

	tea "charm.land/bubbletea/v2"

	"github.com/x86dx2/x86qw/go/internal/setup"
)

func space() tea.Msg { return tea.KeyPressMsg(tea.Key{Code: tea.KeySpace, Text: " "}) }

func started(t *testing.T, existing bool) *Model {
	t.Helper()
	m := New(Config{Backend: &setup.Demo{ExistingInstall: existing, Speed: 50}, Target: "/tmp/x86qw", Lang: PT, Theme: "dark"})
	drive(m, tea.WindowSizeMsg{Width: 100, Height: 34})
	drive(m, run(m.Init())...)
	return m
}

func TestKeyboardNavigation(t *testing.T) {
	m := started(t, false)
	want := func(s screen, label string) {
		t.Helper()
		if m.screen != s {
			t.Fatalf("%s: tela %d, esperado %d", label, m.screen, s)
		}
	}
	drive(m, key("enter"))
	if !m.nickFlag {
		t.Fatal("enter sem nick deveria marcar o campo")
	}
	drive(m, typeText("x86dx2")...)
	drive(m, key("down"))
	if m.opts.Intent != setup.Host {
		t.Fatalf("↓ deveria escolher Hospedar: %s", m.opts.Intent)
	}
	drive(m, key("up"), key("enter"))
	want(scConfigure, "enter na intenção vai para a configuração")
	drive(m, key("esc"))
	want(scIntent, "esc na configuração volta à intenção")
	drive(m, key("enter"))
	want(scConfigure, "intenção → configuração")
	m.Update(key("i")) // without running what it starts
	if m.screen != scConfigure || !m.preOn {
		t.Fatal("instalar deveria abrir o pré-voo na própria configuração")
	}
	m.Update(key("esc"))
	if m.screen != scConfigure || m.preOn {
		t.Fatal("esc deveria recolher o pré-voo e ficar na configuração")
	}
	drive(m, key("i"))
	want(scConfigure, "o pré-voo termina e espera a confirmação")
	if !m.preDone || m.confirmRows()[m.focus].id != "confirm" {
		t.Fatalf("o foco deveria ir para Confirmar e instalar: %d", m.focus)
	}
	m.Update(key("enter"))
	want(scInstall, "enter confirma e instala")
	m.cancel()
	m.screen, m.focus, m.modal = scConfigure, 0, nil

	// Dialog: open setup, change tabs with ← →, toggle with space.
	m.focus = 1
	drive(m, key("enter"))
	if m.modal == nil || m.modal.kind != "config" {
		t.Fatal("enter deveria abrir Sua configuração")
	}
	drive(m, key("right"))
	if m.modal.tab != 1 {
		t.Fatalf("→ deveria trocar de aba: %d", m.modal.tab)
	}
	drive(m, key("down"), key("down"))
	invert := m.opts.Mouse.Invert
	drive(m, space())
	if m.opts.Mouse.Invert == invert {
		t.Fatal("espaço deveria alternar inverter eixo")
	}
	drive(m, key("tab"), key("tab"), key("tab"))
	if m.modal.tab != 0 {
		t.Fatalf("tab deveria dar a volta nas abas: %d", m.modal.tab)
	}
	drive(m, key("esc"))
	if m.modal != nil {
		t.Fatal("esc deveria fechar o diálogo")
	}
	drive(m, key("ctrl+l"))
	if m.lang != EN {
		t.Fatal("ctrl+l deveria trocar o idioma")
	}
}

func TestPanelNavigation(t *testing.T) {
	m := started(t, true)
	if m.screen != scPanel {
		t.Fatalf("instalação existente deveria abrir o painel: %d", m.screen)
	}
	drive(m, key("down"), key("enter"))
	if m.screen != scConfigure {
		t.Fatalf("Componentes e configuração deveria abrir a revisão: %d", m.screen)
	}
	drive(m, key("esc"))
	if m.screen != scPanel {
		t.Fatalf("esc na revisão deveria voltar ao painel: %d", m.screen)
	}
	drive(m, key("up"), key("enter"))
	if m.panelView != "update" {
		t.Fatalf("enter em Atualizar: %q", m.panelView)
	}
	drive(m, key("esc"))
	if m.panelView != "" {
		t.Fatal("esc deveria voltar ao menu do painel")
	}
}

func TestPreflightWarningWaitsAndCorrects(t *testing.T) {
	m := started(t, false)
	drive(m, typeText("x86dx2")...)
	drive(m, key("down"), key("down"), key("enter"), key("i"))
	if m.screen != scConfigure || !m.preOn || m.preflightOutcome() != setup.CheckWarning {
		t.Fatalf("aviso deveria esperar na revisão: tela %d", m.screen)
	}
	// Correct it in place: Server → first port 27501, then close.
	for index, row := range m.confirmRows() {
		if row.id == "server" {
			m.focus = index
		}
	}
	drive(m, key("enter"), key("down"), key("right"), key("esc"))
	if m.opts.Server.BasePort != 27501 {
		t.Fatalf("porta inicial: %d", m.opts.Server.BasePort)
	}
	if m.screen != scConfigure || !m.preDone || m.preflightOutcome() != setup.CheckOK {
		t.Fatalf("o pré-voo deveria rodar de novo e passar: tela %d, %v", m.screen, m.preflightOutcome())
	}
	m.Update(key("enter"))
	if m.screen != scInstall {
		t.Fatalf("enter deveria instalar: %d", m.screen)
	}
	m.cancel()
}
