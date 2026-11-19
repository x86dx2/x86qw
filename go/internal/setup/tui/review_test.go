package tui

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
	"github.com/charmbracelet/x/ansi"

	"github.com/x86dx2/x86qw/go/internal/setup"
)

// offline is the demo without the published catalog.
type offline struct{ *setup.Demo }

func (o offline) Catalog(ctx context.Context) (*setup.Catalog, error) {
	cat, err := o.Demo.Catalog(ctx)
	if err == nil {
		cat.Unavailable = errors.New("no network")
	}
	return cat, err
}

func TestOfflineInstallationStillOpensAndNewOneRetries(t *testing.T) {
	m := New(Config{Backend: offline{&setup.Demo{ExistingInstall: true, Speed: 50}}, Target: shortTarget(t), Lang: PT})
	drive(m, tea.WindowSizeMsg{Width: 100, Height: 34})
	drive(m, run(m.Init())...)
	if m.err != nil || m.screen != scMatch {
		t.Fatalf("without a catalog, the installation still opens: %v, screen %d", m.err, m.screen)
	}
	toConsole(m)
	m.focus = homeRow(m, "change")
	drive(m, key("enter"))
	if m.screen != scPanel {
		t.Fatal("without a catalog, nothing new can be installed")
	}
	fresh := New(Config{Backend: offline{&setup.Demo{Speed: 50}}, Target: shortTarget(t), Lang: PT})
	drive(fresh, tea.WindowSizeMsg{Width: 100, Height: 34})
	drive(fresh, run(fresh.Init())...)
	if fresh.err == nil || !strings.Contains(fresh.View().Content, "no network") {
		t.Fatal("with no installation and no catalog, the screen says why")
	}
	fresh.cfg.Backend = &setup.Demo{Speed: 50}
	drive(fresh, key("r"))
	if fresh.err != nil || fresh.catalog == nil || fresh.screen != scIntent {
		t.Fatalf("r tries again: %v", fresh.err)
	}
}

// Once the network comes back, the quiet check that reaches the catalog
// unblocks Update and Change without restarting the app.
func TestTheNetworkComingBackUnblocksUpdateAndChange(t *testing.T) {
	m := New(Config{Backend: offline{&setup.Demo{ExistingInstall: true, Speed: 50}}, Target: shortTarget(t), Lang: PT})
	drive(m, tea.WindowSizeMsg{Width: 100, Height: 34})
	drive(m, run(m.Init())...)
	m.existing.UpdatesErr = errors.New("no network")
	online := &setup.Demo{ExistingInstall: true}
	cat, _ := online.Catalog(context.Background())
	found, _ := online.Existing(context.Background(), m.existing.Target)
	drive(m, freshMsg{catalog: cat, existing: found})
	toConsole(m)
	m.focus = homeRow(m, "update")
	drive(m, key("enter"))
	if m.panelView != "update" {
		t.Fatalf("Update stays refused: %q", m.toast)
	}
	drive(m, key("esc"))
	m.focus = homeRow(m, "change")
	drive(m, key("enter"))
	if m.screen != scConfigure {
		t.Fatalf("Change stays refused: %q", m.toast)
	}
}

// TestUpdateRunsFromHomeAndBack: Atualizar applies from home and esc, or
// the end, returns there.
func TestUpdateRunsFromHomeAndBack(t *testing.T) {
	m := started(t, true)
	toConsole(m)
	m.focus = homeRow(m, "update")
	drive(m, key("enter"), key("enter"))
	if m.screen != scConfigure || !m.preOn || !m.opts.Upgrade || !m.fromPanel {
		t.Fatalf("apply opens the preflight from home: screen %d", m.screen)
	}
	drive(m, key("esc"), key("esc"))
	if m.screen != scPanel || m.panelView != "" || m.opts.Upgrade || m.panelRows()[m.focus].id != "update" {
		t.Fatalf("esc goes back home, on Update: screen %d %q", m.screen, m.panelView)
	}
	drive(m, key("enter"), key("enter"))
	drive(m, key("i"))
	if m.progress.Total == 0 || !m.progress.Finished {
		t.Fatalf("apply downloads the updates: %d bytes", m.progress.Total)
	}
	if m.screen != scPanel || !strings.Contains(m.toast, m.t("done.applied")) || m.panelRows()[m.focus].id != "update" {
		t.Fatalf("the end goes back home: screen %d", m.screen)
	}
}

// The size Apply shows is what the update downloads, packages that are not
// components (ezQuake, the CLI, the core data) included.
func TestApplyShowsWhatTheUpdateDownloads(t *testing.T) {
	m := started(t, true)
	toConsole(m)
	m.focus = homeRow(m, "update")
	drive(m, key("enter"), key("enter"))
	shown := m.totalBytes()
	drive(m, key("i"))
	if shown != m.progress.Total {
		t.Fatalf("Apply shows %d bytes, the update downloads %d", shown, m.progress.Total)
	}
}

func TestNothingToDownloadReadsAsDone(t *testing.T) {
	m := started(t, true)
	m.screen, m.progress = scInstall, setup.Progress{Finished: true}
	if view := m.View().Content; !strings.Contains(view, strings.TrimSpace(bigNumber(100, m.glyphs)[1])) {
		t.Fatal("finished with no bytes is 100%")
	}
	m.screen = scDone
	if view := m.View().Content; !strings.Contains(view, m.t("done.nothing")) {
		t.Fatal("the end says there was nothing to download")
	}
}

func TestRemovedInstallationOnlyLeaves(t *testing.T) {
	m := started(t, true)
	m.screen, m.panelView, m.removed = scPanel, "remove", true
	if cmd := m.key("enter"); cmd != nil || m.busy {
		t.Fatal("after uninstalling, enter does nothing")
	}
	if m.key("esc") != nil || m.key("esc") == nil {
		t.Fatal("esc twice quits")
	}
}

// failingOpen is the demo with a connection that fails.
type failingOpen struct{ *setup.Demo }

func (failingOpen) Open(context.Context, string, string) error {
	return errors.New("ezQuake did not open")
}

func TestServersShowWhatFailsAndFavoritesFirst(t *testing.T) {
	m := New(Config{Backend: failingOpen{&setup.Demo{ExistingInstall: true, Speed: 50}}, Target: shortTarget(t), Lang: PT, Start: "servers"})
	drive(m, tea.WindowSizeMsg{Width: 100, Height: 34})
	drive(m, run(m.Init())...)
	if m.screen != scServers || len(m.game.servers) == 0 {
		t.Fatalf("servers: screen %d", m.screen)
	}
	m.View()
	top := m.tops[scServers]
	if !m.visibleServers()[0].Favorite {
		t.Fatal("favorites first")
	}
	m.focus = 1
	drive(m, key("ctrl+f"))
	if favorites := m.visibleServers(); !favorites[0].Favorite || !favorites[1].Favorite {
		t.Fatal("ctrl+f saves the server to favorites")
	}
	drive(m, key("enter"))
	if view := m.View().Content; !strings.Contains(view, "ezQuake did not open") || m.tops[scServers] != top {
		t.Fatal("the join error shows in the dock without moving the list")
	}
	if strings.Contains(m.toast, "conectando") {
		t.Fatal("no toast before connecting")
	}
	drive(m, tea.MouseWheelMsg(tea.Mouse{Button: tea.MouseWheelDown}))
	if m.focus != 2 {
		t.Fatalf("the wheel scrolls the list: %d", m.focus)
	}
	m.Update(m.click("hint:ctrl+r"))
	if !m.game.serversLoading {
		t.Fatal("clicking ctrl+r in the footer refreshes")
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
		t.Fatalf("the password takes no quotes: %q", m.opts.Server.Password)
	}
	m.modal.tab = 0
	m.opts.Server.Name, m.modal.field["srv.name"].Value = "", nil
	m.modal.tab = 3
	drive(m, key("enter"))
	if m.modal == nil || m.modal.tab != 0 {
		t.Fatal("enter with an empty name stays in the dialog, on the General tab")
	}
	view := m.View().Content
	if !strings.Contains(view, "dê um nome ao servidor") {
		t.Fatal("the problem shows on the tab that has it")
	}
	drive(m, key("esc"))
	if m.opts.Server.Name == "" {
		t.Fatal("esc gives the name back")
	}
	// Invalid choices do not run the checks; why stays under the setup.
	m.opts.Server.Name = ""
	drive(m, key("i"))
	if m.preOn || m.confirmErr == nil || !strings.Contains(m.View().Content, "dê um nome ao servidor") {
		t.Fatal("invalid choices do not run the preflight and say why")
	}
}

func TestHostStopsOnASecondPressAndAddRestores(t *testing.T) {
	m := started(t, true)
	toContext(m, "3")
	m.focus = len(m.hostRows()) - 1
	drive(m, key("enter"))
	drive(m, run(m.loadStack())...)
	if m.game.stack == nil || m.focus != 0 {
		t.Fatal("the server starts and the focus goes back to the top")
	}
	drive(m, key("enter"))
	if m.game.acting != "" || m.game.stack == nil {
		t.Fatal("stopping asks a second time")
	}
	if hints := m.footerHints(); hints[0][1] != m.t("stop.again") {
		t.Fatalf("the footer asks for confirmation: %v", hints)
	}
	drive(m, key("enter"))
	drive(m, run(m.loadStack())...)
	if m.game.stack != nil {
		t.Fatal("the second time stops")
	}

	// A host without the server programs: add them, then change our mind.
	m.existing.Installed = []string{"ktx"}
	m.opts.Intent = setup.Play
	drive(m, key("enter"))
	if m.screen != scConfigure || m.opts.Intent != setup.Both {
		t.Fatal("adding the server opens the setup as client and server")
	}
	drive(m, key("esc"))
	if m.screen != scHost || m.opts.Intent != setup.Play {
		t.Fatalf("esc gives the choices back and returns to the server: screen %d %s", m.screen, m.opts.Intent)
	}
}

func TestBackFromAScreenLandsOnItsContext(t *testing.T) {
	m := started(t, true)
	for _, number := range []string{"2", "3"} {
		toContext(m, number)
		drive(m, key("esc"))
		if !m.railOn || m.railFocus != ctx(number[0]-'1') {
			t.Fatalf("from context %s back to the rail: on %v, context %d", number, m.railOn, m.railFocus)
		}
	}
	toConsole(m)
	drive(m, key("esc"))
	if !m.railOn || m.railFocus != ctxConsole {
		t.Fatalf("from Console back to the rail: on %v, context %d", m.railOn, m.railFocus)
	}
}

func TestOnePollAtATime(t *testing.T) {
	m := started(t, true)
	m.game.polling = false
	if m.pollStack() == nil || m.pollStack() != nil {
		t.Fatal("a second poll does not start while the first waits")
	}
}

func TestErrorsStayOnTheirScreen(t *testing.T) {
	m := started(t, true)
	m.Update(stackMsg{err: errors.New("unreadable state")})
	m.screen = scMatch
	if m.errorHere() != nil {
		t.Fatal("the server error does not show in the match")
	}
	m.screen = scHost
	if m.errorHere() == nil {
		t.Fatal("the server error shows on the server")
	}
}

func TestLocalServerAndUnknownRuleset(t *testing.T) {
	m := started(t, true)
	m.opts.Server.Local = true
	if m.connectHost() != "127.0.0.1" {
		t.Fatal("a local server is 127.0.0.1")
	}
	m.screen = scDone
	if view := m.View().Content; strings.Contains(view, m.t("ports.open")) {
		t.Fatal("no router hint for a local server")
	}
	if m.ruleset("novo") != "novo" {
		t.Fatal("an unknown ruleset shows by its name")
	}
}

func TestPortsStepByTen(t *testing.T) {
	m := started(t, true)
	toContext(m, "3")
	drive(m, key("enter"), key("down"))
	base := m.opts.Server.BasePort
	drive(m, key("shift+right"))
	if m.opts.Server.BasePort != base+10 {
		t.Fatalf("shift+→ adds ten: %d → %d", base, m.opts.Server.BasePort)
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
	toConsole(m)
	m.focus = homeRow(m, "update")
	drive(m, key("enter"))
	view := m.View().Content
	for _, word := range []string{"Componente", "Dados do Quake", "Base e inicialização"} {
		if strings.Contains(view, word) {
			t.Fatalf("%q in English:\n%s", word, view)
		}
	}
}

// The inventory speaks English; Portuguese shows its components in
// Portuguese all the same.
func TestPortugueseShowsTheInventoryInPortuguese(t *testing.T) {
	m := started(t, true)
	m.lang = PT
	toConsole(m)
	m.focus = homeRow(m, "update")
	drive(m, key("enter"))
	view := m.View().Content
	for _, word := range []string{"Componente", "Dados do Quake", "Base e inicialização"} {
		if !strings.Contains(view, word) {
			t.Fatalf("%q missing in Portuguese:\n%s", word, view)
		}
	}
	for _, word := range []string{"Quake data", "client base and startup"} {
		if strings.Contains(view, word) {
			t.Fatalf("%q in Portuguese:\n%s", word, view)
		}
	}
}

// Marking a favorite moves it up the list, and the focus goes with it.
func TestTheFocusFollowsANewFavorite(t *testing.T) {
	m := New(Config{Backend: &setup.Demo{ExistingInstall: true, Speed: 50}, Target: shortTarget(t), Lang: EN, Start: "servers"})
	drive(m, tea.WindowSizeMsg{Width: 100, Height: 34})
	drive(m, run(m.Init())...)
	servers := m.visibleServers()
	m.focus = len(servers) - 1
	if servers[m.focus].Favorite {
		t.Fatal("the last server should not be a favorite yet")
	}
	address := servers[m.focus].Address
	drive(m, key("ctrl+f"))
	if focused := m.focusedServer(); focused == nil || focused.Address != address || !focused.Favorite {
		t.Fatalf("the focus left %s for %+v", address, focused)
	}
}

// A copied line brings its line break along: pasted, it adds no space to
// the folder; and what a field's limit leaves out is said.
func TestPasteDropsTheLineBreakAndSaysWhatIsCut(t *testing.T) {
	m := started(t, false)
	drive(m, typeText("x86dx2")...)
	drive(m, key("enter"))
	m.openModal("folder")
	m.modal.field["folder"].Set("")
	drive(m, tea.PasteMsg{Content: "/Users/x86/Games/qw\r\n"})
	if m.opts.Target != "/Users/x86/Games/qw" {
		t.Fatalf("pasted folder %q", m.opts.Target)
	}
	m.modal = nil
	m.openModal("server")
	m.modal.tab = 1
	for index, row := range focusables(m.modalRows()) {
		if row.id == "password" {
			m.modal.focus = index
		}
	}
	m.toast = ""
	drive(m, tea.PasteMsg{Content: strings.Repeat("Ab3", 14)})
	if len(m.opts.Server.Password) != 32 || m.toast == "" {
		t.Fatalf("password %q, toast %q", m.opts.Server.Password, m.toast)
	}
}

// The caret sits after the last character typed, in columns: a CJK name
// takes two each, an accent written as its own mark none.
func TestTheCaretFollowsWideAndCombiningCharacters(t *testing.T) {
	for _, target := range []string{"/Users/田中太郎/Games/x86qw", "/Users/田中太郎/ゲーム/クエイク/x86qw", "/Users/x86/Música/x86qw"} {
		m := New(Config{Backend: &setup.Demo{Speed: 50}, Target: target, Lang: EN})
		drive(m, tea.WindowSizeMsg{Width: 80, Height: 24})
		drive(m, run(m.Init())...)
		drive(m, typeText("x86dx2")...)
		drive(m, key("enter"))
		m.openModal("folder")
		lines, _, caret := m.modalView()
		if caret == nil {
			t.Fatalf("%s: no caret", target)
		}
		if before := ansi.Strip(ansi.Cut(lines[caret[1]], 0, caret[0])); !strings.HasSuffix(before, "/x86qw") {
			t.Errorf("%s: the caret follows %q", target, before)
		}
	}
}

// A dialog that grows (an instance added, a port shown) keeps its top row
// while it fits: nothing in it moves.
func TestAGrowingDialogKeepsItsTop(t *testing.T) {
	m := started(t, false)
	drive(m, tea.WindowSizeMsg{Width: 100, Height: 40})
	drive(m, typeText("x86dx2")...)
	drive(m, key("down"), key("down"), key("enter"))
	m.openModal("server")
	top := func() int {
		for row, line := range strings.Split(ansi.Strip(m.View().Content), "\n") {
			if strings.Contains(line, "╭─") {
				return row
			}
		}
		return -1
	}
	want := top()
	for _, row := range focusables(m.modalRows()) {
		if row.id == "add" {
			row.activate()
		}
	}
	if got := top(); got != want {
		t.Fatalf("the dialog moved from row %d to %d", want, got)
	}
}

// The Keys tab shows the whole keyboard and every binding from the
// narrowest dialog up.
func TestTheKeysTabShowsEveryBinding(t *testing.T) {
	for _, width := range []int{60, 64, 69, 72, 80} {
		for _, lang := range []Lang{EN, PT} {
			m := New(Config{Backend: &setup.Demo{Speed: 50}, Target: shortTarget(t), Lang: lang})
			drive(m, tea.WindowSizeMsg{Width: width, Height: 40})
			drive(m, run(m.Init())...)
			drive(m, typeText("x86dx2")...)
			drive(m, key("enter"))
			m.opts.Keys = setup.KeyPresets["arrows"]
			m.openModal("config")
			lines, _, _ := m.modalView()
			text := ansi.Strip(strings.Join(lines, "\n"))
			for _, line := range strings.Split(text, "\n") {
				if strings.Contains(line, " Z ") && !strings.Contains(line, "→") {
					t.Errorf("%d columns: the → key is cut: %q", width, line)
				}
			}
			if !strings.Contains(text, m.t("a.jump")) {
				t.Errorf("%d columns, %s: the jump binding is cut", width, lang)
			}
		}
	}
}

// A footer too wide for the window drops whole hints before the last one:
// esc, the way out, always shows, and no hint is cut in half.
func TestTheFooterKeepsEscAndWholeHints(t *testing.T) {
	m := New(Config{Backend: &setup.Demo{ExistingInstall: true, Speed: 50}, Target: shortTarget(t), Lang: PT, Start: "servers"})
	drive(m, tea.WindowSizeMsg{Width: 80, Height: 24})
	drive(m, run(m.Init())...)
	items := m.footerHints()
	line, hits := hints(m.theme, items, m.w-4, "")
	text := ansi.Strip(line)
	last := items[len(items)-1]
	if strings.Contains(text, "…") || !strings.HasSuffix(text, last[0]+" "+last[1]) {
		t.Fatalf("footer %q", text)
	}
	if end := hits[len(hits)-1]; end.id != "hint:"+last[0] || end.x1 != lipgloss.Width(line) {
		t.Fatalf("the last hit %+v does not end the line of %d", end, lipgloss.Width(line))
	}
}
