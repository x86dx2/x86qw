package services

import (
	"github.com/x86dx2/x86qw/go/internal/privfs"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/x86dx2/x86qw/go/internal/cli"
	"github.com/x86dx2/x86qw/go/internal/play"
)

func games(t *testing.T) []play.Game {
	t.Helper()
	loaded, err := play.LoadGames()
	if err != nil {
		t.Fatal(err)
	}
	return loaded
}

func TestParseHost(t *testing.T) {
	o, err := ParseHost([]string{"--mode", "ffa", "--map=dm6", "--bots", "3", "--bot-skill", "random", "--bind", "0.0.0.0",
		"--with-qtv", "--qtv-port", "28010", "--password", "x"}, games(t))
	if err != nil {
		t.Fatal(err)
	}
	if o.Game != "ktx" || o.Map != "dm6" || o.KtxOptions.Bots != 3 || o.KtxOptions.BotSkill != "random" ||
		o.Bind != "0.0.0.0" || !o.WithQTV || o.QtvPort != 28010 || o.Password != "x" || o.Port != 28501 {
		t.Fatalf("unexpected options: %+v", o)
	}
	for _, args := range [][]string{
		{"ktx", "--password", "a", "--prompt-password"},
		{"ktx", "--port", "80"},
		{"final-arena", "--mode", "duel"},
		{"ktx", "--bot-skill", "3"},
		{"nosuch"},
		{"ktx", "--bind", "host"},
	} {
		if _, err := ParseHost(args, games(t)); err == nil || !IsUsage(err) {
			t.Errorf("%v: want usage error, got %v", args, err)
		}
	}
}

func TestParseQtvAndProxy(t *testing.T) {
	q, err := ParseQtv([]string{"--upstream", "127.0.0.1:28501", "--password", "abc"})
	if err != nil || q.Upstream != "127.0.0.1:28501" || q.QtvPassword != "abc" || q.Hostname != "x86QW QTV" {
		t.Fatalf("qtv: %+v %v", q, err)
	}
	p, err := ParseProxy([]string{"--bind", "::1", "--port", "30005"})
	if err != nil || p.ProxyBind != "::1" || p.ProxyPort != 30005 {
		t.Fatalf("proxy: %+v %v", p, err)
	}
	if _, err := ParseProxy([]string{"extra"}); err == nil {
		t.Fatal("positional proxy argument accepted")
	}
}

func TestPresetRoundTrip(t *testing.T) {
	target := t.TempDir()
	os.MkdirAll(filepath.Join(target, "qw"), 0o755)
	o, err := ParseHost([]string{"ktx", "--mode", "ctf", "--map", "e1m3", "--with-qtv", "--target", target}, games(t))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := SavePreset(target, "ctf-lan", o); err != nil {
		t.Fatal(err)
	}
	payload, _ := os.ReadFile(filepath.Join(target, "qw", "x86qw-host-presets.json"))
	if !strings.HasPrefix(string(payload), `{"format": 1, "kind": "host-presets", "presets": [{"bind": "127.0.0.1"`) {
		t.Fatalf("preset file: %s", payload)
	}
	loaded, err := ParseHost([]string{"--preset", "ctf-lan", "--target", target}, games(t))
	if err != nil {
		t.Fatal(err)
	}
	if loaded.Game != "ktx" || loaded.Mode != "ctf" || loaded.Map != "e1m3" || !loaded.WithQTV {
		t.Fatalf("applied preset: %+v", loaded)
	}
	o.Password = "secret"
	if _, err := SavePreset(target, "with-secret", o); err == nil {
		t.Fatal("preset stored a password")
	}
}

func TestReadPasswordFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "pw")
	os.WriteFile(path, []byte("secret\n"), 0o600)
	if err := privfs.ProtectFile(path); err != nil {
		t.Fatal(err)
	}
	if value, err := ReadPasswordFile(path, "password"); err != nil || value != "secret" {
		t.Fatalf("%q %v", value, err)
	}
	exposeToOthers(t, path)
	if _, err := ReadPasswordFile(path, "password"); err == nil {
		t.Fatal("world-readable password file accepted")
	}
	os.WriteFile(path, []byte("a\nb\n"), 0o600)
	os.Chmod(path, 0o600)
	if _, err := ReadPasswordFile(path, "password"); err == nil {
		t.Fatal("multi-line password file accepted")
	}
}

func TestQtvResponseReady(t *testing.T) {
	if !qtvResponseReady([]byte("HTTP/1.0 200 OK\r\n\r\nbody"), "") {
		t.Fatal("200 not ready")
	}
	if qtvResponseReady([]byte("HTTP/1.0 404 Not Found\r\n\r\n"), "") {
		t.Fatal("404 ready")
	}
	if !qtvResponseReady([]byte("HTTP/1.0 200 OK\r\n\r\n<a>127.0.0.1:28501</a>"), "127.0.0.1:28501") {
		t.Fatal("upstream not found")
	}
	if qtvResponseReady([]byte("HTTP/1.0 200 OK\r\n\r\nnothing"), "127.0.0.1:28501") {
		t.Fatal("missing upstream accepted")
	}
}

func TestPreflightPortsRejectsDuplicates(t *testing.T) {
	err := PreflightPorts([]PortRequest{{"MVDSV", "127.0.0.1", 28501, "udp"}, {"QTV", "127.0.0.1", 28501, "tcp"}})
	if err == nil || !strings.Contains(err.Error(), "Duplicate local port") {
		t.Fatalf("got %v", err)
	}
}

// MVDSV reads " +" in its folder as the start of a command, as ezQuake does.
func TestHostRefusesAFolderWithAPlus(t *testing.T) {
	if _, err := HostSpec("/srv/My Games +quit/x86qw", &HostOptions{}, nil, nil, nil); err == nil || !strings.Contains(err.Error(), `" +"`) {
		t.Fatalf("%v", err)
	}
}

// A server name goes out in the Quake charset, which has no accents: like
// the installer, the host command refuses one instead of showing it garbled.
func TestServerNamesTakePlainLettersOnly(t *testing.T) {
	if _, err := SafeName("Servidor São Paulo", "hostname", 96); err == nil {
		t.Fatal("an accented name was accepted")
	}
	if _, err := SafeName("Servidor Sao Paulo", "hostname", 96); err != nil {
		t.Fatal(err)
	}
}

// A journal names files with forward slashes only: on Windows a backslash or
// a drive would lead out of the installation, so neither is accepted.
func TestJournalPathsStayInside(t *testing.T) {
	for _, relative := range []string{`..\..\x`, `qw\..\..\x`, "C:x", "../x", "/x"} {
		if _, ok := JournalPath("/srv/x86qw", relative); ok {
			t.Errorf("%q was accepted", relative)
		}
	}
	if _, ok := JournalPath("/srv/x86qw", "qw/xh_session.cfg"); !ok {
		t.Fatal("a plain path was refused")
	}
}

// A bootstrap runs x86QW from a copy it deletes on exit: the background
// controller runs the installed CLI when it is that same program, and never
// an installed CLI of another build, which may read its arguments otherwise.
func TestTheControllerRunsTheInstalledCLI(t *testing.T) {
	running, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	self, err := os.ReadFile(running)
	if err != nil {
		t.Fatal(err)
	}
	target := t.TempDir()
	installed := cli.Command(target)
	if got, _ := controllerExecutable(target); got != running {
		t.Fatalf("no installed CLI: %s", got)
	}
	if err := os.WriteFile(installed, self, 0o755); err != nil {
		t.Fatal(err)
	}
	if got, _ := controllerExecutable(target); got != installed {
		t.Fatalf("the same program installed: %s", got)
	}
	if err := os.WriteFile(installed, append(self, 0), 0o755); err != nil {
		t.Fatal(err)
	}
	if got, _ := controllerExecutable(target); got != running {
		t.Fatalf("another build installed: %s", got)
	}
}
