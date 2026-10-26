//go:build !windows

package maint

import "os"

// removeAfterExit is never needed on POSIX: a running executable can be
// unlinked.
func removeAfterExit(original, aside string) bool { return false }

// deferLauncher is never needed on POSIX: the shell has already read the
// launcher script.
func deferLauncher(path string) bool { return false }

// removesItself reports whether path is the running executable: POSIX
// removes it in place while it runs.
func removesItself(path string) bool {
	self, err := os.Executable()
	if err != nil {
		return false
	}
	pathInfo, err := os.Lstat(path)
	selfInfo, selfErr := os.Stat(self)
	return err == nil && selfErr == nil && os.SameFile(pathInfo, selfInfo)
}
