//go:build !windows

package services

import (
	"os"
	"testing"
)

func exposeToOthers(t *testing.T, path string) { os.Chmod(path, 0o644) }
