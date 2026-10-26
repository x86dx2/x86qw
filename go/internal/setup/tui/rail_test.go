package tui

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"
	"github.com/charmbracelet/x/ansi"

	"github.com/x86dx2/x86qw/go/internal/setup"
)

// stripANSI is the screen as text, without its colours.
func stripANSI(s string) string { return ansi.Strip(s) }

// The first run has no tabs: it is the installer, full screen, and esc
// quits it.
func TestInstallerHasNoTabs(t *testing.T) {
	m := started(t, false)
	if m.screen != scIntent {
		t.Fatalf("a new install opens the intent: screen %d", m.screen)
	}
	if _, ok := m.contextOf(); ok {
		t.Fatal("the installer is not a context: it has no tabs")
	}
	drive(m, key("esc"))
	if m.railOn || m.armed != "quit" {
		t.Fatalf("esc in the installer quits, not the tabs: rail %v, armed %q", m.railOn, m.armed)
	}
}

// Tabs appear once an installation exists: four of them, numbered 1–4.
func TestTabsAppearWithAnInstallation(t *testing.T) {
	m := started(t, true)
	if c, ok := m.contextOf(); !ok || c != ctxPlay {
		t.Fatalf("an installation opens Play: context %d, %v", c, ok)
	}
	row, _, _ := m.tabs()
	for _, label := range []string{"1 Jogar", "2 Partidas", "3 Hospedar", "4 Instalação"} {
		if !strings.Contains(stripANSI(row), label) {
			t.Fatalf("the tabs name %q: %s", label, stripANSI(row))
		}
	}
}

// A click on a tab opens its context.
func TestClickOnTheTabsOpensAContext(t *testing.T) {
	m := started(t, true)
	drive(m, run(m.click("ctx:2"))...)
	if m.screen != scServers || m.railOn {
		t.Fatalf("click on Matches opens the servers: screen %d, tabs %v", m.screen, m.railOn)
	}
	drive(m, run(m.click("ctx:3"))...)
	if m.screen != scHost {
		t.Fatalf("click on Host opens the host: screen %d", m.screen)
	}
}

// The tabs are locked while an install runs, and while anything else is
// busy: a click there does nothing.
func TestTabsAreLockedDuringAnInstall(t *testing.T) {
	m := started(t, true)
	m.screen = scInstall
	if m.canRail() {
		t.Fatal("the tabs must not open during an install")
	}
	m.screen = scPanel
	m.busy = true
	if m.canRail() {
		t.Fatal("the tabs must not open while something runs")
	}
	m.busy, m.screen = false, scInstall
	if m.click("ctx:1") != nil || m.screen != scInstall {
		t.Fatalf("a click on the locked tabs does nothing: screen %d", m.screen)
	}
}

// Leaving a context and coming back lands on its first screen with its
// state kept: the match chosen in Play is still there.
func TestSwitchingContextsKeepsTheirState(t *testing.T) {
	m := started(t, true)
	m.game.match.Mode = "ffa"
	toContext(m, "1")
	if m.screen != scMatch {
		t.Fatalf("Play opens the match: screen %d", m.screen)
	}
	drive(m, key("esc"))
	toConsole(m)
	drive(m, key("esc"))
	toContext(m, "1")
	if m.screen != scMatch || m.game.match.Mode != "ffa" {
		t.Fatalf("back in Play, the match is kept: screen %d, mode %q", m.screen, m.game.match.Mode)
	}
}

// Esc from a context's first screen climbs to the tabs, and a second esc
// quits: the same double press as everywhere else.
func TestEscClimbsToTheTabsThenQuits(t *testing.T) {
	m := started(t, true)
	toContext(m, "2")
	drive(m, key("esc"))
	if !m.railOn || m.railFocus != ctxFind {
		t.Fatalf("esc on Matches climbs to the tabs: on %v, context %d", m.railOn, m.railFocus)
	}
	if m.armed != "" {
		t.Fatal("the first esc on the tabs only arms the quit")
	}
	drive(m, key("esc"))
	if m.armed != "quit" {
		t.Fatalf("a second esc arms the quit: %q", m.armed)
	}
}

// ← and → walk the tabs, wrapping, and the content follows each one; ↓
// goes into the content.
func TestArrowsWrapAlongTheTabs(t *testing.T) {
	m := started(t, true)
	drive(m, key("esc"))
	drive(m, key("right"))
	if m.railFocus != ctxFind || m.screen != scServers || !m.railOn {
		t.Fatalf("→ moves to Matches and opens it: context %d, screen %d, tabs %v", m.railFocus, m.screen, m.railOn)
	}
	drive(m, key("left"), key("left"))
	if m.railFocus != ctxConsole || m.screen != scPanel {
		t.Fatalf("← twice from Matches wraps to Console: context %d, screen %d", m.railFocus, m.screen)
	}
	drive(m, key("down"))
	if m.railOn {
		t.Fatal("↓ goes into the content")
	}
}

// Console is where the installation is kept: its folder is on the screen.
func TestConsoleShowsTheInstallation(t *testing.T) {
	m := started(t, true)
	toConsole(m)
	view := stripANSI(m.View().Content)
	if !strings.Contains(view, setup.ShortPath(m.existing.Target)) {
		t.Fatalf("Console names the folder of the installation:\n%s", view)
	}
}

// TestRailSnapshots renders the first run and each tab at the sizes the
// layout changes at, into X86QW_SNAPSHOTS.
func TestRailSnapshots(t *testing.T) {
	dir := os.Getenv("X86QW_SNAPSHOTS")
	if dir == "" {
		t.Skip("set X86QW_SNAPSHOTS=<folder> to write the captures")
	}
	os.MkdirAll(dir, 0o755)
	save := func(m *Model, name string) {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(m.View().Content), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	sizes := []struct{ w, h int }{{100, 34}, {80, 24}, {60, 18}}
	for _, size := range sizes {
		first := New(Config{Backend: &setup.Demo{Speed: 50}, Target: "/Users/x86/Games/x86qw", Lang: PT, Demo: true, Theme: "dark"})
		drive(first, tea.WindowSizeMsg{Width: size.w, Height: size.h})
		drive(first, run(first.Init())...)
		save(first, fmt.Sprintf("installer-%d-%d.ansi", size.w, size.h))

		m := New(Config{Backend: &setup.Demo{ExistingInstall: true, Speed: 50}, Target: "/Users/x86/Games/x86qw", Lang: PT, Demo: true, Theme: "dark"})
		drive(m, tea.WindowSizeMsg{Width: size.w, Height: size.h})
		drive(m, run(m.Init())...)
		for number := 1; number <= int(ctxCount); number++ {
			toContext(m, fmt.Sprint(number))
			save(m, fmt.Sprintf("tabs-%d-%d-%d.ansi", size.w, size.h, number))
		}
	}
}
