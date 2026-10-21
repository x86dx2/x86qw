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
		`{"address":"b.example:28501","map":"dm6","mode":"duel","players":{"bots":1,"humans":1},"qtv_stream":"2@b.example:28000","title":"B?"}]`
	if string(got) != want {
		t.Fatalf("got %s", got)
	}
}

func TestEmptyIsError(t *testing.T) {
	if _, err := Project(nil); err == nil {
		t.Fatal("empty list accepted")
	}
}
