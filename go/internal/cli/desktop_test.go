package cli

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestDesktopEntryBelongsToItsInstallation(t *testing.T) {
	home, root, other := t.TempDir(), t.TempDir(), t.TempDir()
	if err := InstallDesktopEntry(root, home); err != nil {
		t.Fatal(err)
	}
	if err := InstallDesktopEntry(root, home); err != nil {
		t.Fatalf("rewriting its own entry: %v", err)
	}
	if err := InstallDesktopEntry(other, home); err == nil {
		t.Fatal("another installation's entry was replaced")
	}
	if err := RemoveDesktopEntry(other, home); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(DesktopEntryPath(home)); err != nil {
		t.Fatal("another installation removed the entry")
	}
	if err := RemoveDesktopEntry(root, home); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(DesktopEntryPath(home)); !os.IsNotExist(err) {
		t.Fatal("the entry should be gone")
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
			t.Errorf("%s: %s, want %s", value, got, want)
		}
	}
	root := t.TempDir()
	if !launches("[Desktop Entry]\nExec=\""+filepath.Join(root, "x86qw.sh")+"\" play\n", root) {
		t.Fatal("an old quoted entry is no longer recognized")
	}
	if !launches(desktopEntry(root), root) || launches(desktopEntry(root+"2"), root) {
		t.Fatal("the entry does not identify its own installation")
	}
}

func TestEntriesSayEnglishFirst(t *testing.T) {
	for _, entry := range []string{desktopEntry("/opt/qw"), handlerEntry("/opt/qw")} {
		if !strings.Contains(entry, "\nComment=") || !strings.Contains(entry, "\nComment[pt_BR]=") {
			t.Fatalf("entry:\n%s", entry)
		}
	}
}
