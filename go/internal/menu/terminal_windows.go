//go:build windows

package menu

import (
	"errors"
	"os"
)

// Windows keeps the line-based fallback until key reading is ported.
func isTerminal(file *os.File) bool { return false }

func terminalSize() (int, int) { return 88, 24 }

func readTerminalKey() (string, error) {
	return "", errors.New("navegação por teclas ainda não foi portada para Windows")
}
