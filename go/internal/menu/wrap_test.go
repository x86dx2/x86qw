package menu

import "testing"

func TestWrapKeepsIndentLikeTextwrap(t *testing.T) {
	got := wrap("  Jogo    | KTX", 88)
	if len(got) != 1 || got[0] != "  Jogo    | KTX" {
		t.Fatalf("%q", got)
	}
	got = wrap("aaa bbb   ccc", 7)
	if len(got) != 2 || got[0] != "aaa bbb" || got[1] != "ccc" {
		t.Fatalf("%q", got)
	}
	if got := wrap("   ", 10); len(got) != 1 || got[0] != "" {
		t.Fatalf("%q", got)
	}
}
