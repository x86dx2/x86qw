//go:build windows

package play

import "errors"

// RuntimeConfig is unavailable on Windows until launching is ported.
type RuntimeConfig struct{ Name string }

// Create refuses on Windows.
func Create(target string, payload []byte) (*RuntimeConfig, error) {
	return nil, errors.New("configuração KTX ainda não é suportada no Windows")
}

// Transfer is a no-op.
func (c *RuntimeConfig) Transfer(pid int) error { return nil }

// Release is a no-op.
func (c *RuntimeConfig) Release() {}
