package cli

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestLinuxHandlersComeAndGo(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(home, ".config"))
	t.Setenv("XDG_DATA_HOME", filepath.Join(home, ".local", "share"))
	root := t.TempDir()
	err := SetHandlers(root, true, true)
	if err != nil && !strings.Contains(err.Error(), "xdg-mime") {
		t.Fatal(err)
	}
	entry, _ := os.ReadFile(handlerEntryPath(home))
	if !strings.Contains(string(entry), "x-scheme-handler/qw") || !strings.Contains(string(entry), "Exec="+root+"/x86qw.sh open --target "+root+" %u") {
		t.Fatalf("entrada:\n%s", entry)
	}
	if err := SetHandlers(t.TempDir(), true, false); err == nil {
		t.Fatal("outra instalação substituiu a entrada")
	}
	if err := SetHandlers(root, false, false); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{handlerEntryPath(home), demoMIMEPath(home)} {
		if _, err := os.Stat(path); !os.IsNotExist(err) {
			t.Fatalf("%s deveria ter saído", path)
		}
	}
}
