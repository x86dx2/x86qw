// Package privfs creates and protects the private directories of an
// installation (.x86qw, sessions, staging, journals, caches): mode 0700 on
// POSIX, and on Windows a protected DACL that grants full access only to the
// current user and
// LOCAL SYSTEM and is inherited by everything created inside.
package privfs

import (
	"errors"
	"os"
	"path/filepath"
)

// MkdirAll creates path and every missing parent as private directories.
func MkdirAll(path string) error {
	if info, err := os.Lstat(path); err == nil {
		if !info.IsDir() {
			return &os.PathError{Op: "mkdir", Path: path, Err: errors.New("não é um diretório")}
		}
		return nil
	}
	if parent := filepath.Dir(path); parent != path {
		if err := MkdirAll(parent); err != nil {
			return err
		}
	}
	if err := Mkdir(path); err != nil && !errors.Is(err, os.ErrExist) {
		return err
	}
	return nil
}
