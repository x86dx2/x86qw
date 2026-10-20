package tui

import (
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
		t.Fatalf("partida: tela %d, %+v", m.screen, m.game.match)
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
	t.Fatalf("sem a linha %s", id)
	return formRow{}
}

func TestBotsFollowTheRoster(t *testing.T) {
	m := onMatch(t)
	m.game.match.Mode = "duel"
	optionRow(t, m, "bots").step(+1)
	if m.game.match.Bots != 1 {
		t.Fatalf("duel tem um adversário: %d", m.game.match.Bots)
	}
	optionRow(t, m, "bots").step(+1)
	if m.game.match.Bots != 0 {
		t.Fatalf("duel volta a sem bots: %d", m.game.match.Bots)
	}
	m.clearOptions()
	m.game.match.Mode = "ffa"
	optionRow(t, m, "bots").step(-1)
	if !m.game.match.FillBots {
		t.Fatal("antes de sem bots, um modo aberto preenche o servidor")
	}
	optionRow(t, m, "bots").step(+1)
	optionRow(t, m, "bots").step(+1)
	if m.game.match.FillBots || m.game.match.Bots != 1 {
		t.Fatalf("depois de sem bots vem 1: %+v", m.game.match)
	}
}

func TestRaceAndCTFAskTheirOwnRules(t *testing.T) {
	m := onMatch(t)
	m.game.match.Mode = "race"
	m.openModal("m.opts")
	for _, row := range m.gameModalRows() {
		if row.id == "race.scoring" {
			t.Fatal("pontuação só no match competitivo")
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
		t.Fatalf("modo novo, opções novas: %+v", m.game.match)
	}
	drive(m, key("esc"))
	if m.game.match.Mode != "ffa" || m.game.match.Bots != 3 {
		t.Fatalf("esc restaura a partida: %+v", m.game.match)
	}
}

func TestMapThatNoLongerFitsIsReplaced(t *testing.T) {
	m := onMatch(t)
	m.game.match.Map = "schloss"
	stale := m.game.match
	stale.Bots = 7
	m.Update(mapsMsg{list: &setup.MapList{Maps: []string{"dm6"}, Default: "dm6"}, match: stale})
	if m.game.match.Map != "schloss" {
		t.Fatal("uma lista de outra partida deve ser ignorada")
	}
	m.Update(mapsMsg{list: &setup.MapList{Maps: []string{"dm6", "dm4"}, Default: "dm6", Narrowed: true}, match: m.game.match})
	if m.game.match.Map != "dm6" || m.game.mapNote == "" {
		t.Fatalf("mapa trocado com aviso: %q · %q", m.game.match.Map, m.game.mapNote)
	}
}
