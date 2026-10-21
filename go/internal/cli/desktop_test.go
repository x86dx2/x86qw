package cli

import (
	"os"
	"path/filepath"
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

func TestExecArgumentQuotesOnlyWhatNeedsIt(t *testing.T) {
	for value, want := range map[string]string{
		"/home/a/x86qw":    "/home/a/x86qw",
		"/home/a/100%":     "/home/a/100%%",
		"/home/a b/qw":     `"/home/a b/qw"`,
		`/home/a "b"/$c\d`: `"/home/a \\"b\\"/\\$c\\\\d"`,
	} {
		if got := execArgument(value); got != want {
			t.Errorf("%s: %s, queria %s", value, got, want)
		}
	}
	root := t.TempDir()
	if !launches("[Desktop Entry]\nExec=\""+filepath.Join(root, "x86qw.sh")+"\" play\n", root) {
		t.Fatal("uma entrada antiga, com aspas, deixou de ser reconhecida")
	}
	if !launches(desktopEntry(root), root) || launches(desktopEntry(root+"2"), root) {
		t.Fatal("a entrada não identifica a própria instalação")
	}
}
