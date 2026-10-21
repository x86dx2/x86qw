package main

import (
	"bytes"
	"path/filepath"
	"strings"
	"testing"

	"github.com/x86dx2/x86qw/go/internal/play"
	"github.com/x86dx2/x86qw/go/internal/setup"
)

func TestSetupRecipeAsksForYesAndRejectsBadFlags(t *testing.T) {
	withoutTerminal(t)
	target := filepath.Join(t.TempDir(), "x86qw")
	var stdout, stderr bytes.Buffer
	code := run([]string{"setup", "--intent", "play", "--nick", "x86dx2", "--target", target}, &stdout, &stderr)
	if code != 2 || !strings.Contains(stderr.String(), "Run again with --yes: x86qw setup --intent play --nick x86dx2") {
		t.Fatalf("without --yes: %d %q", code, stderr.String())
	}
	stderr.Reset()
	code = run([]string{"setup", "--intent", "play", "--nick", "x86dx2", "--keys", "q,w", "--target", target, "--yes"}, &stdout, &stderr)
	if code != 2 || !strings.Contains(stderr.String(), "--keys") {
		t.Fatalf("invalid keys: %d %q", code, stderr.String())
	}
}

func TestPlayAcceptsTheTargetTheInstallerHandsOver(t *testing.T) {
	games, err := play.LoadGames()
	if err != nil {
		t.Fatal(err)
	}
	request, err := parsePlay([]string{"--target", "/games/x86qw"}, games)
	if err != nil || request.target != "/games/x86qw" {
		t.Fatalf("%+v %v", request, err)
	}
}

func TestMatchArgsParseAsPlayFlags(t *testing.T) {
	games, err := play.LoadGames()
	if err != nil {
		t.Fatal(err)
	}
	for _, m := range []setup.Match{
		{Game: "ktx", Mode: "tot", Map: "dm6", Ruleset: "qcon", Channel: "stable", Bots: 3, BotSkill: "8", BotName: "x86qw", BotWeapon: "random", BotHealth: 150, BotBreak: "off"},
		{Game: "ktx", Mode: "ffa", Map: "dm4", FillBots: true},
		{Game: "ktx", Mode: "ctf", Map: "e2m2", CTFHook: "fast", CTFRunes: "off", CTFBased: true},
		{Game: "ktx", Mode: "race", Map: "dm6", RaceStyle: "match", RaceScoring: "formula1", RacePacemaker: 3, RaceHide: true},
		{Game: "pro-x", Map: "dm2"},
	} {
		args := m.Args("/games/x86qw")
		request, err := parsePlay(args[1:], games)
		if err != nil {
			t.Fatalf("%v: %v", args, err)
		}
		if request.game != m.Game || request.mode != m.Mode || request.mapName != m.Map || request.options.Bots != m.Bots || request.options.FillBots != m.FillBots {
			t.Fatalf("%v → %+v", args, request)
		}
	}
}
