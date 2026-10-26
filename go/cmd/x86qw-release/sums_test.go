package main

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// releaseAssets writes the binaries of version 9.9.9 into dir and returns
// their SHA256SUMS.
func releaseAssets(t *testing.T, dir string) string {
	t.Helper()
	var sums strings.Builder
	for _, target := range Targets {
		name := assetName("9.9.9", target)
		body := []byte("binary " + target)
		if err := os.WriteFile(filepath.Join(dir, name), body, 0o644); err != nil {
			t.Fatal(err)
		}
		digest := sha256.Sum256(body)
		fmt.Fprintf(&sums, "%s  %s\n", hex.EncodeToString(digest[:]), name)
	}
	return sums.String()
}

func TestChecksumsMustListEveryBinary(t *testing.T) {
	dir := t.TempDir()
	sums := releaseAssets(t, dir)
	write := func(text string) {
		if err := os.WriteFile(filepath.Join(dir, "SHA256SUMS"), []byte(text), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	write(sums)
	if p, err := checkSums("9.9.9", dir); err != nil || p.Size["linux-amd64"] == 0 {
		t.Fatalf("%v %+v", err, p)
	}
	write(strings.Replace(sums, "0", "1", 1))
	if _, err := checkSums("9.9.9", dir); err == nil {
		t.Fatal("a swapped hash passed")
	}
}

// SHA256SUMS comes from the same release as the binaries: a binary the
// release workflow did not attest stops prepare before it pins anything.
func TestPrepareRefusesABinaryWithoutAttestation(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "SHA256SUMS"), []byte(releaseAssets(t, dir)), 0o644); err != nil {
		t.Fatal(err)
	}
	saved := attested
	t.Cleanup(func() { attested = saved })
	attested = func(path string) error { return errors.New("no attestation for " + filepath.Base(path)) }
	if err := prepare(t.TempDir(), "9.9.9", dir, ""); err == nil || !strings.Contains(err.Error(), "no attestation") {
		t.Fatalf("prepare went on: %v", err)
	}
}
