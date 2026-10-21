//go:build !windows

package cli

import (
	"errors"
	"os"
)

// retireBatch removes an x86qw.cmd; nothing runs one here.
func retireBatch(path string) error {
	if err := os.Remove(path); err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	return nil
}
