package play

import (
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestOpenAcceptsOnlyCleanAddresses(t *testing.T) {
	target := t.TempDir()
	for _, good := range []string{"qw://qw.x86.com.br:27500", "qw://10.0.0.5", "QW://Server-1.example.org:28000/observe", "qw://host/qtvplay"} {
		args, err := OpenArguments(target, good)
		if err != nil || args[0] != "+qwurl" || !strings.HasPrefix(args[1], "qw://") {
			t.Fatalf("%s: %v %v", good, args, err)
		}
	}
	for _, bad := range []string{"qw://host;quit", `qw://host"`, "qw://host:27500 +exec x", "qw://$rcon_password", "qw://host/../x", "qw://", "http://host", "qw://host:123456"} {
		if _, err := OpenArguments(target, bad); err == nil {
			t.Fatalf("%q deveria ser recusado", bad)
		}
	}
}

func TestOpenCopiesTheDemoUnderItsOwnName(t *testing.T) {
	target, elsewhere := t.TempDir(), t.TempDir()
	demo := filepath.Join(elsewhere, "final; quit $name.mvd")
	os.WriteFile(demo, []byte("demo bytes"), 0o644)
	slashed := filepath.ToSlash(demo)
	if !strings.HasPrefix(slashed, "/") {
		slashed = "/" + slashed // C:/… becomes file:///C:/…
	}
	for _, item := range []string{demo, "file://" + (&url.URL{Path: slashed}).EscapedPath()} {
		args, err := OpenArguments(target, item)
		if err != nil || args[0] != "+playdemo" || strings.ContainsAny(args[1], " ;$\"") {
			t.Fatalf("%s: %v %v", item, args, err)
		}
		if copied, _ := os.ReadFile(filepath.Join(target, "qw", filepath.FromSlash(args[1]))); string(copied) != "demo bytes" {
			t.Fatal("a demo não foi copiada para qw/demos")
		}
	}
	if _, err := OpenArguments(target, filepath.Join(elsewhere, "notes.txt")); err == nil {
		t.Fatal("um .txt não é demo")
	}
}
