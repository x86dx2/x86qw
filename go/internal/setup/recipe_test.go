package setup

import (
	"reflect"
	"strings"
	"testing"
)

// A key that hides a command is refused: the console would run it. The keys
// that work today, names and a semicolon included, still pass.
func TestRecipeRefusesAKeyThatHidesACommand(t *testing.T) {
	if _, err := parseRecipe([]string{"--intent", "play", "--nick", "p", "--keys", "w;connect evil.example,s,a,d,space"}); err == nil {
		t.Fatal("a key with a command after it was accepted")
	}
	if _, err := parseRecipe([]string{"--intent", "play", "--nick", "p", "--keys", "j,k,l,;,mouse2"}); err != nil {
		t.Fatal(err)
	}
}

// A recipe that leaves fields out gets the defaults for them, not zeros: FOV
// 0, volume 0 and no server would install what nobody chose.
func TestRecipeFillsWhatItLeavesOutWithTheDefaults(t *testing.T) {
	o, err := ReadRecipe(strings.NewReader(`{"format": 1, "Intent": "play", "Nick": "p"}`))
	if err != nil {
		t.Fatal(err)
	}
	if o.Video.FOV != 110 || o.Volume != 70 || o.Mouse.Sensitivity != 2.6 || o.Keys.Forward != "w" || len(o.Server.Instances) != 2 {
		t.Fatalf("%+v", o)
	}
}

// What the install could not carry out is refused up front: a mode or a
// component that does not exist.
func TestRecipeRefusesWhatItCannotInstall(t *testing.T) {
	for _, args := range [][]string{
		{"--intent", "host", "--servers", "dual:dm6"},
		{"--intent", "play", "--nick", "p", "--content", "ktx,qrp-hries"},
	} {
		if _, err := parseRecipe(args); err == nil {
			t.Errorf("%v was accepted", args)
		}
	}
}

func TestRecipeRoundTrips(t *testing.T) {
	both := Defaults(Both, "/games/x86qw")
	both.Nick, both.Content, both.Components = "x86dx2", "custom", []string{"ktx", "nquake-maps"}
	both.Keys = Keys{Preset: "custom", Forward: "uparrow", Back: "downarrow", Left: "leftarrow", Right: "rightarrow", Jump: "mouse2"}
	both.Mouse.Invert, both.Experience, both.Volume = true, "qcon", 55
	both.Video = Video{Width: 2560, Height: 1440, Display: "borderless", FOV: 100}
	both.Server.Hostname, both.Server.BasePort, both.Server.QWFWDPort = "qw.example.com", 27510, 0
	both.Server.Local, both.Server.Slots, both.Server.NoRecord = true, 24, true
	both.Integration.Demos = false
	host := Defaults(Host, "/srv/qw")
	play := Defaults(Play, "/games/qw")
	play.Nick = "pla"
	for _, want := range []Options{both, host, play} {
		got, err := parseRecipe(want.Recipe()[2:])
		if err != nil {
			t.Fatalf("%v: %v", want.Recipe(), err)
		}
		// The recipe never carries the password nor the DPI.
		got.Server.Rcon, want.Server.Rcon = "", ""
		got.Mouse.DPI = want.Mouse.DPI
		if want.Intent == Host {
			got.Nick = want.Nick
		}
		if !reflect.DeepEqual(got, want) {
			t.Fatalf("the recipe does not come back the same:\n%+v\n%+v", got, want)
		}
	}
}

// The game sends names as bytes of the Quake charset, which has no accents:
// "Nélson" would reach the other players garbled, so it is refused with a
// reason; passwords, which only have to match, keep any character.
func TestNamesTakePlainLettersOnly(t *testing.T) {
	both := Defaults(Both, "/games/x86qw")
	both.Nick = "Nelson"
	if err := both.Validate(); err != nil {
		t.Fatal(err)
	}
	for _, change := range []func(*Options){
		func(o *Options) { o.Nick = "Nélson" },
		func(o *Options) { o.Server.Name = "Servidor São Paulo" },
	} {
		o := both
		change(&o)
		if err := o.Validate(); err == nil {
			t.Errorf("accepted: nick %q, server %q", o.Nick, o.Server.Name)
		}
	}
}

// Bots go only where the mode has them, and a mode named by an alias is kept
// under its own name, as the app shows it.
func TestRecipeServersFollowTheModeCatalog(t *testing.T) {
	if _, err := parseRecipe([]string{"--intent", "host", "--servers", "ctf:e2m2+bots"}); err == nil {
		t.Fatal("bots on a mode without them were accepted")
	}
	o, err := parseRecipe([]string{"--intent", "host", "--servers", "1on1:dm6"})
	if err != nil || o.Server.Instances[0].Mode != "duel" {
		t.Fatalf("%+v %v", o.Server.Instances, err)
	}
}
