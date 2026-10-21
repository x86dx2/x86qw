package content

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"strconv"
	"testing"
)

func TestParseLFSPointer(t *testing.T) {
	oid := "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
	good := "version https://git-lfs.github.com/spec/v1\noid sha256:" + oid + "\nsize 12\n"
	if got, size, err := ParseLFSPointer([]byte(good)); err != nil || got != oid || size != 12 {
		t.Fatalf("good pointer: %v %v %v", got, size, err)
	}
	for _, bad := range []string{
		good + "\n",
		"version https://git-lfs.github.com/spec/v1\noid sha256:" + oid + "\nsize 012\n",
		"version https://git-lfs.github.com/spec/v1\noid sha1:" + oid + "\nsize 12\n",
		"version https://git-lfs.github.com/spec/v1\noid sha256:" + oid + "\nextra 12\n",
		"version https://git-lfs.github.com/spec/v1\noid sha256:" + oid + "\nsize -1\n",
	} {
		if _, _, err := ParseLFSPointer([]byte(bad)); err == nil {
			t.Fatalf("accepted %q", bad)
		}
	}
}

func TestMaterializeLFS(t *testing.T) {
	root := t.TempDir()
	payload := []byte("test LFS object")
	sum := sha256.Sum256(payload)
	oid := hex.EncodeToString(sum[:])
	pointer := "version https://git-lfs.github.com/spec/v1\noid sha256:" + oid + "\nsize " + strconv.Itoa(len(payload)) + "\n"
	os.MkdirAll(filepath.Join(root, "dist", "a b"), 0o755)
	os.WriteFile(filepath.Join(root, "dist", "a b", "x+1.zip"), []byte(pointer), 0o644)
	os.WriteFile(filepath.Join(root, "dist", "plain.txt"), []byte("not a pointer"), 0o644)
	var urls []string
	fetch := func(_ context.Context, url, destination string, size int64, got string) error {
		urls = append(urls, url)
		os.MkdirAll(filepath.Dir(destination), 0o755)
		return os.WriteFile(destination, payload, 0o644)
	}
	commit := "0123456789abcdef0123456789abcdef01234567"
	created, err := MaterializeLFS(context.Background(), root, "x86dx2/x86qw", commit, fetch)
	if err != nil || created != 1 {
		t.Fatalf("first run: %d %v", created, err)
	}
	if want := "https://media.githubusercontent.com/media/x86dx2/x86qw/" + commit + "/dist/a%20b/x%2B1.zip"; len(urls) != 1 || urls[0] != want {
		t.Fatalf("urls %v", urls)
	}
	if created, err := MaterializeLFS(context.Background(), root, "x86dx2/x86qw", commit, fetch); err != nil || created != 0 {
		t.Fatalf("second run: %d %v", created, err)
	}
	if _, err := MaterializeLFS(context.Background(), root, "bad repo", commit, fetch); err != nil {
		t.Fatalf("cached objects need no URL: %v", err)
	}
}
