package setup

import (
	"reflect"
	"testing"
)

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
