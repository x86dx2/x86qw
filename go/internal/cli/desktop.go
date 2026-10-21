package cli

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
)

// A Linux desktop entry opens `x86qw play` in a terminal from the
// application menu, and a hidden one opens qw:// links and demos (see
// handlers_linux.go). Like the Windows shortcuts, an entry that points at
// another installation is never replaced or removed.

const demoMIME = "application/x-quakeworld-demo"

// DesktopEntryPath is where the entry lives for the user.
func DesktopEntryPath(home string) string {
	return filepath.Join(home, ".local", "share", "applications", "x86qw.desktop")
}

func handlerEntryPath(home string) string {
	return filepath.Join(home, ".local", "share", "applications", "x86qw-open.desktop")
}

func handlerEntry(root string) string {
	return fmt.Sprintf(`[Desktop Entry]
Type=Application
Name=x86QW
Comment=Abre servidores qw:// e demos QuakeWorld
Exec=%s open --target %s %%u
NoDisplay=true
Terminal=false
MimeType=x-scheme-handler/qw;%s;
`, execArgument(filepath.Join(root, "x86qw.sh")), execArgument(root), demoMIME)
}

// RefreshEntries rewrites the Linux entries that launch root in the
// current format, so an update repairs what an earlier version wrote. It
// creates none and leaves the others alone.
func RefreshEntries(root, home string) {
	if runtime.GOOS != "linux" {
		return
	}
	for path, entry := range map[string]string{DesktopEntryPath(home): desktopEntry(root), handlerEntryPath(home): handlerEntry(root)} {
		if owned, _ := ownsEntry(path, root); owned {
			os.WriteFile(path, []byte(entry), 0o644)
		}
	}
}

func desktopEntry(root string) string {
	return fmt.Sprintf(`[Desktop Entry]
Type=Application
Name=x86QW
Comment=QuakeWorld moderno
Exec=%s play
Path=%s
Terminal=true
Categories=Game;ActionGame;
`, execArgument(filepath.Join(root, "x86qw.sh")), root)
}

// execArgument writes one argument of an Exec line: bare when nothing in
// it needs quoting, the form every launcher reads (xdg-open without a
// desktop environment splits on spaces and keeps the quotes); quoted and
// escaped as the Desktop Entry spec says otherwise.
func execArgument(value string) string {
	value = strings.ReplaceAll(value, "%", "%%")
	if !strings.ContainsAny(value, " \t\n\"'\\><~|&;$*?#()`") {
		return value
	}
	quoted := strings.NewReplacer(`"`, `\"`, "`", "\\`", "$", `\$`, `\`, `\\`).Replace(value)
	// The string escape doubles each backslash once more.
	return `"` + strings.ReplaceAll(quoted, `\`, `\\`) + `"`
}

// launches reports whether a desktop entry runs root's x86qw.sh, written
// bare or always quoted, as entries were before 2.4.1.
func launches(payload, root string) bool {
	launcher := filepath.Join(root, "x86qw.sh")
	return strings.Contains(payload, "Exec="+execArgument(launcher)+" ") || strings.Contains(payload, `Exec="`+launcher+`"`)
}

// ownsEntry reports whether the entry at path launches root.
func ownsEntry(path, root string) (bool, bool) {
	payload, err := os.ReadFile(path)
	if err != nil {
		return false, false
	}
	return launches(string(payload), root), true
}

// InstallDesktopEntry writes the entry for root.
func InstallDesktopEntry(root, home string) error {
	path := DesktopEntryPath(home)
	if owned, exists := ownsEntry(path, root); exists && !owned {
		return fmt.Errorf("Um atalho não gerenciado já ocupa %s.", path)
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	return os.WriteFile(path, []byte(desktopEntry(root)), 0o644)
}

// RemoveDesktopEntry deletes the entry when it launches root.
func RemoveDesktopEntry(root, home string) error {
	path := DesktopEntryPath(home)
	if owned, _ := ownsEntry(path, root); owned {
		return os.Remove(path)
	}
	return nil
}

// UnlinkUserCommand removes ~/.local/bin/x86qw when it points at root.
func UnlinkUserCommand(root, home string) error {
	command := filepath.Join(home, ".local", "bin", "x86qw")
	link, err := os.Readlink(command)
	if err != nil {
		return nil
	}
	if !filepath.IsAbs(link) {
		link = filepath.Join(filepath.Dir(command), link)
	}
	resolved, _ := filepath.EvalSymlinks(link)
	expected, _ := filepath.EvalSymlinks(filepath.Join(root, "x86qw.sh"))
	if resolved == expected || link == filepath.Join(root, "x86qw.sh") {
		return os.Remove(command)
	}
	return nil
}
