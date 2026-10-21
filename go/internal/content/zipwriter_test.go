package content

import (
	"archive/zip"
	"bytes"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func publishedCache(t *testing.T) []string {
	t.Helper()
	dir := os.Getenv("X86QW_COMPONENT_CACHE")
	if dir == "" {
		output, err := exec.Command("getconf", "DARWIN_USER_CACHE_DIR").Output()
		if err == nil {
			dir = filepath.Join(strings.TrimSpace(string(output)), "x86qw", "components")
		}
	}
	paths, _ := filepath.Glob(filepath.Join(dir, "*.zip"))
	if len(paths) == 0 {
		t.Skip("published package cache unavailable")
	}
	return paths
}

// TestZipLayoutRebuildsPublishedPackages rewrites every published package
// from its own members, in order, and requires identical bytes: member
// order, headers, timestamps, attributes and deflate streams all match.
func TestZipLayoutRebuildsPublishedPackages(t *testing.T) {
	for _, path := range publishedCache(t) {
		info, _ := os.Stat(path)
		if info.Size() > 64<<20 && os.Getenv("X86QW_DEFLATE_LARGE") != "1" {
			continue
		}
		published, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		reader, err := zip.NewReader(bytes.NewReader(published), int64(len(published)))
		if err != nil {
			t.Fatal(err)
		}
		var entries []ZipEntry
		for _, file := range reader.File {
			source, err := file.Open()
			if err != nil {
				t.Fatal(err)
			}
			data, _ := io.ReadAll(source)
			source.Close()
			entries = append(entries, ZipEntry{Name: file.Name, Data: data})
		}
		if entries[len(entries)-1].Name != "_x86qw/component.json" {
			t.Errorf("%s: component.json is not the last member", filepath.Base(path))
		}
		rebuilt, err := BuildZip(entries)
		if err != nil {
			t.Fatal(err)
		}
		if !bytes.Equal(rebuilt, published) {
			t.Errorf("%s: ZIP layout differs from the published one", filepath.Base(path))
		}
	}
}
