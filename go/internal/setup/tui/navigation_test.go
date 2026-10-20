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
	want(scPreflight, "enter na intenção")
	for !m.preDone {
		drive(m, run(m.nextCheck())...)
	}
	drive(m, key("esc"))
	want(scIntent, "esc no pré-voo")
	drive(m, key("enter"))
	for !m.preDone {
		drive(m, run(m.nextCheck())...)
	}
	drive(m, key("enter"))
	want(scReview, "enter no pré-voo")
	drive(m, key("left"))
	want(scPreflight, "← na revisão volta ao pré-voo")
	drive(m, key("enter"))
	want(scReview, "pré-voo concluído segue para a revisão")

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
	if m.screen != scReview {
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
