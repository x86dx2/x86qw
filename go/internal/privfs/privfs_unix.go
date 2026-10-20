//go:build !windows

package privfs

import "os"

// Mkdir creates one directory with mode 0700.
func Mkdir(path string) error { return os.Mkdir(path, 0o700) }

// Protect makes an existing directory private (0700).
func Protect(path string) error { return os.Chmod(path, 0o700) }

// Ensure re-protects a directory that is readable by others.
func Ensure(path string, info os.FileInfo) error {
	if info.Mode().Perm()&0o077 != 0 {
		return Protect(path)
	}
	return nil
}
