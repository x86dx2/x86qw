package play

import (
	"os"
	"path/filepath"
	"slices"
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

// Frogbots join one at a time, frames apart; KTX picks their team unless
// --bot-team names one.
func TestFrogbotsJoinOnTheirTeam(t *testing.T) {
	modes, _ := LoadModes()
	waits := strings.Repeat("wait | ", frogbotAddWaitFrames)
	blue := "blue"
	for _, c := range []struct {
		mode    string
		options Options
		want    string
	}{
		{"2on2", Options{Bots: 3, BotSkill: "5"}, "if ($k_maxclients < 4) then k_maxclients 4 | if ($maxclients < 4) then maxclients 4 | " +
			"team red | cmd botcmd skill 5 | cmd botcmd addbot 5 | " + waits + "cmd botcmd addbot 5 | " + waits + "cmd botcmd addbot 5"},
		{"2on2", Options{Bots: 2, BotSkill: "7", BotTeam: &blue}, "if ($k_maxclients < 3) then k_maxclients 3 | if ($maxclients < 3) then maxclients 3 | " +
			"cmd botcmd skill 7 | cmd botcmd addbot 7 blue | " + waits + "cmd botcmd addbot 7 blue"},
		{"2on2on2", Options{FillBots: true, BotSkill: "5"}, "if ($k_maxclients < 6) then k_maxclients 6 | if ($maxclients < 6) then maxclients 6 | " +
			"team red | cmd botcmd skill 5 | " + strings.Repeat("cmd botcmd addbot 5 | "+waits, 4) + "cmd botcmd addbot 5"},
		{"ffa", Options{Bots: 2, BotSkill: "5", BotTeam: &blue}, "if ($k_maxclients < 3) then k_maxclients 3 | if ($maxclients < 3) then maxclients 3 | " +
			"cmd botcmd skill 5 | cmd botcmd addbot 5 blue | " + waits + "cmd botcmd addbot 5 blue"},
	} {
		mode, _ := FindMode(modes, c.mode)
		commands, err := LaunchCommands(mode, "dm6", map[string]bool{"bots/maps/dm6.bot": true}, c.options)
		if got := strings.Join(commands, " | "); err != nil || got != c.want {
			t.Errorf("%s: %v\n got %s\nwant %s", c.mode, err, got, c.want)
		}
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

// A server hosted on this machine holds UDP 27500, ezQuake's default: the
// local match's server takes any free port, or it never comes up.
func TestTheLocalMatchTakesAnyFreePort(t *testing.T) {
	games, err := LoadGames()
	if err != nil {
		t.Fatal(err)
	}
	modes, _ := LoadModes()
	for _, game := range games {
		var mode *Mode
		if game.UsesModeCatalog() {
			mode = &modes[0]
		}
		launch, err := Build(game, mode, "dm6", map[string]bool{}, DefaultOptions(), defaultRuleset)
		if err != nil {
			t.Fatalf("%s: %v", game.ID, err)
		}
		if !slices.Equal(launch.Arguments[:2], []string{"-port", "0"}) {
			t.Errorf("%s: %v", game.ID, launch.Arguments)
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

// The map asset labels players read come straight from modes.json.
func TestRequirementLabelsAreEnglish(t *testing.T) {
	modes, err := LoadModes()
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]string{"frogbot-route": "Frogbot route", "ctf-entities": "CTF flag entities", "race-route": "Race route"}
	seen := map[string]bool{}
	for _, mode := range modes {
		for _, requirement := range mode.Requirements {
			if requirement.Label != want[requirement.Key] {
				t.Errorf("%s: %s is labelled %q", mode.Key, requirement.Key, requirement.Label)
			}
			seen[requirement.Key] = true
		}
	}
	if len(seen) != len(want) {
		t.Fatalf("requirements seen: %v", seen)
	}
}
