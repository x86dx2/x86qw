package setup

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/x86dx2/x86qw/go/internal/play"
	"github.com/x86dx2/x86qw/go/internal/services"
)

func TestHostCommandParses(t *testing.T) {
	games, err := play.LoadGames()
	if err != nil {
		t.Fatal(err)
	}
	target := t.TempDir()
	options := Defaults(Host, target)
	if err := SaveServerPresets(target, options); err != nil {
		t.Fatal(err)
	}
	for index := range options.Server.Instances {
		args := HostCommand(target, options, index)
		if _, err := services.ParseHost(args[1:], games); err != nil {
			t.Fatalf("x86qw %s: %v", strings.Join(args, " "), err)
		}
	}
}

func TestClientConfigAppliesOnceAndKeepsUserLines(t *testing.T) {
	target := t.TempDir()
	os.MkdirAll(filepath.Join(target, "qw"), 0o755)
	os.WriteFile(filepath.Join(target, "qw", "x86qw-user.cfg"), []byte("bind F11 \"toggleconsole\""), 0o644)
	options := Defaults(Play, target)
	options.Nick, options.Keys = "x86dx2", KeyPresets["esdf"]
	options.Mouse.Invert, options.Video.Display = true, "borderless"
	for _, stamp := range []int64{100, 200} {
		if err := WriteClientConfig(target, options, stamp); err != nil {
			t.Fatal(err)
		}
	}
	user, _ := os.ReadFile(filepath.Join(target, "qw", "x86qw-user.cfg"))
	if !strings.HasPrefix(string(user), "bind F11 \"toggleconsole\"\n") || strings.Count(string(user), "x86qw setup: begin") != 1 ||
		!strings.Contains(string(user), `if $_x86qw_setup != 200 "exec x86qw-setup.cfg"`) {
		t.Fatalf("x86qw-user.cfg:\n%s", user)
	}
	cfg := ClientConfig(options, 200)
	for _, want := range []string{`name                     "x86dx2"`, `cl_fakename              "x86"`, `bind e                   "+forward"`,
		`m_pitch                  "-0.022"`, `vid_win_borderless       "1"`, `vid_fullscreen           "0"`, `volume                   "0.7"`, `set _x86qw_setup "200"`} {
		if !strings.Contains(cfg, want) {
			t.Fatalf("falta %q em:\n%s", want, cfg)
		}
	}
	ruleset, _ := os.ReadFile(filepath.Join(target, ".x86qw", "launcher", "client-ruleset.json"))
	if !strings.Contains(string(ruleset), `"ruleset": "x86qw"`) {
		t.Fatalf("ruleset: %s", ruleset)
	}
}

func TestChoicesRoundTripPrivately(t *testing.T) {
	target := t.TempDir()
	options := Defaults(Both, target)
	options.Nick = "x86dx2"
	if err := SaveChoices(target, options); err != nil {
		t.Fatal(err)
	}
	loaded, err := LoadChoices(target)
	if err != nil || loaded == nil || loaded.Nick != "x86dx2" || loaded.Server.Rcon != options.Server.Rcon {
		t.Fatalf("%+v %v", loaded, err)
	}
	for _, name := range []string{choicesFile, RconFile} {
		info, err := os.Stat(filepath.Join(target, filepath.FromSlash(name)))
		if err != nil || (info.Mode().Perm()&0o077 != 0 && os.PathSeparator == '/') {
			t.Fatalf("%s: %v %v", name, info.Mode(), err)
		}
	}
}
