//go:build !windows

package tui

// legacyConsole is only ever true on Windows.
func legacyConsole() bool { return false }
