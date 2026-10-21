//go:build !windows

package install

import "golang.org/x/sys/unix"

// executableMissing reports whether path lacks the execute permission.
func executableMissing(path string) bool {
	return unix.Access(path, unix.X_OK) != nil
}
