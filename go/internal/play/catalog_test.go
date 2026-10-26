package play

import (
	"os"
	"path/filepath"
	"strings"
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
			t.Fatalf("embedded copy differs from %s", path)
		}
	}
	modes, err := LoadModes()
	if err != nil || len(modes) != 24 {
		t.Fatalf("modes: %d %v", len(modes), err)
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
		t.Fatal("duel with 2 bots accepted")
	}
	if _, err := LaunchCommands(duel, "dm6", map[string]bool{}, DefaultOptions()); err != nil {
		t.Fatal(err)
	}
	o.Bots = 1
	if _, err := LaunchCommands(duel, "dm6", map[string]bool{}, o); err == nil {
		t.Fatal("bots without a Frogbot route accepted")
	}
}

// ezQuake runs each +command of its command line through a 1024-byte line:
// whatever the ruleset and the mode, no command is longer, or its end is lost.
func TestLaunchCommandsFitTheConsoleLine(t *testing.T) {
	games, err := LoadGames()
	if err != nil {
		t.Fatal(err)
	}
	var ktx Game
	for _, game := range games {
		if game.ID == "ktx" {
			ktx = game
		}
	}
	modes, _ := LoadModes()
	for _, ruleset := range RulesetKeys {
		for _, mode := range modes {
			assets := map[string]bool{}
			for _, requirement := range mode.Requirements {
				assets[strings.ToLower(strings.ReplaceAll(requirement.Asset, "{map}", "dm6"))] = true
			}
			launch, err := Build(ktx, &mode, "dm6", assets, DefaultOptions(), ruleset)
			if err != nil {
				t.Fatalf("%s %s: %v", ruleset, mode.Key, err)
			}
			// Each command runs from its + argument to the next.
			var lines []string
			for _, arg := range launch.Arguments {
				if strings.HasPrefix(arg, "+") {
					lines = append(lines, arg[1:])
				} else if len(lines) > 0 {
					lines[len(lines)-1] += " " + arg
				}
			}
			// The runtime cfg goes through the same line.
			for _, line := range append(lines, launch.StartupCommands...) {
				if len(line) > 1023 {
					t.Errorf("%s %s: a %d-byte command", ruleset, mode.Key, len(line))
				}
			}
		}
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
			t.Fatalf("definition too large: %d", len(definition))
		}
	}
	if len(invocation) > 700 {
		t.Fatalf("invocation too large: %d", len(invocation))
	}
}
