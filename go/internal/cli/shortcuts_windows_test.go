//go:build windows

package cli

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/registry"
)

func TestShortcutsLifecycle(t *testing.T) {
	t.Setenv("APPDATA", t.TempDir())
	t.Setenv("USERPROFILE", t.TempDir())
	root := t.TempDir()
	os.MkdirAll(filepath.Join(root, filepath.FromSlash(cliDir)), 0o755)
	os.WriteFile(Command(root), []byte("MZ"), 0o755)
	shortcuts, err := ShortcutPaths()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := InstallShortcuts(root); err != nil {
		t.Fatal(err)
	}
	for _, path := range shortcuts {
		if info, err := os.Stat(path); err != nil || info.Size() == 0 {
			t.Fatalf("shortcut missing: %s %v", path, err)
		}
	}
	if _, err := InstallShortcuts(root); err != nil {
		t.Fatalf("reinstalling its own shortcut: %v", err)
	}
	other := t.TempDir()
	os.MkdirAll(filepath.Join(other, filepath.FromSlash(cliDir)), 0o755)
	os.WriteFile(Command(other), []byte("MZ"), 0o755)
	if _, err := InstallShortcuts(other); err == nil {
		t.Fatal("another installation's shortcut was replaced")
	}
	if err := RemoveShortcuts(other); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(shortcuts[0]); err != nil {
		t.Fatal("removing through the other installation deleted this one's shortcut")
	}
	if err := RemoveShortcuts(root); err != nil {
		t.Fatal(err)
	}
	for _, path := range shortcuts {
		if _, err := os.Stat(path); !os.IsNotExist(err) {
			t.Fatalf("shortcut not removed: %s", path)
		}
	}
}

func TestUserPathKeepsOtherEntriesUnexpanded(t *testing.T) {
	key, err := registry.OpenKey(registry.CURRENT_USER, "Environment", registry.QUERY_VALUE|registry.SET_VALUE)
	if err != nil {
		t.Fatal(err)
	}
	defer key.Close()
	original, kind, err := key.GetStringValue("Path")
	if err == nil {
		t.Cleanup(func() {
			if kind == registry.EXPAND_SZ {
				key.SetExpandStringValue("Path", original)
			} else {
				key.SetStringValue("Path", original)
			}
		})
	} else {
		t.Cleanup(func() { key.DeleteValue("Path") })
	}
	if err := key.SetExpandStringValue("Path", `%USERPROFILE%\bin;C:\Tools`); err != nil {
		t.Fatal(err)
	}
	root := t.TempDir()
	for _, step := range []func(string) error{AddUserPath, AddUserPath} {
		if err := step(root); err != nil {
			t.Fatal(err)
		}
	}
	value, kind, err := key.GetStringValue("Path")
	if err != nil || kind != registry.EXPAND_SZ || value != `%USERPROFILE%\bin;C:\Tools;`+root {
		t.Fatalf("PATH after adding: %q (%d) %v", value, kind, err)
	}
	if err := RemoveUserPath(root); err != nil {
		t.Fatal(err)
	}
	if value, _, _ := key.GetStringValue("Path"); value != `%USERPROFILE%\bin;C:\Tools` {
		t.Fatalf("PATH after removing: %q", value)
	}
}

// Windows puts the link in place of %1 and splits the line itself: what a
// raw quote in the link adds lands after "--", never as an option.
func TestHandlerCommandKeepsTheLinkAfterTheOptions(t *testing.T) {
	root := `C:\Games\x86qw`
	line := strings.ReplaceAll(handlerCommand(root), "%1", `qw://x" --target "\\evil\share`)
	args, err := windows.DecomposeCommandLine(line)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{Command(root), "open", "--target", root, "--", "qw://x", "--target", `\\evil\share`}
	if !slices.Equal(args, want) {
		t.Fatalf("%q", args)
	}
}

func TestWindowsHandlersComeAndGo(t *testing.T) {
	const classes = `Software\Classes\`
	for _, key := range []string{"qw", demoProgID, ".mvd", ".qwd"} {
		if k, err := registry.OpenKey(registry.CURRENT_USER, classes+key, registry.QUERY_VALUE); err == nil {
			k.Close()
			t.Skipf("HKCU\\%s%s already exists on this machine", classes, key)
		}
	}
	t.Cleanup(func() {
		for _, key := range []string{"qw", demoProgID, ".mvd", ".qwd"} {
			deleteTree(classes + key)
		}
	})
	extensionKeys := func() bool {
		for _, extension := range []string{".mvd", ".qwd"} {
			if k, err := registry.OpenKey(registry.CURRENT_USER, classes+extension, registry.QUERY_VALUE); err == nil {
				k.Close()
				return true
			}
		}
		return false
	}
	root := t.TempDir()
	// Turning off what was never on leaves the registry as it was.
	if err := SetHandlers(root, false, false); err != nil || extensionKeys() {
		t.Fatalf("turning demos off created .mvd or .qwd: %v", err)
	}
	if err := SetHandlers(root, true, true); err != nil {
		t.Fatal(err)
	}
	if owned, _ := ownedCommand(classes+"qw", root); !owned {
		t.Fatal("qw:// should open with this installation")
	}
	// The command written before "--" is this installation's too.
	command, _ := registry.OpenKey(registry.CURRENT_USER, classes+`qw\shell\open\command`, registry.SET_VALUE)
	command.SetStringValue("", fmt.Sprintf(`"%s" open --target "%s" "%%1"`, Command(root), root))
	command.Close()
	if owned, _ := ownedCommand(classes+"qw", root); !owned {
		t.Fatal("the command without -- is not recognized")
	}
	k, _ := registry.OpenKey(registry.CURRENT_USER, classes+".mvd", registry.QUERY_VALUE)
	if value, _, _ := k.GetStringValue(""); value != demoProgID {
		t.Fatalf(".mvd: %q", value)
	}
	k.Close()
	if err := SetHandlers(t.TempDir(), true, false); err == nil {
		t.Fatal("another installation took qw://")
	}
	if err := SetHandlers(root, false, false); err != nil {
		t.Fatal(err)
	}
	if _, exists := ownedCommand(classes+"qw", root); exists {
		t.Fatal("qw:// should be gone")
	}
	if extensionKeys() {
		t.Fatal(".mvd and .qwd should be gone with the demo type")
	}
}

// Shortcuts to the x86qw.cmd of the layout before 2.5 are this
// installation's: listed as legacy, repointed at x86qw.exe.
func TestLegacyShortcutsAreRepointed(t *testing.T) {
	t.Setenv("APPDATA", t.TempDir())
	t.Setenv("USERPROFILE", t.TempDir())
	root := t.TempDir()
	os.MkdirAll(filepath.Join(root, filepath.FromSlash(cliDir)), 0o755)
	os.WriteFile(Command(root), []byte("MZ"), 0o755)
	shortcuts, err := ShortcutPaths()
	if err != nil {
		t.Fatal(err)
	}
	script := filepath.Join(t.TempDir(), "legacy.ps1")
	os.WriteFile(script, []byte("param([string]$Path, [string]$Target)\r\n"+
		"$Shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($Path)\r\n$Shortcut.TargetPath = $Target\r\n$Shortcut.Save()\r\n"), 0o600)
	for _, path := range shortcuts {
		os.MkdirAll(filepath.Dir(path), 0o755)
		command := exec.Command("powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script,
			"-Path", path, "-Target", filepath.Join(root, "x86qw.cmd"))
		if output, err := command.CombinedOutput(); err != nil {
			t.Fatalf("legacy shortcut: %v %s", err, output)
		}
	}
	if got := legacyShortcuts(root); len(got) != 2 {
		t.Fatalf("legacy shortcuts: %q", got)
	}
	if _, err := InstallShortcuts(root); err != nil {
		t.Fatal(err)
	}
	if got := legacyShortcuts(root); len(got) != 0 {
		t.Fatalf("still legacy after the repoint: %q", got)
	}
}

// A qw:// command that runs .x86qw\cli\x86qw.exe is this installation's
// and listed as legacy until SetHandlers rewrites it.
func TestLegacyHandlersAreRewritten(t *testing.T) {
	const classes = `Software\Classes\`
	for _, key := range []string{"qw", demoProgID, ".mvd", ".qwd"} {
		if k, err := registry.OpenKey(registry.CURRENT_USER, classes+key, registry.QUERY_VALUE); err == nil {
			k.Close()
			t.Skipf("HKCU\\%s%s already exists on this machine", classes, key)
		}
	}
	t.Cleanup(func() {
		for _, key := range []string{"qw", demoProgID, ".mvd", ".qwd"} {
			deleteTree(classes + key)
		}
	})
	root := t.TempDir()
	k, _, err := registry.CreateKey(registry.CURRENT_USER, classes+`qw\shell\open\command`, registry.SET_VALUE)
	if err != nil {
		t.Fatal(err)
	}
	k.SetStringValue("", `"`+filepath.Join(root, ".x86qw", "cli", "x86qw.exe")+`" open --target "`+root+`" "%1"`)
	k.Close()
	if owned, _ := ownedCommand(classes+"qw", root); !owned || len(legacyHandlers(root)) != 1 {
		t.Fatalf("the old command is not recognized: owned %v, legacy %q", owned, legacyHandlers(root))
	}
	if err := SetHandlers(root, true, false); err != nil {
		t.Fatal(err)
	}
	if owned, _ := ownedCommand(classes+"qw", root); !owned || len(legacyHandlers(root)) != 0 {
		t.Fatalf("not rewritten: owned %v, legacy %q", owned, legacyHandlers(root))
	}
}

// Turning demos off in one installation leaves .mvd and .qwd to the one
// whose demo type they open.
func TestDemosStayWithTheirInstallation(t *testing.T) {
	const classes = `Software\Classes\`
	for _, key := range []string{"qw", demoProgID, ".mvd", ".qwd"} {
		if k, err := registry.OpenKey(registry.CURRENT_USER, classes+key, registry.QUERY_VALUE); err == nil {
			k.Close()
			t.Skipf("HKCU\\%s%s already exists on this machine", classes, key)
		}
	}
	t.Cleanup(func() {
		for _, key := range []string{"qw", demoProgID, ".mvd", ".qwd"} {
			deleteTree(classes + key)
		}
	})
	root, other := t.TempDir(), t.TempDir()
	if err := SetHandlers(root, false, true); err != nil {
		t.Fatal(err)
	}
	if err := SetHandlers(other, false, false); err != nil {
		t.Fatal(err)
	}
	if owned, _ := ownedCommand(classes+demoProgID, root); !owned {
		t.Fatal("the other installation took the demo type")
	}
	for _, extension := range []string{".mvd", ".qwd"} {
		k, _ := registry.OpenKey(registry.CURRENT_USER, classes+extension, registry.QUERY_VALUE)
		value, _, _ := k.GetStringValue("")
		k.Close()
		if value != demoProgID {
			t.Fatalf("%s: %q after the other installation turned demos off", extension, value)
		}
	}
}
