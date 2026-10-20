// Package launchguard keeps a validated executable from changing between
// its SHA-256 check and process creation (host.bound_launch_target). On
// Windows the file is held open without write or delete sharing while it
// is hashed and launched; elsewhere the hash is checked right before start.
package launchguard

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
)

func digest(reader io.Reader) (string, error) {
	hash := sha256.New()
	if _, err := io.Copy(hash, reader); err != nil {
		return "", err
	}
	return hex.EncodeToString(hash.Sum(nil)), nil
}

func mismatch(path string) error {
	return fmt.Errorf("Alvo de execução mudou após a validação: %s", path)
}
