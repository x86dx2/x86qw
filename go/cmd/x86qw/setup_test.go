package main

import (
	"bytes"
	"path/filepath"
	"strings"
	"testing"

	"github.com/x86dx2/x86qw/go/internal/play"
)

func TestSetupRecipeAsksForYesAndRejectsBadFlags(t *testing.T) {
	target := filepath.Join(t.TempDir(), "x86qw")
	var stdout, stderr bytes.Buffer
	code := run([]string{"setup", "--intent", "play", "--nick", "x86dx2", "--target", target}, &stdout, &stderr)
	if code != 2 || !strings.Contains(stderr.String(), "Confirme com: x86qw setup --intent play --nick x86dx2") {
		t.Fatalf("sem --yes: %d %q", code, stderr.String())
	}
	stderr.Reset()
	code = run([]string{"setup", "--intent", "play", "--nick", "x86dx2", "--keys", "q,w", "--target", target, "--yes"}, &stdout, &stderr)
	if code != 2 || !strings.Contains(stderr.String(), "--keys") {
		t.Fatalf("teclas inválidas: %d %q", code, stderr.String())
	}
}

func TestPlayAcceptsTheTargetTheInstallerHandsOver(t *testing.T) {
	games, err := play.LoadGames()
	if err != nil {
		t.Fatal(err)
	}
	request, err := parsePlay([]string{"--target", "/games/x86qw"}, games)
	if err != nil || request.target != "/games/x86qw" {
		t.Fatalf("%+v %v", request, err)
	}
}
