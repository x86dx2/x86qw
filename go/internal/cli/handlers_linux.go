package cli

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

// On Linux a hidden desktop entry runs `x86qw open` for qw:// links and
// for the demo type a shared-mime-info package declares, and xdg-mime
// makes it the default for what is on. The entry itself is in desktop.go.

func demoMIMEPath(home string) string {
	return filepath.Join(home, ".local", "share", "mime", "packages", "x86qw-demo.xml")
}

const demoMIMEPackage = `<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
  <mime-type type="application/x-quakeworld-demo">
    <comment>QuakeWorld demo</comment>
    <glob pattern="*.mvd"/>
    <glob pattern="*.qwd"/>
  </mime-type>
</mime-info>
`

func setHandlers(root string, links, demos bool) error {
	home, err := os.UserHomeDir()
	if err != nil {
		return err
	}
	entry := handlerEntryPath(home)
	if payload, err := os.ReadFile(entry); err == nil && !launches(string(payload), root) {
		return fmt.Errorf("An unmanaged shortcut is already at %s.", entry)
	}
	if !links && !demos {
		os.Remove(demoMIMEPath(home))
		if err := os.Remove(entry); err != nil && !errors.Is(err, os.ErrNotExist) {
			return err
		}
		refreshMIME(home)
		return nil
	}
	if err := os.MkdirAll(filepath.Dir(entry), 0o755); err != nil {
		return err
	}
	if err := os.WriteFile(entry, []byte(handlerEntry(root)), 0o644); err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(demoMIMEPath(home)), 0o755); err != nil {
		return err
	}
	if err := os.WriteFile(demoMIMEPath(home), []byte(demoMIMEPackage), 0o644); err != nil {
		return err
	}
	refreshMIME(home)
	var errs []error
	for _, item := range []struct {
		on   bool
		mime string
	}{{links, "x-scheme-handler/qw"}, {demos, demoMIME}} {
		if !item.on {
			continue
		}
		if output, err := exec.Command("xdg-mime", "default", "x86qw-open.desktop", item.mime).CombinedOutput(); err != nil {
			errs = append(errs, fmt.Errorf("xdg-mime %s: %s", item.mime, strings.TrimSpace(string(output)+" "+err.Error())))
		}
	}
	return errors.Join(errs...)
}

// refreshMIME rebuilds the user's MIME and desktop caches when the tools
// exist; without them the files still take effect at the next login.
func refreshMIME(home string) {
	exec.Command("update-mime-database", filepath.Join(home, ".local", "share", "mime")).Run()
	exec.Command("update-desktop-database", filepath.Join(home, ".local", "share", "applications")).Run()
}

// legacyHandlers is empty: StillLegacy reads the Linux entries itself.
func legacyHandlers(string) []string { return nil }
