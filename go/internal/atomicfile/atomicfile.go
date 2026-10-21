// Package atomicfile replaces files so a reader sees the old bytes or the
// new ones, never a mix, and two writers never share a temporary file.
package atomicfile

import (
	"errors"
	"os"
	"path/filepath"
)

// Write replaces path with data at mode: a temporary file beside it
// (CreateTemp, so concurrent writers never collide), synced, then renamed
// over path.
func Write(path string, data []byte, mode os.FileMode) error {
	temporary, err := os.CreateTemp(filepath.Dir(path), "."+filepath.Base(path)+".*.tmp")
	if err != nil {
		return err
	}
	_, writeErr := temporary.Write(data)
	err = errors.Join(writeErr, temporary.Sync(), temporary.Close())
	if err == nil {
		err = os.Chmod(temporary.Name(), mode)
	}
	if err == nil {
		err = rename(temporary.Name(), path)
	}
	if err != nil {
		os.Remove(temporary.Name())
	}
	return err
}
