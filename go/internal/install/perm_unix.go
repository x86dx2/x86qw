//go:build !windows

package install

import "golang.org/x/sys/unix"

// executableMissing mirrors host.executable_permission_missing.
func executableMissing(path string) bool {
	return unix.Access(path, unix.X_OK) != nil
}
