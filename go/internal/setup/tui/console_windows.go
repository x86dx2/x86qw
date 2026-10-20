package tui

import (
	"os"

	"golang.org/x/sys/windows"
)

// legacyConsole is the console of Windows before 11 (build 22000) outside
// Windows Terminal and other modern terminals.
func legacyConsole() bool {
	if os.Getenv("WT_SESSION") != "" || os.Getenv("TERM_PROGRAM") != "" {
		return false
	}
	return windows.RtlGetVersion().BuildNumber < 22000
}
