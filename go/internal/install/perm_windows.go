//go:build windows

package install

// executableMissing is false on Windows, which has no POSIX execute bit.
func executableMissing(path string) bool { return false }
