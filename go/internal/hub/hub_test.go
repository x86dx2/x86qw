package hub

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/x86dx2/x86qw/go/internal/maint"
	"github.com/x86dx2/x86qw/go/internal/play"
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

// Every server the list shows can be joined, and its stream watched, through
// x86qw open; an address it cannot reach is left out.
func TestListedServersCanBeJoined(t *testing.T) {
	players := []any{map[string]any{"is_bot": false}}
	servers, err := Project([]any{
		map[string]any{"address": "[2001:db8::1]:27500", "players": players},
		map[string]any{"address": "qw_eu.example.org:27500", "players": players, "qtv_stream": "1234567@qtv.example.org:28000"},
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(servers) != 1 || servers[0].Address != "qw_eu.example.org:27500" || servers[0].QTVStream == nil {
		t.Fatalf("servers: %+v", servers)
	}
	if _, err := play.OpenArguments(t.TempDir(), "qw://"+servers[0].Address); err != nil {
		t.Fatal(err)
	}
	if _, err := play.OpenArguments(t.TempDir(), "qw://"+*servers[0].QTVStream+"/qtvplay"); err != nil {
		t.Fatal(err)
	}
}

// hubAnswer is the Hub's reply to every request; empty, no connection.
type hubAnswer string

func (h hubAnswer) RoundTrip(request *http.Request) (*http.Response, error) {
	if h == "" {
		return nil, errors.New("no connection")
	}
	return &http.Response{StatusCode: http.StatusOK, Body: io.NopCloser(strings.NewReader(string(h))), Header: http.Header{}, Request: request}, nil
}

// A Hub where nobody plays lists no server; the favorites and recents
// stand in only for a Hub that cannot be reached.
func TestTheLibraryStandsInOnlyForAnUnreachableHub(t *testing.T) {
	saved := http.DefaultTransport
	t.Cleanup(func() { http.DefaultTransport = saved })
	root := t.TempDir()
	if err := maint.AddFavorite(root, "fav.example:27500", time.Now()); err != nil {
		t.Fatal(err)
	}
	http.DefaultTransport = hubAnswer(`[{"server_type":"game","software_type":"mvdsv","address":"empty.example:27500","players":[]}]`)
	if servers, err := Servers(context.Background(), root); err != nil || servers == nil || len(servers) != 0 {
		t.Fatalf("quiet Hub: %+v %v", servers, err)
	}
	http.DefaultTransport = hubAnswer("")
	if servers, err := Servers(context.Background(), root); err != nil || len(servers) != 1 || servers[0].Address != "fav.example:27500" {
		t.Fatalf("unreachable Hub: %+v %v", servers, err)
	}
	if _, err := Servers(context.Background(), t.TempDir()); err == nil {
		t.Fatal("an unreachable Hub without favorites or recents must say so")
	}
}
