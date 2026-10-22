package tui

import (
	"os"
	"path/filepath"
	"testing"

	tea "charm.land/bubbletea/v2"

	"github.com/x86dx2/x86qw/go/internal/setup"
)

// TestGameSnapshots renders the app screens into X86QW_SNAPSHOTS.
func TestGameSnapshots(t *testing.T) {
	dir := os.Getenv("X86QW_SNAPSHOTS")
	if dir == "" {
		t.Skip("set X86QW_SNAPSHOTS=<folder> to write the captures")
	}
	save := func(m *Model, name string) {
		os.WriteFile(filepath.Join(dir, name+".ansi"), []byte(m.View().Content), 0o644)
	}
	demo := &setup.Demo{ExistingInstall: true, Speed: 20}
	m := New(Config{Backend: demo, Target: "/Users/x86/Games/x86qw", Lang: PT, Demo: true, Theme: "dark"})
	drive(m, tea.WindowSizeMsg{Width: 100, Height: 34})
	drive(m, run(m.Init())...)
	save(m, "g-01-home")
	toContext(m, "1")
	save(m, "g-02-match")
	m.focus = 1
	drive(m, key("enter"))
	drive(m, typeText("in")...)
	save(m, "g-03-mode-search")
	drive(m, key("esc"))
	m.game.match.Mode = "ffa"
	drive(m, run(m.loadMaps())...)
	m.focus = 2
	drive(m, key("enter"), key("right"), key("right"), key("right"))
	save(m, "g-04-options")
	drive(m, key("enter"))
	m.focus = 3
	save(m, "g-05-match-map-open")
	drive(m, key("enter"))
	save(m, "g-06-map-picker")
	drive(m, key("esc"), key("esc"))
	toContext(m, "2")
	save(m, "g-07-servers")
	drive(m, typeText("duel")...)
	save(m, "g-08-servers-filter")
	drive(m, key("esc"), key("esc"))
	toContext(m, "3")
	save(m, "g-09-host")
	drive(m, key("enter"), key("tab"))
	save(m, "g-09b-host-access")
	drive(m, key("esc"))
	m.focus = 1
	save(m, "g-09c-host-solo")
	m.focus = len(m.hostRows()) - 1
	drive(m, key("enter"))
	drive(m, run(m.loadStack())...)
	save(m, "g-10-host-running")
}
