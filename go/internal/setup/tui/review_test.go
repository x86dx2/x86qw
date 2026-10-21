package tui

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	tea "charm.land/bubbletea/v2"

	"github.com/x86dx2/x86qw/go/internal/setup"
)

// offline is the demo without the published catalog.
type offline struct{ *setup.Demo }

func (o offline) Catalog(ctx context.Context) (*setup.Catalog, error) {
	cat, err := o.Demo.Catalog(ctx)
	if err == nil {
		cat.Unavailable = errors.New("sem rede")
	}
	return cat, err
}

func TestOfflineInstallationStillOpensAndNewOneRetries(t *testing.T) {
	m := New(Config{Backend: offline{&setup.Demo{ExistingInstall: true, Speed: 50}}, Target: shortTarget(t), Lang: PT})
	drive(m, tea.WindowSizeMsg{Width: 100, Height: 34})
	drive(m, run(m.Init())...)
	if m.err != nil || m.screen != scPanel {
		t.Fatalf("sem catálogo, a instalação ainda abre: %v, tela %d", m.err, m.screen)
	}
	m.focus = homeRow(m, "change")
	drive(m, key("enter"))
	if m.screen != scPanel {
		t.Fatal("sem catálogo, nada novo pode ser instalado")
	}
	fresh := New(Config{Backend: offline{&setup.Demo{Speed: 50}}, Target: shortTarget(t), Lang: PT})
	drive(fresh, tea.WindowSizeMsg{Width: 100, Height: 34})
	drive(fresh, run(fresh.Init())...)
	if fresh.err == nil || !strings.Contains(fresh.View().Content, "sem rede") {
		t.Fatal("sem instalação e sem catálogo, a tela diz por quê")
	}
	fresh.cfg.Backend = &setup.Demo{Speed: 50}
	drive(fresh, key("r"))
	if fresh.err != nil || fresh.catalog == nil || fresh.screen != scIntent {
		t.Fatalf("r tenta de novo: %v", fresh.err)
	}
}

// TestUpdateRunsFromHomeAndBack: Atualizar applies from home and esc, or
// the end, returns there.
func TestUpdateRunsFromHomeAndBack(t *testing.T) {
	m := started(t, true)
	m.focus = homeRow(m, "update")
	drive(m, key("enter"), key("enter"))
	if m.screen != scConfigure || !m.preOn || !m.opts.Upgrade || !m.fromPanel {
		t.Fatalf("aplicar abre o pré-voo vindo do início: tela %d", m.screen)
	}
	drive(m, key("esc"), key("esc"))
	if m.screen != scPanel || m.panelView != "" || m.opts.Upgrade || m.panelRows()[m.focus].id != "update" {
		t.Fatalf("esc volta ao início, em Atualizar: tela %d %q", m.screen, m.panelView)
	}
	drive(m, key("enter"), key("enter"))
	drive(m, key("i"))
	if m.progress.Total == 0 || !m.progress.Finished {
		t.Fatalf("o apply baixa as atualizações: %d bytes", m.progress.Total)
	}
	if m.screen != scPanel || !strings.Contains(m.toast, m.t("done.applied")) || m.panelRows()[m.focus].id != "update" {
		t.Fatalf("o fim volta ao início: tela %d", m.screen)
	}
}

func TestNothingToDownloadReadsAsDone(t *testing.T) {
	m := started(t, true)
	m.screen, m.progress = scInstall, setup.Progress{Finished: true}
	if view := m.View().Content; !strings.Contains(view, strings.TrimSpace(bigNumber(100, m.glyphs)[1])) {
		t.Fatal("terminado sem bytes é 100%")
	}
	m.screen = scDone
	if view := m.View().Content; !strings.Contains(view, m.t("done.nothing")) {
		t.Fatal("o fim diz que não havia o que baixar")
	}
}

func TestRemovedInstallationOnlyLeaves(t *testing.T) {
	m := started(t, true)
	m.panelView, m.removed = "remove", true
	if cmd := m.key("enter"); cmd != nil || m.busy {
		t.Fatal("depois de desinstalar, enter não faz nada")
	}
	if m.key("esc") != nil || m.key("esc") == nil {
		t.Fatal("esc duas vezes sai")
	}
}

// failingOpen is the demo with a connection that fails.
type failingOpen struct{ *setup.Demo }

func (failingOpen) Open(context.Context, string, string) error {
	return errors.New("ezQuake não abriu")
}

func TestServersShowWhatFailsAndFavoritesFirst(t *testing.T) {
	m := New(Config{Backend: failingOpen{&setup.Demo{ExistingInstall: true, Speed: 50}}, Target: shortTarget(t), Lang: PT, Start: "servers"})
	drive(m, tea.WindowSizeMsg{Width: 100, Height: 34})
	drive(m, run(m.Init())...)
	if m.screen != scServers || len(m.game.servers) == 0 {
		t.Fatalf("servidores: tela %d", m.screen)
	}
	m.View()
	top := m.tops[scServers]
	if !m.visibleServers()[0].Favorite {
		t.Fatal("favoritos primeiro")
	}
	m.focus = 1
	drive(m, key("ctrl+f"))
	if favorites := m.visibleServers(); !favorites[0].Favorite || !favorites[1].Favorite {
		t.Fatal("ctrl+f guarda o servidor nos favoritos")
	}
	drive(m, key("enter"))
	if view := m.View().Content; !strings.Contains(view, "ezQuake não abriu") || m.tops[scServers] != top {
		t.Fatal("o erro ao entrar aparece na doca, sem mover a lista")
	}
	if strings.Contains(m.toast, "conectando") {
		t.Fatal("nada de toast antes de conectar")
	}
	drive(m, tea.MouseWheelMsg(tea.Mouse{Button: tea.MouseWheelDown}))
	if m.focus != 2 {
		t.Fatalf("a roda percorre a lista: %d", m.focus)
	}
	m.Update(m.click("hint:ctrl+r"))
	if !m.game.serversLoading {
		t.Fatal("clicar em ctrl+r no rodapé atualiza")
	}
}

func TestServerDialogKeepsAProblemWhereItIs(t *testing.T) {
	m := started(t, false)
	drive(m, typeText("x86dx2")...)
	drive(m, key("down"), key("down"), key("enter"))
	for index, row := range m.confirmRows() {
		if row.id == "server" {
			m.focus = index
		}
	}
	drive(m, key("enter"), key("tab"), key("down"), key("down"), key("down"), key("down"))
	drive(m, typeText(`a"b`)...)
	if m.opts.Server.Password != "ab" {
		t.Fatalf("a senha não aceita aspas: %q", m.opts.Server.Password)
	}
	m.modal.tab = 0
	m.opts.Server.Name, m.modal.field["srv.name"].Value = "", nil
	m.modal.tab = 3
	drive(m, key("enter"))
	if m.modal == nil || m.modal.tab != 0 {
		t.Fatal("enter com o nome vazio fica no diálogo, na aba Geral")
	}
	view := m.View().Content
	if !strings.Contains(view, "dê um nome ao servidor") {
		t.Fatal("o problema aparece na aba que o tem")
	}
	drive(m, key("esc"))
	if m.opts.Server.Name == "" {
		t.Fatal("esc devolve o nome")
	}
	// Invalid choices do not run the checks; why stays under the setup.
	m.opts.Server.Name = ""
	drive(m, key("i"))
	if m.preOn || m.confirmErr == nil || !strings.Contains(m.View().Content, "dê um nome ao servidor") {
		t.Fatal("escolhas inválidas não rodam o pré-voo e dizem por quê")
	}
}

func TestHostStopsOnASecondPressAndAddRestores(t *testing.T) {
	m := started(t, true)
	m.focus = homeRow(m, "host")
	drive(m, key("enter"))
	m.focus = len(m.hostRows()) - 1
	drive(m, key("enter"))
	drive(m, run(m.loadStack())...)
	if m.game.stack == nil || m.focus != 0 {
		t.Fatal("o servidor sobe e o foco volta ao começo")
	}
	drive(m, key("enter"))
	if m.game.acting != "" || m.game.stack == nil {
		t.Fatal("parar pede uma segunda vez")
	}
	if hints := m.footerHints(); hints[0][1] != m.t("stop.again") {
		t.Fatalf("o rodapé pede a confirmação: %v", hints)
	}
	drive(m, key("enter"))
	drive(m, run(m.loadStack())...)
	if m.game.stack != nil {
		t.Fatal("a segunda vez para")
	}

	// A host without the server programs: add them, then change our mind.
	m.existing.Installed = []string{"ktx"}
	m.opts.Intent = setup.Play
	drive(m, key("enter"))
	if m.screen != scConfigure || m.opts.Intent != setup.Both {
		t.Fatal("adicionar o servidor abre a configuração como cliente e servidor")
	}
	drive(m, key("esc"))
	if m.screen != scHost || m.opts.Intent != setup.Play {
		t.Fatalf("esc devolve as escolhas e volta ao servidor: tela %d %s", m.screen, m.opts.Intent)
	}
}

func TestBackFromAScreenLandsOnItsRow(t *testing.T) {
	m := started(t, true)
	for _, id := range []string{"servers", "host", "change"} {
		m.focus = homeRow(m, id)
		drive(m, key("enter"), key("esc"))
		if m.screen != scPanel || m.panelRows()[m.focus].id != id {
			t.Fatalf("de %s volta para %s", id, m.panelRows()[m.focus].id)
		}
	}
}

func TestOnePollAtATime(t *testing.T) {
	m := started(t, true)
	m.game.polling = false
	if m.pollStack() == nil || m.pollStack() != nil {
		t.Fatal("um segundo poll não começa enquanto o primeiro espera")
	}
}

func TestErrorsStayOnTheirScreen(t *testing.T) {
	m := started(t, true)
	m.Update(stackMsg{err: errors.New("estado ilegível")})
	m.screen = scMatch
	if m.errorHere() != nil {
		t.Fatal("o erro do servidor não aparece na partida")
	}
	m.screen = scHost
	if m.errorHere() == nil {
		t.Fatal("o erro do servidor aparece no servidor")
	}
}

func TestLocalServerAndUnknownRuleset(t *testing.T) {
	m := started(t, true)
	m.opts.Server.Local = true
	if m.connectHost() != "127.0.0.1" {
		t.Fatal("um servidor local é 127.0.0.1")
	}
	m.screen = scDone
	if view := m.View().Content; strings.Contains(view, m.t("ports.open")) {
		t.Fatal("sem dica de roteador para um servidor local")
	}
	if m.ruleset("novo") != "novo" {
		t.Fatal("um ruleset desconhecido aparece pelo nome")
	}
}

func TestPortsStepByTen(t *testing.T) {
	m := started(t, true)
	m.focus = homeRow(m, "host")
	drive(m, key("enter"), key("enter"), key("down"))
	base := m.opts.Server.BasePort
	drive(m, key("shift+right"))
	if m.opts.Server.BasePort != base+10 {
		t.Fatalf("shift+→ soma dez: %d → %d", base, m.opts.Server.BasePort)
	}
}

func TestQuitNamesTheKeyPressed(t *testing.T) {
	m := started(t, false)
	drive(m, key("esc"))
	if hints := m.footerHints(); hints[0][0] != "esc" {
		t.Fatalf("%v", hints)
	}
	m.armedUntil = time.Now().Add(-time.Second)
	m.Update(key("ctrl+c"))
	if hints := m.footerHints(); hints[0][0] != "ctrl+c" {
		t.Fatalf("%v", hints)
	}
}

func TestEnglishShowsNoPortuguese(t *testing.T) {
	m := started(t, true)
	m.lang = EN
	m.focus = homeRow(m, "update")
	drive(m, key("enter"))
	view := m.View().Content
	for _, word := range []string{"Componente", "Dados do Quake", "Base e inicialização"} {
		if strings.Contains(view, word) {
			t.Fatalf("%q em inglês:\n%s", word, view)
		}
	}
}
