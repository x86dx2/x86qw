package main

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestChecksumsMustListEveryBinary(t *testing.T) {
	dir := t.TempDir()
	var sums strings.Builder
	for _, target := range Targets {
		name := assetName("9.9.9", target)
		body := []byte("binário " + target)
		if err := os.WriteFile(filepath.Join(dir, name), body, 0o644); err != nil {
			t.Fatal(err)
		}
		digest := sha256.Sum256(body)
		fmt.Fprintf(&sums, "%s  %s\n", hex.EncodeToString(digest[:]), name)
	}
	write := func(text string) {
		if err := os.WriteFile(filepath.Join(dir, "SHA256SUMS"), []byte(text), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	write(sums.String())
	if p, err := checkSums("9.9.9", dir); err != nil || p.Size["linux-amd64"] == 0 {
		t.Fatalf("%v %+v", err, p)
	}
	write(strings.Replace(sums.String(), "0", "1", 1))
	if _, err := checkSums("9.9.9", dir); err == nil {
		t.Fatal("um hash trocado passou")
	}
}
