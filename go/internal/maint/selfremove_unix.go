//go:build !windows

package maint

// removeAfterExit is never needed on POSIX: a running executable can be
// unlinked.
func removeAfterExit(original, aside string) bool { return false }

// deferLauncher is never needed on POSIX: the shell has already read the
// launcher script.
func deferLauncher(path string) bool { return false }
