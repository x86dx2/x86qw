package setup

import "testing"

func TestLastMatchRoundTripsAndExpires(t *testing.T) {
	target := t.TempDir()
	if last, err := LoadLastMatch(target); last != nil || err != nil {
		t.Fatalf("sem partida: %+v %v", last, err)
	}
	match := Match{Game: "ktx", Mode: "duel", Map: "dm6", Bots: 1, BotSkill: "8"}
	if err := SaveLastMatch(target, match); err != nil {
		t.Fatal(err)
	}
	last, err := LoadLastMatch(target)
	if err != nil || last == nil || *last != match {
		t.Fatalf("%+v %v", last, err)
	}
	catalog := &MatchCatalog{Games: []Game{{ID: "ktx", Modes: true}}, Modes: []Mode{{ID: "duel"}}}
	if !catalog.valid(match) {
		t.Fatal("a partida ainda serve")
	}
	for _, gone := range []Match{{Game: "pro-x"}, {Game: "ktx", Mode: "removido"}} {
		if catalog.valid(gone) {
			t.Fatalf("%+v não serve mais", gone)
		}
	}
}
