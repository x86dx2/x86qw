package main

import (
	"bytes"
	"errors"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/x86dx2/x86qw/go/internal/maint"
)

type noHub struct{}

func (noHub) RoundTrip(*http.Request) (*http.Response, error) {
	return nil, errors.New("no connection")
}

// With the Hub unreachable the list is the favorites and recents, and the
// command says so instead of passing them off as servers with players.
func TestHubSaysWhenItShowsTheFavorites(t *testing.T) {
	saved := http.DefaultTransport
	t.Cleanup(func() { http.DefaultTransport = saved })
	http.DefaultTransport = noHub{}
	root := t.TempDir()
	if err := maint.AddFavorite(root, "fav.example:27500", time.Now()); err != nil {
		t.Fatal(err)
	}
	var stdout, stderr bytes.Buffer
	if code := run([]string{"hub", "--target", root}, &stdout, &stderr); code != 0 {
		t.Fatalf("exit %d: %s", code, stderr.String())
	}
	if !strings.Contains(stdout.String(), "The Hub is unreachable") || !strings.Contains(stdout.String(), "fav.example:27500") {
		t.Fatalf("output:\n%s", stdout.String())
	}
}
