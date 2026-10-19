//go:build windows

package lock

import "errors"

// Lock is unavailable on Windows until the named-mutex protocol is ported.
type Lock struct{ Path string }

// Acquire refuses on Windows for now.
func Acquire(target, command string) (*Lock, error) {
	return nil, errors.New("o lock de instalação ainda não foi portado para Windows; use a CLI Python")
}

// Release is a no-op.
func (l *Lock) Release() error { return nil }
