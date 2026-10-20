package cli

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// A Linux desktop entry opens `x86qw play` in a terminal from the
// application menu. Like the Windows shortcuts, an entry that points at
// another installation is never replaced or removed.

// DesktopEntryPath is where the entry lives for the user.
func DesktopEntryPath(home string) string {
	return filepath.Join(home, ".local", "share", "applications", "x86qw.desktop")
}

func desktopEntry(root string) string {
	launcher := filepath.Join(root, "x86qw.sh")
	return fmt.Sprintf(`[Desktop Entry]
Type=Application
Name=x86QW
Comment=QuakeWorld moderno
Exec="%s" play
Path=%s
Terminal=true
Categories=Game;ActionGame;
`, launcher, root)
}

// ownsEntry reports whether the entry at path launches root.
func ownsEntry(path, root string) (bool, bool) {
	payload, err := os.ReadFile(path)
	if err != nil {
		return false, false
	}
	return strings.Contains(string(payload), `Exec="`+filepath.Join(root, "x86qw.sh")+`"`), true
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
