package cli

import (
	"errors"
	"fmt"
	"path/filepath"
	"strings"

	"golang.org/x/sys/windows/registry"
)

// On Windows the user's classes open qw:// links and the demo extensions
// with the installation's x86qw.exe. A key that already belongs to
// another program is never touched; a choice the user made in Windows
// settings for .mvd still wins over these defaults.

const demoProgID = "x86qw.demo"

// handlerCommand ends the options before the link: a quote in it would
// otherwise let it add its own --target.
func handlerCommand(root string) string {
	return fmt.Sprintf(`"%s" open --target "%s" -- "%%1"`, Command(root), root)
}

// ownedCommand reports whether the open command under key launches root:
// its x86qw.exe, or the one in .x86qw\cli before 2.5.
func ownedCommand(key, root string) (owned, exists bool) {
	k, err := registry.OpenKey(registry.CURRENT_USER, key+`\shell\open\command`, registry.QUERY_VALUE)
	if err != nil {
		return false, false
	}
	defer k.Close()
	value, _, err := k.GetStringValue("")
	legacy := strings.Contains(strings.ToLower(value), strings.ToLower(filepath.Join(root, ".x86qw", "cli")+`\`))
	return err == nil && (strings.Contains(strings.ToLower(value), strings.ToLower(`"`+Command(root)+`"`)) || legacy), true
}

func writeCommand(key, root, label string, protocol bool) error {
	k, _, err := registry.CreateKey(registry.CURRENT_USER, key, registry.SET_VALUE)
	if err != nil {
		return err
	}
	k.SetStringValue("", label)
	if protocol {
		k.SetStringValue("URL Protocol", "")
	}
	k.Close()
	command, _, err := registry.CreateKey(registry.CURRENT_USER, key+`\shell\open\command`, registry.SET_VALUE)
	if err != nil {
		return err
	}
	defer command.Close()
	return command.SetStringValue("", handlerCommand(root))
}

func deleteTree(key string) error {
	k, err := registry.OpenKey(registry.CURRENT_USER, key, registry.ENUMERATE_SUB_KEYS)
	if err != nil {
		return nil
	}
	names, _ := k.ReadSubKeyNames(-1)
	k.Close()
	for _, name := range names {
		if err := deleteTree(key + `\` + name); err != nil {
			return err
		}
	}
	return registry.DeleteKey(registry.CURRENT_USER, key)
}

func setHandlers(root string, links, demos bool) error {
	const classes = `Software\Classes\`
	var errs []error
	owned, exists := ownedCommand(classes+"qw", root)
	switch {
	case links && exists && !owned:
		errs = append(errs, errors.New("Another program already opens qw:// links; x86QW did not replace it."))
	case links:
		errs = append(errs, writeCommand(classes+"qw", root, "URL:QuakeWorld", true))
	case owned:
		errs = append(errs, deleteTree(classes+"qw"))
	}
	owned, exists = ownedCommand(classes+demoProgID, root)
	if demos && exists && !owned {
		errs = append(errs, errors.New("The x86qw.demo type belongs to another installation."))
		return errors.Join(errs...)
	}
	switch {
	case demos:
		errs = append(errs, writeCommand(classes+demoProgID, root, "QuakeWorld demo", false))
	case owned:
		errs = append(errs, deleteTree(classes+demoProgID))
	case exists:
		// The demo type is another installation's, and so are .mvd and .qwd.
		return errors.Join(errs...)
	}
	for _, extension := range []string{".mvd", ".qwd"} {
		key, err := registry.OpenKey(registry.CURRENT_USER, classes+extension, registry.QUERY_VALUE|registry.SET_VALUE)
		if demos && errors.Is(err, registry.ErrNotExist) {
			key, _, err = registry.CreateKey(registry.CURRENT_USER, classes+extension, registry.QUERY_VALUE|registry.SET_VALUE)
		}
		if errors.Is(err, registry.ErrNotExist) {
			continue
		}
		if err != nil {
			errs = append(errs, err)
			continue
		}
		current, _, _ := key.GetStringValue("")
		switch {
		case demos && (current == "" || current == demoProgID):
			errs = append(errs, key.SetStringValue("", demoProgID))
		case !demos && current == demoProgID:
			errs = append(errs, key.DeleteValue(""))
			// A key left with nothing in it was ours alone.
			if info, err := key.Stat(); err == nil && info.SubKeyCount == 0 && info.ValueCount == 0 {
				key.Close()
				errs = append(errs, registry.DeleteKey(registry.CURRENT_USER, classes+extension))
				continue
			}
		}
		key.Close()
	}
	return errors.Join(errs...)
}

// legacyHandlers lists the qw:// and demo commands, and the shortcuts,
// that still run root's CLI from .x86qw\cli or through x86qw.cmd.
func legacyHandlers(root string) []string {
	var found []string
	legacy := strings.ToLower(filepath.Join(root, ".x86qw", "cli") + `\`)
	for _, key := range []string{`Software\Classes\qw`, `Software\Classes\` + demoProgID} {
		k, err := registry.OpenKey(registry.CURRENT_USER, key+`\shell\open\command`, registry.QUERY_VALUE)
		if err != nil {
			continue
		}
		value, _, err := k.GetStringValue("")
		k.Close()
		if err == nil && strings.Contains(strings.ToLower(value), legacy) {
			found = append(found, `HKCU\`+key)
		}
	}
	return append(found, legacyShortcuts(root)...)
}

// legacyShortcuts lists root's shortcuts that still point at x86qw.cmd.
func legacyShortcuts(root string) []string {
	shortcuts, err := ShortcutPaths()
	if err != nil {
		return nil
	}
	output, err := shortcutScriptOutput("legacy", root, filepath.Join(root, filepath.FromSlash(cliDir), "x86qw.ico"), shortcuts)
	if err != nil {
		// Unreadable shortcuts count as still pointing at the old launcher.
		return shortcuts[:]
	}
	var found []string
	for _, line := range strings.Split(output, "\n") {
		if line = strings.TrimSpace(line); line != "" {
			found = append(found, line)
		}
	}
	return found
}
