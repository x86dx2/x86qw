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
			t.Fatalf("%s: screen %d, expected %d", label, m.screen, s)
		}
	}
	drive(m, key("enter"))
	if !m.nickFlag {
		t.Fatal("enter without a nick should flag the field")
	}
	drive(m, typeText("x86dx2")...)
	drive(m, key("down"))
	if m.opts.Intent != setup.Host {
		t.Fatalf("↓ should pick Host: %s", m.opts.Intent)
	}
	drive(m, key("up"), key("enter"))
	want(scConfigure, "enter on the intent goes to the setup")
	drive(m, key("esc"))
	want(scIntent, "esc on the setup goes back to the intent")
	drive(m, key("enter"))
	want(scConfigure, "intent → setup")
	m.Update(key("i")) // without running what it starts
	if m.screen != scConfigure || !m.preOn {
		t.Fatal("install should open the preflight on the setup itself")
	}
	m.Update(key("esc"))
	if m.screen != scConfigure || m.preOn {
		t.Fatal("esc should fold the preflight and stay on the setup")
	}
	drive(m, key("i"))
	want(scConfigure, "the preflight ends and waits for confirmation")
	if !m.preDone || m.confirmRows()[m.focus].id != "confirm" {
		t.Fatalf("the focus should go to Confirm and install: %d", m.focus)
	}
	m.Update(key("enter"))
	want(scInstall, "enter confirma e instala")
	m.cancel()
	m.screen, m.focus, m.modal = scConfigure, 0, nil

	// Dialog: open setup, change tabs with ← →, toggle with space.
	m.focus = 1
	drive(m, key("enter"))
	if m.modal == nil || m.modal.kind != "config" {
		t.Fatal("enter should open Your setup")
	}
	drive(m, key("right"))
	if m.modal.tab != 1 {
		t.Fatalf("→ should switch tabs: %d", m.modal.tab)
	}
	drive(m, key("down"), key("down"))
	invert := m.opts.Mouse.Invert
	drive(m, space())
	if m.opts.Mouse.Invert == invert {
		t.Fatal("space should toggle the inverted axis")
	}
	drive(m, key("tab"), key("tab"), key("tab"))
	if m.modal.tab != 0 {
		t.Fatalf("tab should wrap around the tabs: %d", m.modal.tab)
	}
	drive(m, key("esc"))
	if m.modal != nil {
		t.Fatal("esc should close the dialog")
	}
	drive(m, key("ctrl+l"))
	if m.lang != EN {
		t.Fatal("ctrl+l should switch the language")
	}
}

func TestPanelNavigation(t *testing.T) {
	m := started(t, true)
	if m.screen != scMatch {
		t.Fatalf("an existing installation opens Play: screen %d", m.screen)
	}
	toConsole(m)
	if m.screen != scPanel {
		t.Fatalf("the rail opens Console: screen %d", m.screen)
	}
	m.focus = homeRow(m, "change")
	drive(m, key("enter"))
	if m.screen != scConfigure {
		t.Fatalf("Components and setup should open the review: %d", m.screen)
	}
	drive(m, key("esc"))
	if m.screen != scPanel {
		t.Fatalf("enter should go back to the setup: screen %d", m.screen)
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
		t.Fatalf("a warning should wait on the review: screen %d", m.screen)
	}
	// Correct it in place: Server → first port 27501, then close.
	for index, row := range m.confirmRows() {
		if row.id == "server" {
			m.focus = index
		}
	}
	drive(m, key("enter"), key("down"), key("right"), key("enter"))
	if m.opts.Server.BasePort != 27501 {
		t.Fatalf("first port: %d", m.opts.Server.BasePort)
	}
	if m.screen != scConfigure || !m.preDone || m.preflightOutcome() != setup.CheckOK {
		t.Fatalf("the preflight should run again and pass: screen %d, %v", m.screen, m.preflightOutcome())
	}
	// The checks ran again for the edit: the focus stays where the user is.
	if m.confirmRows()[m.focus].id != "server" {
		t.Fatalf("the focus jumped to %s", m.confirmRows()[m.focus].id)
	}
	m.Update(key("i"))
	if m.screen != scInstall {
		t.Fatalf("enter should install: %d", m.screen)
	}
	m.cancel()
}

// twoWarnings is the demo whose preflight warns twice, about the folder and
// the ports: the tallest dock the review shows.
type twoWarnings struct{ *setup.Demo }

func (b twoWarnings) Preflight(ctx context.Context, options setup.Options) <-chan setup.Check {
	out := make(chan setup.Check)
	go func() {
		defer close(out)
		for check := range b.Demo.Preflight(ctx, options) {
			if check.State != setup.CheckRunning && (check.ID == "folder" || check.ID == "ports") {
				check.State = setup.CheckWarning
				check.Says.Value, check.Says.Detail, check.Says.Fix = setup.Say("folder.full"), setup.Say("folder.full.d", "~/Games/x86qw", 3), setup.Say("folder.full.fix")
			}
			out <- check
		}
	}()
	return out
}

// At the smallest size the app asks for, a preflight with two warnings
// still draws: the body scrolls in what the dock leaves it.
func TestTwoWarningsDrawAtTheSmallestSize(t *testing.T) {
	m := New(Config{Backend: twoWarnings{&setup.Demo{Speed: 50}}, Target: shortTarget(t), Lang: EN, Theme: "dark"})
	drive(m, tea.WindowSizeMsg{Width: 60, Height: 18})
	drive(m, run(m.Init())...)
	drive(m, typeText("x86dx2")...)
	drive(m, key("down"), key("down"), key("enter"), key("i"))
	if m.screen != scConfigure || m.preflightOutcome() != setup.CheckWarning {
		t.Fatalf("the warnings should wait on the review: screen %d", m.screen)
	}
}

// Keys pressed while the catalog loads wait for it, as the footer, which
// offers only ctrl+c, says.
func TestKeysWaitForTheCatalog(t *testing.T) {
	m := New(Config{Backend: &setup.Demo{Speed: 50}, Target: shortTarget(t), Lang: EN, Theme: "dark"})
	drive(m, tea.WindowSizeMsg{Width: 100, Height: 34})
	drive(m, key("down"), key("enter"), key("down"), key("enter"))
	if m.screen != scIntent || m.catalog != nil {
		t.Fatalf("the keys moved the loading app to screen %d", m.screen)
	}
}

// The key editor takes only what the client binds, as the install refuses
// the rest; a semicolon is a key like any other.
func TestKeyEditorTakesOnlyBindableKeys(t *testing.T) {
	m := started(t, false)
	before := m.opts.Keys
	for _, key := range []string{`"`, "ç"} {
		m.bindKey("jump", key)
	}
	if m.opts.Keys != before {
		t.Fatalf("keys: %+v", m.opts.Keys)
	}
	if m.bindKey("right", ";"); m.opts.Keys.Right != ";" {
		t.Fatalf("keys: %+v", m.opts.Keys)
	}
}

// withoutServer is an installation whose recorded choices hold no server,
// as a play recipe that left the field out made before it got the defaults.
type withoutServer struct {
	*setup.Demo
	existing *setup.Existing
}

func (b withoutServer) Existing(ctx context.Context, target string) (*setup.Existing, error) {
	return b.existing, nil
}

// Adding a server to such an installation starts from the default one.
func TestAddingAServerToChoicesWithoutOne(t *testing.T) {
	target := shortTarget(t)
	options := setup.Defaults(setup.Play, target)
	options.Nick, options.Server = "x86dx2", setup.Server{}
	existing := &setup.Existing{Target: target, Installed: []string{"x86qw-client-bootstrap", "ktx"}, Options: options}
	m := New(Config{Backend: withoutServer{&setup.Demo{Speed: 50}, existing}, Target: target, Lang: EN, Theme: "dark"})
	drive(m, tea.WindowSizeMsg{Width: 100, Height: 34})
	drive(m, run(m.Init())...)
	toContext(m, "3")
	drive(m, key("enter"))
	if m.screen != scConfigure || len(m.opts.Server.Instances) == 0 || m.opts.Server.Validate() != nil {
		t.Fatalf("screen %d, server %+v", m.screen, m.opts.Server)
	}
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
			t.Fatalf("the body moved with %s: %d → %d", k, first, got)
		}
	}
}

func TestPercentStaysPut(t *testing.T) {
	first := bigNumber(0, unicodeGlyphs)
	for value := 0; value <= 100; value++ {
		for index, row := range bigNumber(value, unicodeGlyphs) {
			runes, want := []rune(row), []rune(first[index])
			if len(runes) != len(want) || len(runes) > 30 {
				t.Fatalf("%d%% has %d columns, 0%% has %d", value, len(runes), len(want))
			}
			// The sign is the art after the three digit cells.
			sign := len(runes) - artWidth(bigDigits['%']) - 1
			if string(runes[sign:]) != string(want[sign:]) {
				t.Fatalf("the %% moved at %d%%", value)
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
		t.Fatal("space should change the bundle and keep the dialog open")
	}
	drive(m, key("esc"))
	if m.modal != nil || m.opts.Content != content {
		t.Fatalf("esc should cancel: %q", m.opts.Content)
	}
	drive(m, key("enter"), key("down"), key("enter"))
	if m.modal != nil || m.opts.Content == content {
		t.Fatalf("enter should pick the focused bundle and close: %q", m.opts.Content)
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
			t.Fatalf("the dialog changed size: %d → %d", height, got)
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
		t.Fatalf("finished with %d of %d files", filesDone(last), filesTotal(last))
	}
}

// failing installs nothing: its installation fails at once.
type failing struct{ *setup.Demo }

func (failing) Install(context.Context, setup.Options, <-chan bool) <-chan setup.Progress {
	out := make(chan setup.Progress, 1)
	out <- setup.Progress{Err: errors.New("ktx download failed: every mirror failed")}
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
		t.Fatalf("the error should stay in the cockpit: screen %d, %v", m.screen, m.progress.Err)
	}
	if view := m.View().Content; !strings.Contains(view, "mirror failed") {
		t.Fatal("the cockpit should show the error")
	}
	drive(m, key("enter"))
	if m.screen != scConfigure || m.preOn {
		t.Fatalf("enter should go back to the setup: screen %d", m.screen)
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
		if m.screen != want {
			t.Fatalf("%s: screen %d", id, m.screen)
		}
	}
	m.screen = scDone
	drive(m, key("esc"))
	if m.armed != "quit" {
		t.Fatalf("esc at the end of a first run quits: armed %q", m.armed)
	}
}

func TestPanelRepairsForReal(t *testing.T) {
	m := started(t, true)
	toConsole(m)
	m.focus = homeRow(m, "repair")
	drive(m, key("enter"))
	if m.panelView != "repair" {
		t.Fatalf("panel: %q", m.panelView)
	}
	drive(m, key("enter"))
	if m.busy || m.report == nil || m.report.Files == 0 {
		t.Fatalf("repair: busy %v, %+v", m.busy, m.report)
	}
}

// toContext goes to a context the way a person does: esc to the rail when
// it is not there yet, the context's number, then enter into it.
func toContext(m *Model, number string) {
	if !m.railOn {
		drive(m, key("esc"))
	}
	drive(m, key(number), key("enter"))
}

// toConsole goes to the Console context, where the installation is kept.
func toConsole(m *Model) { toContext(m, "4") }

// homeRow is where a row sits on the Console screen.
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
		t.Fatal("a second tick must not be scheduled while the first runs")
	}
	m.screen, m.panelView, m.toastUntil = scMatch, "", time.Time{}
	m.game.catalog = &setup.MatchCatalog{}
	m.game.stackLoaded = true
	if _, cmd := m.Update(tickMsg(time.Now())); cmd != nil || m.ticking {
		t.Fatal("with the screen still, the loop must stop")
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
		t.Fatal("the install should have failed")
	}
	m.Update(key("ctrl+c"))
	if _, cmd := m.Update(key("ctrl+c")); cmd == nil || m.cancelling {
		t.Fatal("ctrl+c twice must quit, not cancel what already ended")
	}
}

// The recipe dialog still draws in a terminal narrower than 16 columns.
func TestTheRecipeDialogDrawsWhenVeryNarrow(t *testing.T) {
	m := started(t, false)
	drive(m, typeText("x86dx2")...)
	m.openModal("recipe")
	drive(m, tea.WindowSizeMsg{Width: 14, Height: 34}, key("down"))
}
