//go:build windows

package play

import (
	"errors"
	"os"
	"time"
)

// Spawn is not ported to Windows yet.
func Spawn(executable string, args []string, dir string) (*os.Process, error) {
	return nil, errors.New("o lançamento do jogo ainda não é suportado no Windows")
}

// ExitsWithin is unused on Windows.
func ExitsWithin(process *os.Process, grace time.Duration) (bool, int) { return false, 0 }
