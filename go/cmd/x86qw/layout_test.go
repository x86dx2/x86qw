package main

import (
	"bytes"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/x86dx2/x86qw/go/internal/cli"
)

func TestLayoutRootFromEitherBinary(t *testing.T) {
	root := t.TempDir()
	writeFile(t, filepath.Join(root, ".x86qw", "state.json"), `{"format":2}`)
	for _, executable := range []string{cli.Command(root), filepath.Join(root, ".x86qw", "cli", cli.BinaryName())} {
		if got, ok := layoutRoot(executable); !ok || got != root {
			t.Errorf("%s: %q %v", executable, got, ok)
		}
	}
	if _, ok := layoutRoot(filepath.Join(t.TempDir(), cli.BinaryName())); ok {
		t.Error("a binary outside any installation has one")
	}
}

// The old launchers go only once nothing of this installation uses them.
func TestRetireLegacyWaitsForTheLink(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("~/.local/bin is POSIX")
	}
	home, root := t.TempDir(), t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("XDG_DATA_HOME", filepath.Join(home, ".local", "share"))
	for _, path := range []string{cli.Command(root), filepath.Join(root, "x86qw.sh"), filepath.Join(root, ".x86qw", "cli", "x86qw")} {
		writeFile(t, path, "#!/bin/sh\n")
	}
	command := filepath.Join(home, ".local", "bin", "x86qw")
	os.MkdirAll(filepath.Dir(command), 0o755)
	os.Symlink(filepath.Join(root, "x86qw.sh"), command)
	var out bytes.Buffer
	retireLegacy(root, &out)
	if !cli.Legacy(root) || !strings.Contains(out.String(), command) {
		t.Fatalf("retired while the link still used x86qw.sh: %s", out.String())
	}
	if _, err := cli.LinkUserCommand(root, home); err != nil {
		t.Fatal(err)
	}
	out.Reset()
	retireLegacy(root, &out)
	if cli.Legacy(root) || out.Len() > 0 {
		t.Fatalf("the old layout stayed: %s", out.String())
	}
	if _, err := os.Stat(cli.Command(root)); err != nil {
		t.Fatal("the CLI went with the old layout")
	}
}
