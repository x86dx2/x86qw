package maint

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

func TestFavoritesAreNormalizedNewestFirstAndUnique(t *testing.T) {
	root := t.TempDir()
	now := time.Date(2026, 10, 21, 12, 0, 0, 0, time.UTC)
	for _, address := range []string{"QW.Example.COM:27500", "outro.example:28501", "qw.example.com:27500"} {
		if err := AddFavorite(root, address, now); err != nil {
			t.Fatal(err)
		}
	}
	library, err := LoadLibrary(root)
	if err != nil {
		t.Fatal(err)
	}
	var addresses []string
	for _, entry := range library.Favorites {
		addresses = append(addresses, entry.Address)
	}
	if strings.Join(addresses, ",") != "qw.example.com:27500,outro.example:28501" {
		t.Fatalf("favoritos: %v", addresses)
	}
	if entry := library.Favorites[0]; entry.Origin != "user" || entry.Title != entry.Address || entry.Freshness != "2026-10-21T12:00:00Z" {
		t.Fatalf("entrada: %+v", entry)
	}
	if err := RemoveFavorite(root, "OUTRO.example:28501"); err != nil {
		t.Fatal(err)
	}
	if err := RemoveFavorite(root, "outro.example:28501"); err == nil {
		t.Fatal("remover de novo deveria dizer que não existe")
	}
	rendered := RenderLibrary(&Library{Favorites: library.Favorites[:1]})
	if !strings.Contains(rendered, "favoritos: 1\n  qw.example.com:27500") || !strings.Contains(rendered, "recentes: 0") {
		t.Fatalf("relatório: %s", rendered)
	}
}

func TestFavoritesStopAtTheLimit(t *testing.T) {
	root := t.TempDir()
	now := time.Now()
	for index := 0; index < maxFavorites; index++ {
		if err := AddFavorite(root, fmt.Sprintf("servidor%d.example:27500", index), now); err != nil {
			t.Fatal(err)
		}
	}
	if err := AddFavorite(root, "mais-um.example:27500", now); err == nil || !strings.Contains(err.Error(), "cheia") {
		t.Fatalf("o 21º favorito deveria ser recusado: %v", err)
	}
	// Re-adding one already there only moves it to the top.
	if err := AddFavorite(root, "servidor3.example:27500", now); err != nil {
		t.Fatal(err)
	}
}

func TestLibraryRejectsBadAddressesAndDocuments(t *testing.T) {
	root := t.TempDir()
	for _, address := range []string{"semporta.example", "host:0", "host:70000", "host :27500", "http://host:27500"} {
		if err := AddFavorite(root, address, time.Now()); err == nil {
			t.Errorf("%q aceito", address)
		}
	}
	path := filepath.Join(root, filepath.FromSlash(LibraryPath))
	for name, body := range map[string]string{
		"json":   "{",
		"kind":   `{"format": 1, "kind": "profile", "favorites": [], "recents": []}`,
		"extra":  `{"format": 1, "kind": "library", "favorites": [], "recents": [], "x": 1}`,
		"entry":  `{"format": 1, "kind": "library", "favorites": [{"address": "a.example:1"}], "recents": []}`,
		"fresh":  `{"format": 1, "kind": "library", "favorites": [{"address": "a.example:1", "title": "a", "origin": "user", "freshness": "ontem"}], "recents": []}`,
		"origin": `{"format": 1, "kind": "library", "favorites": [{"address": "a.example:1", "title": "a", "origin": "hacker", "freshness": "2026-01-01T00:00:00Z"}], "recents": []}`,
	} {
		write(t, path, body)
		if _, err := LoadLibrary(root); err == nil {
			t.Errorf("%s: biblioteca inválida aceita", name)
		}
	}
}

func TestLibraryRefusesASymlink(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("links simbólicos exigem privilégio no Windows")
	}
	root := t.TempDir()
	outside := filepath.Join(t.TempDir(), "fora.json")
	write(t, outside, `{"format": 1, "kind": "library", "favorites": [], "recents": []}`)
	path := filepath.Join(root, filepath.FromSlash(LibraryPath))
	os.MkdirAll(filepath.Dir(path), 0o755)
	if err := os.Symlink(outside, path); err != nil {
		t.Fatal(err)
	}
	if err := AddFavorite(root, "a.example:27500", time.Now()); err == nil {
		t.Fatal("escrita através de link aceita")
	}
	if !strings.Contains(read(t, outside), `"favorites": []`) {
		t.Fatal("o arquivo de fora foi alterado")
	}
}
