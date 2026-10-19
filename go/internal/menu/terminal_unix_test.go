//go:build !windows

package menu

import "testing"

func TestDecodeEscape(t *testing.T) {
	for sequence, want := range map[string]string{"[A": "up", "[B": "down", "OC": "right", "[5~": "pageup", "[1;5~": "home", "[Z": "unknown"} {
		if got := decodeEscape([]byte(sequence)); got != want {
			t.Fatalf("%q: %s", sequence, got)
		}
	}
}
