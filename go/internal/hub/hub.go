// Package hub lists public QuakeWorld servers like `x86qw hub`, falling back
// to the local favorites and recents library when the Hub is unavailable.
package hub

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode"
)

const (
	// API is the Hub endpoint the Python CLI queries.
	API         = "https://hubapi.quakeworld.nu/v2/servers/mvdsv?empty=exclude&limit=20"
	maxBytes    = 1 << 20
	maxServers  = 20
	libraryPath = "qw/x86qw-library.json"
)

var (
	addressPattern = regexp.MustCompile(`^[A-Za-z0-9_.:\[\]-]+:[0-9]{1,5}$`)
	qtvPattern     = regexp.MustCompile(`^[0-9]+@[A-Za-z0-9_.:\[\]-]+:[0-9]{1,5}$`)
)

// Players counts humans and bots.
type Players struct {
	Bots   int `json:"bots"`
	Humans int `json:"humans"`
}

// Server is the closed public projection of a Hub row.
type Server struct {
	Address   string  `json:"address"`
	Map       string  `json:"map"`
	Mode      string  `json:"mode"`
	Players   Players `json:"players"`
	QTVStream *string `json:"qtv_stream"`
	Title     string  `json:"title"`
}

// Fetch downloads the raw Hub rows.
func Fetch(ctx context.Context, client *http.Client) ([]any, error) {
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, API, nil)
	if err != nil {
		return nil, err
	}
	request.Header.Set("User-Agent", "x86-qw-installer/1")
	response, err := client.Do(request)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("Hub respondeu HTTP %d", response.StatusCode)
	}
	payload, err := io.ReadAll(io.LimitReader(response.Body, maxBytes+1))
	if err != nil || len(payload) > maxBytes {
		return nil, errors.New("resposta do Hub inválida ou grande demais")
	}
	var rows []any
	if err := json.Unmarshal(payload, &rows); err != nil {
		return nil, err
	}
	return rows, nil
}

func validAddress(address string) bool {
	if !addressPattern.MatchString(address) {
		return false
	}
	port, err := strconv.Atoi(address[strings.LastIndex(address, ":")+1:])
	return err == nil && port >= 1 && port <= 65535
}

// Library returns local favorites then recents as Hub-like rows.
func Library(root string) ([]any, error) {
	payload, err := os.ReadFile(filepath.Join(root, libraryPath))
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var document struct {
		Format    int                 `json:"format"`
		Kind      string              `json:"kind"`
		Favorites []map[string]string `json:"favorites"`
		Recents   []map[string]string `json:"recents"`
	}
	if err := json.Unmarshal(payload, &document); err != nil || document.Format != 1 || document.Kind != "library" {
		return nil, errors.New("biblioteca local inválida")
	}
	seen := map[string]bool{}
	var rows []any
	for _, entry := range append(document.Favorites, document.Recents...) {
		address := entry["address"]
		if seen[address] || !validAddress(address) {
			continue
		}
		seen[address] = true
		rows = append(rows, map[string]any{
			"address":  address,
			"title":    entry["title"],
			"mode":     "-",
			"settings": map[string]any{"hostname": entry["title"], "mode": "-", "map": "-"},
			"players":  []any{},
		})
	}
	return rows, nil
}

func text(fallback string, values ...any) string {
	for _, value := range values {
		raw, ok := value.(string)
		if !ok {
			continue
		}
		cleaned := strings.Map(func(r rune) rune {
			if !unicode.IsPrint(r) || r == unicode.ReplacementChar {
				return '?'
			}
			return r
		}, raw)
		cleaned = strings.TrimSpace(cleaned)
		if cleaned != "" {
			if len([]rune(cleaned)) > 4096 {
				cleaned = string([]rune(cleaned)[:4096])
			}
			return cleaned
		}
	}
	return fallback
}

// Project maps raw rows to the public shape, deduplicated and sorted by address.
func Project(rows []any) ([]Server, error) {
	seen := map[string]bool{}
	var servers []Server
	for _, raw := range rows {
		row, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		address, _ := row["address"].(string)
		if !validAddress(address) || seen[address] {
			continue
		}
		settings, _ := row["settings"].(map[string]any)
		if settings == nil {
			settings = map[string]any{}
		}
		var players Players
		switch value := row["players"].(type) {
		case []any:
			for _, player := range value {
				if entry, ok := player.(map[string]any); ok {
					if bot, _ := entry["is_bot"].(bool); bot {
						players.Bots++
					} else {
						players.Humans++
					}
				}
			}
		case map[string]any:
			if humans, ok := value["humans"].(float64); ok && humans >= 0 && humans == float64(int(humans)) {
				players.Humans = int(humans)
			}
			if bots, ok := value["bots"].(float64); ok && bots >= 0 && bots == float64(int(bots)) {
				players.Bots = int(bots)
			}
		}
		var stream *string
		rawQTV := row["qtv_stream"]
		if nested, ok := rawQTV.(map[string]any); ok {
			rawQTV = nested["url"]
			if rawQTV == nil || rawQTV == "" {
				rawQTV = nested["stream"]
			}
		}
		if value, ok := rawQTV.(string); ok && qtvPattern.MatchString(value) {
			stream = &value
		}
		servers = append(servers, Server{
			Address:   address,
			Title:     text(address, settings["hostname"], row["title"], row["name"]),
			Mode:      text("-", row["mode"], settings["mode"]),
			Map:       text("-", settings["map"], row["map"]),
			Players:   players,
			QTVStream: stream,
		})
		seen[address] = true
	}
	if len(servers) == 0 {
		return nil, errors.New("Nenhum servidor ativo reconhecido foi retornado pelo Hub.")
	}
	sort.Slice(servers, func(a, b int) bool { return servers[a].Address < servers[b].Address })
	if len(servers) > maxServers {
		servers = servers[:maxServers]
	}
	return servers, nil
}

// Servers fetches the Hub and falls back to the local library.
func Servers(ctx context.Context, root string) ([]Server, error) {
	rows, err := Fetch(ctx, &http.Client{})
	valid := 0
	for _, raw := range rows {
		if row, ok := raw.(map[string]any); ok {
			if address, _ := row["address"].(string); validAddress(address) {
				valid++
			}
		}
	}
	if err != nil || valid == 0 {
		if rows, err = Library(root); err != nil {
			return nil, err
		}
		if len(rows) == 0 {
			return nil, errors.New("Hub indisponível e não há favoritos ou recentes locais.")
		}
	}
	return Project(rows)
}
