package tui

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	tea "charm.land/bubbletea/v2"

	"github.com/x86dx2/x86qw/go/internal/setup"
)

func space() tea.Msg { return tea.KeyPressMsg(tea.Key{Code: tea.KeySpace, Text: " "}) }

func started(t *testing.T, existing bool) *Model {
	t.Helper()
	m := New(Config{Backend: &setup.Demo{ExistingInstall: existing, Speed: 50}, Target: shortTarget(t), Lang: PT, Theme: "dark"})
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
	m.focus = homeRow(m, "change")
	drive(m, key("enter"))
	if m.screen != scConfigure {
		t.Fatalf("Componentes e configuração deveria abrir a revisão: %d", m.screen)
	}
	drive(m, key("esc"))
	if m.screen != scPanel {
		t.Fatalf("esc na revisão deveria voltar ao painel: %d", m.screen)
	}
	m.focus = homeRow(m, "update")
	drive(m, key("enter"))
	if m.panelView != "update" {
		t.Fatalf("enter em Atualizar: %q", m.panelView)
	}
	drive(m, key("esc"))
	if m.panelView != "" {
		t.Fatal("esc deveria voltar ao menu do painel")
	}
}

// shortTarget is a folder that does not exist yet, short enough for a
// server (a macOS temp dir is too deep for MVDSV).
func shortTarget(t *testing.T) string {
	t.Helper()
	base := "/tmp"
	if runtime.GOOS == "windows" {
		base = ""
	}
	dir, err := os.MkdirTemp(base, "x86qw-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	return filepath.Join(dir, "x86qw")
}

// busyPort is the demo with something holding UDP 27500: whatever the
// machine's ports, the preflight warns while the server starts there.
type busyPort struct{ *setup.Demo }

func (b busyPort) Preflight(ctx context.Context, options setup.Options) <-chan setup.Check {
	out := make(chan setup.Check)
	go func() {
		defer close(out)
		for check := range b.Demo.Preflight(ctx, options) {
			if check.ID == "ports" && check.State != setup.CheckRunning {
				check.State, check.Says.Value = setup.CheckOK, setup.Say("ports.free")
				if options.Server.BasePort == 27500 {
					check.State, check.Says.Value = setup.CheckWarning, setup.Say("ports.busy")
				}
			}
			out <- check
		}
	}()
	return out
}

func TestPreflightWarningWaitsAndCorrects(t *testing.T) {
	m := New(Config{Backend: busyPort{&setup.Demo{Speed: 50}}, Target: shortTarget(t), Lang: PT, Theme: "dark"})
	drive(m, tea.WindowSizeMsg{Width: 100, Height: 34})
	drive(m, run(m.Init())...)
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
	drive(m, key("enter"), key("down"), key("right"), key("enter"))
	if m.opts.Server.BasePort != 27501 {
		t.Fatalf("porta inicial: %d", m.opts.Server.BasePort)
	}
	if m.screen != scConfigure || !m.preDone || m.preflightOutcome() != setup.CheckOK {
		t.Fatalf("o pré-voo deveria rodar de novo e passar: tela %d, %v", m.screen, m.preflightOutcome())
	}
	// The checks ran again for the edit: the focus stays where the user is.
	if m.confirmRows()[m.focus].id != "server" {
		t.Fatalf("o foco saltou para %s", m.confirmRows()[m.focus].id)
	}
	m.Update(key("i"))
	if m.screen != scInstall {
		t.Fatalf("enter deveria instalar: %d", m.screen)
	}
	m.cancel()
}

func TestBodyKeepsItsPlace(t *testing.T) {
	m := started(t, false)
	drive(m, typeText("x86dx2")...)
	top := func() int {
		m.View()
		return m.tops[m.screen]
	}
	first := top()
	for _, k := range []string{"down", "down", "up", "up", "down"} {
		drive(m, key(k))
		if got := top(); got != first {
			t.Fatalf("o corpo mudou de lugar com %s: %d → %d", k, first, got)
		}
	}
}

func TestPercentStaysPut(t *testing.T) {
	first := bigNumber(0, unicodeGlyphs)
	for value := 0; value <= 100; value++ {
		for index, row := range bigNumber(value, unicodeGlyphs) {
			runes, want := []rune(row), []rune(first[index])
			if len(runes) != len(want) || len(runes) > 30 {
				t.Fatalf("%d%% tem %d colunas, 0%% tem %d", value, len(runes), len(want))
			}
			// The sign is the art after the three digit cells.
			sign := len(runes) - artWidth(bigDigits['%']) - 1
			if string(runes[sign:]) != string(want[sign:]) {
				t.Fatalf("o %% andou em %d%%", value)
			}
		}
	}
}

func TestDialogEnterKeepsEscCancels(t *testing.T) {
	m := started(t, false)
	drive(m, typeText("x86dx2")...)
	drive(m, key("enter"))
	m.focus = 0
	drive(m, key("enter")) // Components
	content := m.opts.Content
	drive(m, key("down"), space())
	if m.modal == nil || m.opts.Content == content {
		t.Fatal("espaço deveria mudar o pacote e manter o diálogo aberto")
	}
	drive(m, key("esc"))
	if m.modal != nil || m.opts.Content != content {
		t.Fatalf("esc deveria cancelar: %q", m.opts.Content)
	}
	drive(m, key("enter"), key("down"), key("enter"))
	if m.modal != nil || m.opts.Content == content {
		t.Fatalf("enter deveria escolher o pacote em foco e fechar: %q", m.opts.Content)
	}

	// The setup dialog keeps one size across its tabs.
	m.focus = 1
	drive(m, key("enter"))
	m.View()
	height := len(must(m.modalView()))
	for range 4 {
		drive(m, key("tab"))
		m.View()
		if got := len(must(m.modalView())); got != height {
			t.Fatalf("o diálogo mudou de tamanho: %d → %d", height, got)
		}
	}
}

func must(lines []string, _ []hit, _ *[2]int) []string { return lines }

func TestFinishedInstallCountsEveryFile(t *testing.T) {
	demo := &setup.Demo{Speed: 50}
	options := setup.Defaults(setup.Both, t.TempDir())
	var last setup.Progress
	for progress := range demo.Install(context.Background(), options, nil) {
		last = progress
	}
	if !last.Finished || filesDone(last) != filesTotal(last) {
		t.Fatalf("terminou com %d de %d arquivos", filesDone(last), filesTotal(last))
	}
}

// failing installs nothing: its installation fails at once.
type failing struct{ *setup.Demo }

func (failing) Install(context.Context, setup.Options, <-chan bool) <-chan setup.Progress {
	out := make(chan setup.Progress, 1)
	out <- setup.Progress{Err: errors.New("download de ktx falhou: todos os mirrors falharam")}
	close(out)
	return out
}

func TestFailedInstallExplainsAndGoesBack(t *testing.T) {
	m := New(Config{Backend: failing{&setup.Demo{}}, Target: t.TempDir(), Lang: PT})
	drive(m, tea.WindowSizeMsg{Width: 100, Height: 34})
	drive(m, run(m.Init())...)
	drive(m, typeText("x86dx2")...)
	drive(m, key("enter"), key("i"))
	drive(m, key("i"))
	if m.screen != scInstall || m.progress.Err == nil {
		t.Fatalf("o erro deveria ficar no cockpit: tela %d, %v", m.screen, m.progress.Err)
	}
	if view := m.View().Content; !strings.Contains(view, "mirrors falharam") {
		t.Fatal("o cockpit deveria mostrar o erro")
	}
	drive(m, key("enter"))
	if m.screen != scConfigure || m.preOn {
		t.Fatalf("enter deveria voltar à configuração: tela %d", m.screen)
	}
}

// TestDoneOpensTheApp: the last step leads into the installation's app,
// and esc goes to its home.
func TestDoneOpensTheApp(t *testing.T) {
	m := started(t, true)
	m.opts = setup.Defaults(setup.Both, m.cfg.Target)
	for id, want := range map[string]screen{"play": scMatch, "server": scHost, "find": scServers} {
		m.screen = scDone
		m.activateDone(id)
		if m.screen != want || m.next != nil {
			t.Fatalf("%s: tela %d, próximo %v", id, m.screen, m.next)
		}
	}
	m.screen = scDone
	drive(m, key("esc"))
	if m.screen != scPanel {
		t.Fatalf("esc no fim deveria ir ao início: %d", m.screen)
	}
}

func TestPanelRepairsForReal(t *testing.T) {
	m := started(t, true)
	m.focus = homeRow(m, "repair")
	drive(m, key("enter"))
	if m.panelView != "repair" {
		t.Fatalf("painel: %q", m.panelView)
	}
	drive(m, key("enter"))
	if m.busy || m.report == nil || m.report.Files == 0 {
		t.Fatalf("reparo: ocupado %v, %+v", m.busy, m.report)
	}
}

// homeRow is where a row sits on the home screen.
func homeRow(m *Model, id string) int {
	for index, row := range m.panelRows() {
		if row.id == id {
			return index
		}
	}
	return -1
}

func TestOneTickLoopAtMost(t *testing.T) {
	m := started(t, true)
	m.ticking = false
	if m.tick() == nil || m.tick() != nil {
		t.Fatal("um segundo tick não deve ser agendado enquanto o primeiro corre")
	}
	m.screen, m.panelView, m.toastUntil = scMatch, "", time.Time{}
	m.game.catalog = &setup.MatchCatalog{}
	m.game.stackLoaded = true
	if _, cmd := m.Update(tickMsg(time.Now())); cmd != nil || m.ticking {
		t.Fatal("com a tela parada, o laço deve parar")
	}
}

func TestCtrlCQuitsAfterAFailedInstall(t *testing.T) {
	m := New(Config{Backend: failing{&setup.Demo{}}, Target: t.TempDir(), Lang: PT})
	drive(m, tea.WindowSizeMsg{Width: 100, Height: 34})
	drive(m, run(m.Init())...)
	drive(m, typeText("x86dx2")...)
	drive(m, key("enter"), key("i"))
	drive(m, key("i"))
	if m.progress.Err == nil {
		t.Fatal("a instalação deveria ter falhado")
	}
	m.Update(key("ctrl+c"))
	if _, cmd := m.Update(key("ctrl+c")); cmd == nil || m.cancelling {
		t.Fatal("ctrl+c duas vezes deve sair, não cancelar o que já acabou")
	}
}
