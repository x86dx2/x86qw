package main

import (
	"bytes"
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
				t.Fatalf("%s: non-ASCII byte at position %d", path, index)
			}
		}
	}
}

// Windows PowerShell 5.1 may offer only TLS 1.0, which GitHub refuses, so
// the bootstrap turns on TLS 1.2 before it downloads the CLI.
func TestPowerShellBootstrapEnablesTLS12(t *testing.T) {
	for _, path := range []string{
		filepath.Join("templates", "install.ps1"),
		filepath.Join("..", "..", "..", "site", "public", "install.ps1"),
	} {
		payload, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		tls := bytes.Index(payload, []byte("[Net.ServicePointManager]::SecurityProtocol -bor 3072"))
		if client := bytes.Index(payload, []byte("HttpClient]::new()")); tls < 0 || tls > client {
			t.Fatalf("%s does not turn on TLS 1.2 before the download", path)
		}
	}
}
