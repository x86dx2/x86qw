//go:build !windows

package maint

// removeAfterExit is never needed on POSIX: a running executable can be
// unlinked.
func removeAfterExit(original, aside string) bool { return false }
