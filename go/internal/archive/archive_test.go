package archive

import (
	"archive/zip"
	"bytes"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

type entry struct {
	name string
	body string
	mode os.FileMode
}

func build(t *testing.T, entries ...entry) string {
	t.Helper()
	var buffer bytes.Buffer
	writer := zip.NewWriter(&buffer)
	for _, e := range entries {
		header := &zip.FileHeader{Name: e.name, Method: zip.Deflate}
		if e.mode != 0 {
			header.SetMode(e.mode)
		}
		out, err := writer.CreateHeader(header)
		if err != nil {
			t.Fatal(err)
		}
		out.Write([]byte(e.body))
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "a.zip")
	if err := os.WriteFile(path, buffer.Bytes(), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestExtractsValidArchive(t *testing.T) {
	source := build(t,
		entry{name: "payload/"},
		entry{name: "payload/qw/ktx.cfg", body: "set a 1\n"},
		entry{name: "payload/bin/run", body: "#!/bin/sh\n", mode: 0o755},
	)
	plan, err := Open(source, DefaultLimits)
	if err != nil {
		t.Fatal(err)
	}
	defer plan.Close()
	destination := filepath.Join(t.TempDir(), "out")
	if err := plan.Extract(destination, "payload/bin/run"); err != nil {
		t.Fatal(err)
	}
	data, _ := os.ReadFile(filepath.Join(destination, "payload/qw/ktx.cfg"))
	if string(data) != "set a 1\n" {
		t.Fatalf("conteúdo %q", data)
	}
	// Windows has no POSIX mode bits to preserve.
	info, _ := os.Stat(filepath.Join(destination, "payload/bin/run"))
	if runtime.GOOS != "windows" && info.Mode().Perm() != 0o755 {
		t.Fatalf("modo %v", info.Mode())
	}
	info, _ = os.Stat(filepath.Join(destination, "payload/qw/ktx.cfg"))
	if runtime.GOOS != "windows" && info.Mode().Perm() != 0o644 {
		t.Fatalf("modo %v", info.Mode())
	}
	if err := plan.Extract(destination); err == nil {
		t.Fatal("extração sobre destino existente aceita")
	}
}

func TestRejectsUnsafeArchives(t *testing.T) {
	cases := map[string][]entry{
		"parent":       {{name: "../evil", body: "x"}},
		"absolute":     {{name: "/etc/x", body: "x"}},
		"backslash":    {{name: `a\b`, body: "x"}},
		"colon":        {{name: "c:x", body: "x"}},
		"reserved":     {{name: "dir/CON.txt", body: "x"}},
		"trailing dot": {{name: "dir/name.", body: "x"}},
		"casefold":     {{name: "Qw/a", body: "x"}, {name: "qw/b", body: "y"}},
		"collision":    {{name: "A.txt", body: "x"}, {name: "a.txt", body: "y"}},
		"file vs dir":  {{name: "a", body: "x"}, {name: "a/b", body: "y"}},
		"symlink":      {{name: "link", body: "target", mode: os.ModeSymlink | 0o777}},
		"bomb":         {{name: "big", body: strings.Repeat("0", 2<<20)}},
	}
	for name, entries := range cases {
		t.Run(name, func(t *testing.T) {
			plan, err := Open(build(t, entries...), DefaultLimits)
			if err == nil {
				plan.Close()
				t.Fatal("ZIP inseguro aceito")
			}
		})
	}
}

func TestMemberLimit(t *testing.T) {
	limits := DefaultLimits
	limits.MaxMembers = 1
	if _, err := Open(build(t, entry{name: "a", body: "1"}, entry{name: "b", body: "2"}), limits); err == nil {
		t.Fatal("limite de membros ignorado")
	}
}
