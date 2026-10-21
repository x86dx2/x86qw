// Package maint holds the maintenance commands: library, profile, ui,
// changes, cleanup, uninstall/purge and migrate.
package maint

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/x86dx2/x86qw/go/internal/atomicfile"
	"github.com/x86dx2/x86qw/go/internal/pyjson"
)

// The library keeps favorite and recent servers in qw/x86qw-library.json.

const (
	LibraryPath  = "qw/x86qw-library.json"
	maxFavorites = 20
	maxRecents   = 20
)

var (
	addressPattern = regexp.MustCompile(`^[A-Za-z0-9_.:\[\]-]+:[0-9]{1,5}$`)
	origins        = map[string]bool{"user": true, "hub": true, "local": true}
)

// LibraryEntry is one favorite or recent.
type LibraryEntry struct {
	Address, Title, Origin, Freshness string
}

// Library holds favorites and recents.
type Library struct {
	Favorites []LibraryEntry
	Recents   []LibraryEntry
}

// NormalizeAddress lowercases the host and validates the port.
func NormalizeAddress(value string) (string, error) {
	if !addressPattern.MatchString(value) {
		return "", fmt.Errorf("invalid server address: %q", value)
	}
	cut := strings.LastIndex(value, ":")
	host, portText := value[:cut], value[cut+1:]
	port, _ := strconv.Atoi(portText)
	if port < 1 || port > 65535 {
		return "", fmt.Errorf("invalid server port: %q", value)
	}
	if strings.HasPrefix(host, "[") && strings.HasSuffix(host, "]") {
		return fmt.Sprintf("%s:%d", host, port), nil
	}
	return fmt.Sprintf("%s:%d", strings.ToLower(host), port), nil
}

func cleanTitle(value, fallback string) string {
	cleaned := strings.Map(func(r rune) rune {
		if !unicode.IsPrint(r) && r != ' ' || r == unicode.ReplacementChar {
			return '?'
		}
		return r
	}, value)
	cleaned = strings.TrimSpace(cleaned)
	if runes := []rune(cleaned); len(runes) > 4096 {
		cleaned = string(runes[:4096])
	}
	if cleaned == "" {
		return fallback
	}
	return cleaned
}

func newEntry(address, title, origin string, now time.Time) (LibraryEntry, error) {
	if !origins[origin] {
		return LibraryEntry{}, fmt.Errorf("invalid origin in the library: %q", origin)
	}
	normalized, err := NormalizeAddress(address)
	if err != nil {
		return LibraryEntry{}, err
	}
	return LibraryEntry{
		Address: normalized, Title: cleanTitle(title, normalized), Origin: origin,
		Freshness: now.UTC().Format("2006-01-02T15:04:05Z"),
	}, nil
}

// LoadLibrary reads qw/x86qw-library.json without creating it.
func LoadLibrary(target string) (*Library, error) {
	path := filepath.Join(target, filepath.FromSlash(LibraryPath))
	if info, err := os.Lstat(path); err == nil && info.Mode()&os.ModeSymlink != 0 {
		return nil, errors.New("the library is a symlink")
	}
	payload, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return &Library{}, nil
	}
	if err != nil {
		return nil, err
	}
	var document map[string]any
	if err := json.Unmarshal(payload, &document); err != nil {
		return nil, errors.New("the library is not valid JSON")
	}
	if format, _ := document["format"].(float64); format != 1 || document["kind"] != "library" {
		return nil, errors.New("the library has an invalid header")
	}
	if len(document) != 4 || document["favorites"] == nil || document["recents"] == nil {
		return nil, errors.New("the library has missing or extra fields")
	}
	library := &Library{}
	if library.Favorites, err = parseEntries(document["favorites"], "favorites", maxFavorites); err != nil {
		return nil, err
	}
	if library.Recents, err = parseEntries(document["recents"], "recents", maxRecents); err != nil {
		return nil, err
	}
	return library, nil
}

func parseEntries(value any, label string, limit int) ([]LibraryEntry, error) {
	items, ok := value.([]any)
	if !ok || len(items) > limit {
		return nil, fmt.Errorf("the library's %s list is invalid", label)
	}
	seen := map[string]bool{}
	var entries []LibraryEntry
	for _, raw := range items {
		item, ok := raw.(map[string]any)
		if !ok || len(item) != 4 {
			return nil, fmt.Errorf("an item in the library's %s list is invalid", label)
		}
		fields := map[string]string{}
		for _, key := range []string{"address", "title", "origin", "freshness"} {
			value, present := item[key]
			if !present {
				return nil, fmt.Errorf("an item in the library's %s list is invalid", label)
			}
			fields[key] = fmt.Sprint(value)
		}
		if !strings.HasSuffix(fields["freshness"], "Z") {
			return nil, errors.New("a date in the library is invalid")
		}
		when, err := time.Parse("2006-01-02T15:04:05Z", fields["freshness"])
		if err != nil {
			return nil, errors.New("a date in the library is invalid")
		}
		entry, err := newEntry(fields["address"], fields["title"], fields["origin"], when)
		if err != nil {
			return nil, err
		}
		if seen[entry.Address] {
			return nil, fmt.Errorf("library %s contains duplicate addresses", label)
		}
		seen[entry.Address] = true
		entries = append(entries, entry)
	}
	return entries, nil
}

func entryMaps(entries []LibraryEntry) []any {
	items := make([]any, len(entries))
	for index, entry := range entries {
		items[index] = map[string]any{"address": entry.Address, "title": entry.Title, "origin": entry.Origin, "freshness": entry.Freshness}
	}
	return items
}

func writeLibrary(target string, library *Library) error {
	destination := filepath.Join(target, filepath.FromSlash(LibraryPath))
	if info, err := os.Lstat(destination); err == nil && info.Mode()&os.ModeSymlink != 0 {
		return errors.New("the library is a symlink")
	}
	encoded, err := pyjson.Dumps(map[string]any{
		"format": 1, "kind": "library", "favorites": entryMaps(library.Favorites), "recents": entryMaps(library.Recents),
	}, -1)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(destination), 0o755); err != nil {
		return err
	}
	return atomicfile.Write(destination, []byte(encoded+"\n"), 0o644)
}

// AddFavorite pins a server (newest first, at most 20).
func AddFavorite(target, address string, now time.Time) error {
	entry, err := newEntry(address, "", "user", now)
	if err != nil {
		return err
	}
	library, err := LoadLibrary(target)
	if err != nil {
		return err
	}
	favorites := []LibraryEntry{entry}
	for _, item := range library.Favorites {
		if item.Address != entry.Address {
			favorites = append(favorites, item)
		}
	}
	if len(favorites) > maxFavorites {
		return errors.New("the favorites list is full")
	}
	library.Favorites = favorites
	return writeLibrary(target, library)
}

// RemoveFavorite unpins a server.
func RemoveFavorite(target, address string) error {
	normalized, err := NormalizeAddress(address)
	if err != nil {
		return err
	}
	library, err := LoadLibrary(target)
	if err != nil {
		return err
	}
	var favorites []LibraryEntry
	for _, item := range library.Favorites {
		if item.Address != normalized {
			favorites = append(favorites, item)
		}
	}
	if len(favorites) == len(library.Favorites) {
		return fmt.Errorf("favorite not found: %s", normalized)
	}
	library.Favorites = favorites
	return writeLibrary(target, library)
}

// RenderLibrary is the library as `x86qw library` prints it.
func RenderLibrary(library *Library) string {
	lines := []string{"x86QW · library", ""}
	for _, group := range []struct {
		key     string
		entries []LibraryEntry
	}{{"favorites", library.Favorites}, {"recents", library.Recents}} {
		lines = append(lines, fmt.Sprintf("%s: %d", group.key, len(group.entries)))
		for _, item := range group.entries {
			lines = append(lines, fmt.Sprintf("  %s  %s  %s  %s", item.Address, item.Title, item.Origin, item.Freshness))
		}
	}
	return strings.Join(lines, "\n") + "\n"
}
