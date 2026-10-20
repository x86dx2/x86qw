package cli

import (
	"os"
	"testing"
)

func TestDesktopEntryBelongsToItsInstallation(t *testing.T) {
	home, root, other := t.TempDir(), t.TempDir(), t.TempDir()
	if err := InstallDesktopEntry(root, home); err != nil {
		t.Fatal(err)
	}
	if err := InstallDesktopEntry(root, home); err != nil {
		t.Fatalf("reescrever a própria entrada: %v", err)
	}
	if err := InstallDesktopEntry(other, home); err == nil {
		t.Fatal("a entrada de outra instalação foi substituída")
	}
	if err := RemoveDesktopEntry(other, home); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(DesktopEntryPath(home)); err != nil {
		t.Fatal("outra instalação removeu a entrada")
	}
	if err := RemoveDesktopEntry(root, home); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(DesktopEntryPath(home)); !os.IsNotExist(err) {
		t.Fatal("a entrada deveria ter saído")
	}
}
