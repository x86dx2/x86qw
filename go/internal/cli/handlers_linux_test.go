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
		t.Fatalf("entry:\n%s", entry)
	}
	if err := SetHandlers(t.TempDir(), true, false); err == nil {
		t.Fatal("another installation replaced the entry")
	}
	if err := SetHandlers(root, false, false); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{handlerEntryPath(home), demoMIMEPath(home)} {
		if _, err := os.Stat(path); !os.IsNotExist(err) {
			t.Fatalf("%s should be gone", path)
		}
	}
}

func TestUpdatesRewriteEntriesFromEarlierVersions(t *testing.T) {
	home, root, other := t.TempDir(), t.TempDir(), t.TempDir()
	launcher := filepath.Join(root, "x86qw.sh")
	legacy := map[string]string{
		DesktopEntryPath(home): "[Desktop Entry]\nExec=\"" + launcher + "\" play\n",
		handlerEntryPath(home): "[Desktop Entry]\nExec=\"" + launcher + "\" open --target \"" + root + "\" %u\n",
	}
	os.MkdirAll(filepath.Dir(DesktopEntryPath(home)), 0o755)
	for path, payload := range legacy {
		os.WriteFile(path, []byte(payload), 0o644)
	}
	RefreshEntries(other, home)
	for path, payload := range legacy {
		if got, _ := os.ReadFile(path); string(got) != payload {
			t.Fatalf("another installation rewrote %s", path)
		}
	}
	RefreshEntries(root, home)
	for path := range legacy {
		if got, _ := os.ReadFile(path); !strings.Contains(string(got), "Exec="+launcher+" ") {
			t.Fatalf("%s was not rewritten:\n%s", path, got)
		}
	}
	empty := t.TempDir()
	RefreshEntries(root, empty)
	if _, err := os.Stat(filepath.Dir(DesktopEntryPath(empty))); !os.IsNotExist(err) {
		t.Fatal("the update created entries that did not exist")
	}
}
