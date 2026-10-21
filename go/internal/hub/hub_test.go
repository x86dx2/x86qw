package hub

import (
	"encoding/json"
	"testing"
)

func TestProjectsClosedShape(t *testing.T) {
	var rows []any
	json.Unmarshal([]byte(`[
	 {"address":"b.example:28501","settings":{"hostname":"  B\u0001 ","map":"dm6"},"mode":"duel",
	  "players":[{"name":"x"},{"name":"bot","is_bot":true}],"qtv_stream":{"url":"2@b.example:28000"}},
	 {"address":"a.example:28501","title":"A","players":{"humans":3,"bots":1},"extra":{"secret":1}},
	 {"address":"a.example:28501","title":"dup"},
	 {"address":"bad address"},
	 {"address":"c.example:70000"}
	]`), &rows)
	servers, err := Project(rows)
	if err != nil {
		t.Fatal(err)
	}
	got, _ := json.Marshal(servers)
	want := `[{"address":"a.example:28501","map":"-","mode":"-","players":{"bots":1,"humans":3},"qtv_stream":null,"title":"A"},` +
		`{"address":"b.example:28501","map":"dm6","mode":"duel","players":{"bots":1,"humans":1},"qtv_stream":"2@b.example:28000","title":"B"}]`
	if string(got) != want {
		t.Fatalf("got %s", got)
	}
}

func TestEmptyIsError(t *testing.T) {
	if _, err := Project(nil); err == nil {
		t.Fatal("empty list accepted")
	}
}

// The Hub's list since 2026: every server, "info" for the settings,
// is_bot null for people, player_mode for the mode.
func TestActiveKeepsBusyGameServers(t *testing.T) {
	var rows []any
	json.Unmarshal([]byte(`[
	 {"server_type":"game","software_type":"mvdsv","address":"empty.example:27500","info":{"hostname":"Empty","map":"dm3"},"players":[]},
	 {"server_type":"proxy","software_type":"qwfwd","address":"proxy.example:30000","players":[{"name":{"ascii":"p"},"is_bot":null}]},
	 {"server_type":"game","software_type":"fte","address":"fte.example:27500","players":[{"name":{"ascii":"f"},"is_bot":null}]},
	 {"server_type":"game","software_type":"mvdsv","address":"one.example:27500","player_mode":"duel","info":{"hostname":"One","map":"dm6","mode":"x"},
	  "players":[{"name":{"ascii":"a"},"is_bot":null}]},
	 {"server_type":"game","software_type":"mvdsv","address":"busy.example:27500","player_mode":"4on4","info":{"hostname":" Busy ","map":"e1m2"},
	  "players":[{"name":{"ascii":"a"},"is_bot":null},{"name":{"ascii":"b"},"is_bot":false},{"name":{"ascii":"c"},"is_bot":true}],
	  "qtv_stream":{"id":1,"address":"busy.example:28000","url":"1@busy.example:28000"}}
	]`), &rows)
	servers, err := Project(Active(rows))
	if err != nil {
		t.Fatal(err)
	}
	got, _ := json.Marshal(servers)
	want := `[{"address":"busy.example:27500","map":"e1m2","mode":"4on4","players":{"bots":1,"humans":2},"qtv_stream":"1@busy.example:28000","title":"Busy"},` +
		`{"address":"one.example:27500","map":"dm6","mode":"duel","players":{"bots":0,"humans":1},"qtv_stream":null,"title":"One"}]`
	if string(got) != want {
		t.Fatalf("got %s", got)
	}
	if len(Active(rows[:1])) != 0 {
		t.Fatal("an empty server counted as active")
	}
}

func TestReadableTurnsTheQuakeWorldCharsetIntoText(t *testing.T) {
	for raw, want := range map[string]string{
		"FFA @ qw.servegame.org\u0087":     "FFA @ qw.servegame.org",
		"\u0010Ñ×\u0011 \u0092\u0093":      "[QW] 01",
		"\u00c8\u00e5\u00ec\u00ec\u00ef ✓": "Hello ✓",
	} {
		if got := readable(raw); got != want {
			t.Errorf("%q: %q, want %q", raw, got, want)
		}
	}
}
