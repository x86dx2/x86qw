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

func handlerCommand(root string) string {
	return fmt.Sprintf(`"%s" open --target "%s" "%%1"`, filepath.Join(root, ".x86qw", "cli", BinaryName()), root)
}

// ownedCommand reports whether the open command under key launches root.
func ownedCommand(key, root string) (owned, exists bool) {
	k, err := registry.OpenKey(registry.CURRENT_USER, key+`\shell\open\command`, registry.QUERY_VALUE)
	if err != nil {
		return false, false
	}
	defer k.Close()
	value, _, err := k.GetStringValue("")
	return err == nil && strings.Contains(value, filepath.Join(root, ".x86qw", "cli")), true
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
		errs = append(errs, errors.New("Outro programa já abre links qw://; o x86QW não o substituiu."))
	case links:
		errs = append(errs, writeCommand(classes+"qw", root, "URL:QuakeWorld", true))
	case owned:
		errs = append(errs, deleteTree(classes+"qw"))
	}
	owned, exists = ownedCommand(classes+demoProgID, root)
	if demos && exists && !owned {
		errs = append(errs, errors.New("O tipo x86qw.demo pertence a outra instalação."))
		return errors.Join(errs...)
	}
	if demos {
		errs = append(errs, writeCommand(classes+demoProgID, root, "QuakeWorld demo", false))
	} else if owned {
		errs = append(errs, deleteTree(classes+demoProgID))
	}
	for _, extension := range []string{".mvd", ".qwd"} {
		key, _, err := registry.CreateKey(registry.CURRENT_USER, classes+extension, registry.QUERY_VALUE|registry.SET_VALUE)
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
		}
		key.Close()
	}
	return errors.Join(errs...)
}
