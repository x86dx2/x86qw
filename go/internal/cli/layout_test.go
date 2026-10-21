package cli

import (
	"os"
	"path/filepath"
	"runtime"
	"slices"
	"testing"
)

// legacyRoot is an installation in the layout before 2.5: the binary in
// .x86qw/cli and both launchers.
func legacyRoot(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	for _, path := range legacyPaths(root) {
		os.MkdirAll(filepath.Dir(path), 0o755)
		if err := os.WriteFile(path, []byte("old"), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	return root
}

func TestInstallPutsTheCLIAtTheRootAndRetireRemovesTheOldLayout(t *testing.T) {
	root := legacyRoot(t)
	source := filepath.Join(t.TempDir(), "x86qw-new")
	os.WriteFile(source, []byte("new"), 0o755)
	if _, err := Install(root, source, "2.5.0"); err != nil {
		t.Fatal(err)
	}
	if payload, _ := os.ReadFile(Command(root)); string(payload) != "new" || InstalledVersion(root) != "2.5.0" {
		t.Fatalf("CLI %q, receipt %q", payload, InstalledVersion(root))
	}
	if !Legacy(root) {
		t.Fatal("Install removed the old layout before anything was repointed")
	}
	if err := Retire(root); err != nil {
		t.Fatal(err)
	}
	if Legacy(root) {
		t.Fatal("the old layout is still there")
	}
	if _, err := os.Stat(Command(root)); err != nil {
		t.Fatal("Retire removed the new CLI")
	}
}

func TestTheOldCommandLinkIsOursAndGetsRepointed(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("~/.local/bin is POSIX")
	}
	root, home := legacyRoot(t), t.TempDir()
	os.WriteFile(Command(root), []byte("new"), 0o755)
	command := filepath.Join(home, ".local", "bin", "x86qw")
	os.MkdirAll(filepath.Dir(command), 0o755)
	if err := os.Symlink(filepath.Join(root, "x86qw.sh"), command); err != nil {
		t.Fatal(err)
	}
	if !PointsAt(command, root) || !slices.Contains(StillLegacy(root, home), command) {
		t.Fatal("the link to x86qw.sh is not recognized as this installation's")
	}
	if _, err := LinkUserCommand(root, home); err != nil {
		t.Fatal(err)
	}
	if !samePath(LinkTarget(command), Command(root)) || len(StillLegacy(root, home)) > 0 {
		t.Fatalf("link points at %s", LinkTarget(command))
	}
	// The link outlives the files it pointed at during uninstall.
	os.Remove(Command(root))
	if !PointsAt(command, root) {
		t.Fatal("a link to a removed CLI is no longer recognized")
	}
	if err := UnlinkUserCommand(root, home); err != nil || exists(command) {
		t.Fatal("the link stayed")
	}
}

func TestEntriesForEitherLauncherAreOurs(t *testing.T) {
	root := t.TempDir()
	for _, entry := range []string{
		desktopEntry(root),
		"[Desktop Entry]\nExec=" + execArgument(filepath.Join(root, "x86qw.sh")) + " play\n",
		"[Desktop Entry]\nExec=\"" + filepath.Join(root, "x86qw.sh") + "\" play\n",
	} {
		if !launches(entry, root) {
			t.Fatalf("not ours:\n%s", entry)
		}
	}
	if launches(desktopEntry(t.TempDir()), root) {
		t.Fatal("another installation's entry is ours")
	}
}

func exists(path string) bool {
	_, err := os.Lstat(path)
	return err == nil
}

// An aside still in use (an app left open across two updates) is never
// overwritten: the next free name is taken.
func TestFreeAsideSkipsOneInUse(t *testing.T) {
	aside := filepath.Join(t.TempDir(), "x86qw.exe.old")
	if got := freeAside(aside); got != aside {
		t.Fatalf("free slot: %s", got)
	}
	// A folder with something in it cannot be removed, as a running
	// executable cannot on Windows.
	os.MkdirAll(filepath.Join(aside, "busy"), 0o755)
	if got := freeAside(aside); got != aside+".1" {
		t.Fatalf("busy slot: %s", got)
	}
}
