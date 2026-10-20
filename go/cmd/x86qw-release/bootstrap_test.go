package main

import (
	"os"
	"path/filepath"
	"testing"
)

// Windows PowerShell 5.1 parses BOM-less .ps1 files as ANSI, so a single
// UTF-8 character breaks the served bootstrap.
func TestPowerShellBootstrapIsASCII(t *testing.T) {
	for _, path := range []string{
		filepath.Join("templates", "install.ps1"),
		filepath.Join("..", "..", "..", "site", "public", "install.ps1"),
	} {
		payload, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		for index, b := range payload {
			if b > 0x7f {
				t.Fatalf("%s: byte não ASCII na posição %d", path, index)
			}
		}
	}
}
