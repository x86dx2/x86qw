package tui

import (
	"context"
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"

	"github.com/x86dx2/x86qw/go/internal/setup"
)

// onMatch opens the demo installation on the match screen.
func onMatch(t *testing.T) *Model {
	t.Helper()
	m := New(Config{Backend: &setup.Demo{ExistingInstall: true, Speed: 50}, Target: t.TempDir(), Lang: PT, Start: "match"})
	drive(m, tea.WindowSizeMsg{Width: 100, Height: 34})
	drive(m, run(m.Init())...)
	if m.screen != scMatch || m.game.catalog == nil || m.game.match.Map == "" {
		t.Fatalf("match: screen %d, %+v", m.screen, m.game.match)
	}
	return m
}

func optionRow(t *testing.T, m *Model, id string) formRow {
	t.Helper()
	m.openModal("m.opts")
	defer func() { m.modal = nil }()
	for _, row := range m.gameModalRows() {
		if row.id == id {
			return row
		}
	}
	t.Fatalf("no row %s", id)
	return formRow{}
}

func TestBotsFollowTheRoster(t *testing.T) {
	m := onMatch(t)
	m.game.match.Mode = "duel"
	optionRow(t, m, "bots").step(+1)
	if m.game.match.Bots != 1 {
		t.Fatalf("duel has one opponent: %d", m.game.match.Bots)
	}
	optionRow(t, m, "bots").step(+1)
	if m.game.match.Bots != 0 {
		t.Fatalf("duel goes back to no bots: %d", m.game.match.Bots)
	}
	m.clearOptions()
	m.game.match.Mode = "ffa"
	optionRow(t, m, "bots").step(-1)
	if !m.game.match.FillBots {
		t.Fatal("before no bots, an open mode fills the server")
	}
	optionRow(t, m, "bots").step(+1)
	optionRow(t, m, "bots").step(+1)
	if m.game.match.FillBots || m.game.match.Bots != 1 {
		t.Fatalf("after no bots comes 1: %+v", m.game.match)
	}
}

func TestRaceAndCTFAskTheirOwnRules(t *testing.T) {
	m := onMatch(t)
	m.game.match.Mode = "race"
	m.openModal("m.opts")
	for _, row := range m.gameModalRows() {
		if row.id == "race.scoring" {
			t.Fatal("scoring only in the competitive match")
		}
	}
	m.modal = nil
	m.game.match.RaceStyle = "match"
	optionRow(t, m, "race.scoring")
	m.clearOptions()
	m.game.match.Mode = "ctf"
	for _, id := range []string{"ctf.hook", "ctf.runes", "ctf.based"} {
		optionRow(t, m, id)
	}
}

func TestModeChangeClearsTheOptions(t *testing.T) {
	m := onMatch(t)
	m.game.match.Mode, m.game.match.Bots = "ffa", 3
	m.openModal("m.mode")
	for _, row := range m.gameModalRows() {
		if row.id == "mode.duel" {
			row.activate()
		}
	}
	if m.game.match.Mode != "duel" || m.game.match.Bots != 0 {
		t.Fatalf("new mode, new options: %+v", m.game.match)
	}
	drive(m, key("esc"))
	if m.game.match.Mode != "ffa" || m.game.match.Bots != 3 {
		t.Fatalf("esc restores the match: %+v", m.game.match)
	}
}

func TestMapThatNoLongerFitsIsReplaced(t *testing.T) {
	m := onMatch(t)
	m.game.match.Map = "schloss"
	stale := m.game.match
	stale.Bots = 7
	m.Update(mapsMsg{list: &setup.MapList{Maps: []string{"dm6"}, Default: "dm6"}, match: stale})
	if m.game.match.Map != "schloss" {
		t.Fatal("a list for another match must be ignored")
	}
	m.Update(mapsMsg{list: &setup.MapList{Maps: []string{"dm6", "dm4"}, Default: "dm6", Narrowed: true}, match: m.game.match})
	if m.game.match.Map != "dm6" || m.game.mapNote == "" {
		t.Fatalf("map swapped with a notice: %q · %q", m.game.match.Map, m.game.mapNote)
	}
}

// TestMatchDrawsBeforeItsCatalog is the x86qw play path: home's catalog
// arrives first and the match screen is drawn before its own does.
func TestMatchDrawsBeforeItsCatalog(t *testing.T) {
	m := New(Config{Backend: &setup.Demo{ExistingInstall: true}, Target: t.TempDir(), Lang: PT, Start: "match"})
	m.Update(tea.WindowSizeMsg{Width: 100, Height: 34})
	catalog, _ := (&setup.Demo{}).Catalog(context.Background())
	existing, _ := (&setup.Demo{ExistingInstall: true}).Existing(context.Background(), t.TempDir())
	m.Update(catalogMsg{catalog, existing, nil})
	m.View()
	m.click("row:play")
	drive(m, key("enter"), key("p"))
}

// listed is the demo with servers of its own on the list.
type listed struct {
	*setup.Demo
	servers []setup.PublicServer
}

func (l listed) Servers(context.Context, string) ([]setup.PublicServer, error) { return l.servers, nil }

// A Hub where nobody plays says so. Favorites with nobody on them are what
// stands in for a Hub that cannot be reached, and the lead says that.
func TestServersLeadTellsAQuietHubFromAnUnreachableOne(t *testing.T) {
	for want, servers := range map[string][]setup.PublicServer{
		"Nobody is playing":      nil,
		"The Hub is unreachable": {{Address: "fav.example:27500", Title: "fav.example:27500", Map: "-", Mode: "-", Favorite: true}},
		"1 servers with players": {{Address: "a.example:27500", Title: "A", Map: "dm6", Mode: "duel", Humans: 2}},
	} {
		m := New(Config{Backend: listed{&setup.Demo{ExistingInstall: true, Speed: 200}, servers}, Target: shortTarget(t), Lang: EN, Start: "servers"})
		drive(m, tea.WindowSizeMsg{Width: 100, Height: 34})
		drive(m, run(m.Init())...)
		if view := stripANSI(m.View().Content); !strings.Contains(view, want) {
			t.Errorf("%q missing:\n%s", want, view)
		}
	}
}
