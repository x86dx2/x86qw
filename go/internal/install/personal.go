package install

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// SavePersonalFile mirrors personal_file_step for a create-or-replace:
// missing parents are created 0755 under root, symlinks anywhere on the
// path are refused and the file is published atomically with mode 0644.
func SavePersonalFile(root, relative string, payload []byte) error {
	destination := filepath.Join(root, filepath.FromSlash(relative))
	parts := strings.Split(filepath.ToSlash(relative), "/")
	current := root
	for _, part := range parts[:len(parts)-1] {
		current = filepath.Join(current, part)
		info, err := os.Lstat(current)
		if errors.Is(err, os.ErrNotExist) {
			if err := os.Mkdir(current, 0o755); err != nil {
				return err
			}
			continue
		}
		if err != nil {
			return err
		}
		if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
			return fmt.Errorf("Caminho pessoal inseguro: %s", current)
		}
	}
	if info, err := os.Lstat(destination); err == nil && !info.Mode().IsRegular() {
		return fmt.Errorf("Caminho pessoal inseguro: %s", destination)
	}
	if err := atomicWrite(destination, payload, 0o644); err != nil {
		return fmt.Errorf("Falha ao publicar configuração pessoal: %s", destination)
	}
	return nil
}
