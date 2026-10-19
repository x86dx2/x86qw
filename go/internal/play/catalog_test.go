package play

import (
	"os"
	"path/filepath"
	"testing"
)

func TestEmbeddedCatalogsMatchSources(t *testing.T) {
	sources := map[string][]byte{
		"dist/mods/ktx/1.47/x86qw/catalog/modes.json":                    KtxModesJSON,
		"dist/mods/ktx/1.47/x86qw/catalog/frogbots/names.json":           namesX86QW,
		"dist/mods/ktx/1.47/x86qw/catalog/frogbots/names.one-piece.json": namesOnePiece,
	}
	for path, embedded := range sources {
		want, err := os.ReadFile(filepath.Join("..", "..", "..", filepath.FromSlash(path)))
		if err != nil {
			t.Fatal(err)
		}
		if string(want) != string(embedded) {
			t.Fatalf("cópia embutida diverge de %s", path)
		}
	}
	modes, err := LoadModes()
	if err != nil || len(modes) != 24 {
		t.Fatalf("modos: %d %v", len(modes), err)
	}
	for _, profile := range []struct {
		payload []byte
		name    string
	}{{namesX86QW, "x86qw"}, {namesOnePiece, "one-piece"}} {
		if names, err := parseNames(profile.payload, profile.name, profile.name); err != nil || len(names) == 0 {
			t.Fatalf("%s: %v", profile.name, err)
		}
	}
}

func TestPlanForDuelWithBots(t *testing.T) {
	modes, _ := LoadModes()
	duel, err := FindMode(modes, "1ON1")
	if err != nil || duel.Key != "duel" {
		t.Fatalf("alias: %v", err)
	}
	o := DefaultOptions()
	o.Bots = 1
	o.BotSkill = "8"
	commands, err := LaunchCommands(duel, "dm6", map[string]bool{"bots/maps/dm6.bot": true}, o)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"if ($k_maxclients < 2) then k_maxclients 2", "if ($maxclients < 2) then maxclients 2", "cmd botcmd skill 8", "cmd botcmd addbot 8"}
	if len(commands) != len(want) {
		t.Fatalf("%q", commands)
	}
	for index := range want {
		if commands[index] != want[index] {
			t.Fatalf("%q", commands)
		}
	}
	o.Bots = 2
	if _, err := LaunchCommands(duel, "dm6", map[string]bool{"bots/maps/dm6.bot": true}, o); err == nil {
		t.Fatal("duel com 2 bots aceito")
	}
	if _, err := LaunchCommands(duel, "dm6", map[string]bool{}, DefaultOptions()); err != nil {
		t.Fatal(err)
	}
	o.Bots = 1
	if _, err := LaunchCommands(duel, "dm6", map[string]bool{}, o); err == nil {
		t.Fatal("bots sem rota Frogbot aceitos")
	}
}

func TestChunkingKeepsBodiesBounded(t *testing.T) {
	var commands []string
	for index := 0; index < 400; index++ {
		commands = append(commands, "cmd botcmd addbot 5")
	}
	definitions, invocation, err := ChunkedSetupPlan(commands, 700)
	if err != nil {
		t.Fatal(err)
	}
	for _, definition := range definitions {
		if len(definition) > 700+64 {
			t.Fatalf("definição grande demais: %d", len(definition))
		}
	}
	if len(invocation) > 700 {
		t.Fatalf("invocação grande demais: %d", len(invocation))
	}
}
