package install

import (
	"crypto/sha256"
	_ "embed"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/archive"
	"github.com/x86dx2/x86qw/go/internal/receipt"
)

// GamesJSON is maintenance/inventory/games.json (kept identical by a test).
//
//go:embed games.json
var GamesJSON []byte

// playSupportVersion is the version of the play-support files.
const playSupportVersion = "8"

// Game is one entry of games.json: what installs it, what play and host
// pass to ezQuake and MVDSV, and its play-support files.
type Game struct {
	ID                           string      `json:"id"`
	Label                        string      `json:"label"`
	Description                  string      `json:"description"`
	Component                    string      `json:"component"`
	Version                      string      `json:"version"`
	Gamedir                      string      `json:"gamedir"`
	Gamecode                     string      `json:"gamecode"`
	Marker                       string      `json:"marker"`
	PlaySupportGamecode          string      `json:"play_support_gamecode"`
	PersonalConfig               string      `json:"personal_config"`
	LegacyMarker                 string      `json:"legacy_marker"`
	LegacyComponents             []string    `json:"legacy_components"`
	DefaultMap                   string      `json:"default_map"`
	SuggestedMaps                []string    `json:"suggested_maps"`
	PreMapArguments              []string    `json:"pre_map_arguments"`
	PostMapArguments             []string    `json:"post_map_arguments"`
	LocalServerSettings          [][2]string `json:"local_server_settings"`
	LegacyRemoteCapabilities     []string    `json:"legacy_remote_capabilities"`
	ClientGameArguments          []string    `json:"client_game_arguments"`
	PreConnectArguments          []string    `json:"pre_connect_arguments"`
	ClientCompatibilityArguments []string    `json:"client_compatibility_arguments"`
	ModeCatalog                  string      `json:"mode_catalog"`
	BotNamesPersonalConfig       string      `json:"bot_names_personal_config"`
	DedicatedArguments           []string    `json:"dedicated_arguments"`
	DedicatedSettings            [][2]string `json:"dedicated_settings"`
}

// UsesModeCatalog reports whether the game is played through KTX modes.
func (g Game) UsesModeCatalog() bool { return g.ModeCatalog != "" }

// LoadGames parses the embedded games catalog.
func LoadGames() ([]Game, error) {
	var document struct {
		Games []Game `json:"games"`
	}
	if err := json.Unmarshal(GamesJSON, &document); err != nil {
		return nil, err
	}
	return document.Games, nil
}

func regular(name string) bool {
	info, err := os.Lstat(name)
	return err == nil && info.Mode().IsRegular()
}

func hasRecord(root, id string) bool {
	_, err := os.Stat(filepath.Join(root, receipt.ComponentsDir, id, "receipt"))
	return err == nil
}

// AvailableGames lists games whose component is recorded and whose marker
// exists (gameplay.available_local_games).
func AvailableGames(root string, games []Game) []Game {
	var available []Game
	for _, game := range games {
		installed := hasRecord(root, game.Component)
		for _, legacy := range game.LegacyComponents {
			installed = installed || hasRecord(root, legacy)
		}
		marker := filepath.Join(root, filepath.FromSlash(game.Marker))
		if !regular(marker) && game.LegacyMarker != "" {
			if legacy := filepath.Join(root, filepath.FromSlash(game.LegacyMarker)); regular(legacy) {
				marker = legacy
			}
		}
		if installed && regular(marker) {
			available = append(available, game)
		}
	}
	return available
}

// InstalledComponent is the game's component, else a recorded legacy
// component, else "".
func InstalledComponent(root string, game Game) string {
	if hasRecord(root, game.Component) {
		return game.Component
	}
	for _, legacy := range game.LegacyComponents {
		if hasRecord(root, legacy) {
			return legacy
		}
	}
	return ""
}

var numericVersion = regexp.MustCompile(`^\d+(?:\.\d+)+`)

// InstalledVersion is the numeric prefix of the component receipt's
// selection, else the catalog version.
func InstalledVersion(root string, game Game) string {
	component := InstalledComponent(root, game)
	if component == "" {
		return game.Version
	}
	record, err := receipt.Load(filepath.Join(root, receipt.ComponentsDir, component))
	if err != nil {
		return game.Version
	}
	if match := numericVersion.FindString(record.Receipt.Get("selection")); match != "" {
		return match
	}
	return game.Version
}

// ExpectedPlaySupport returns the derived gamecode files: each game's
// qwprogs.dat copied to its play_support_gamecode path.
func ExpectedPlaySupport(root string, games []Game) (map[string][]byte, error) {
	expected := map[string][]byte{}
	for _, game := range games {
		if game.PlaySupportGamecode == "" {
			continue
		}
		program := filepath.Join(root, filepath.FromSlash(game.Gamecode))
		if !regular(program) && game.LegacyMarker != "" {
			if legacy := filepath.Join(root, filepath.FromSlash(game.LegacyMarker)); regular(legacy) {
				program = legacy
			}
		}
		if !regular(program) {
			return nil, fmt.Errorf("local gamecode not found: %s", program)
		}
		payload, err := gameProgram(program)
		if err != nil {
			return nil, err
		}
		expected[game.PlaySupportGamecode] = payload
	}
	return expected, nil
}

func gameProgram(program string) ([]byte, error) {
	switch strings.ToLower(filepath.Ext(program)) {
	case ".dat":
		return os.ReadFile(program)
	case ".pk3":
		plan, err := archive.Open(program, archive.DefaultLimits)
		if err != nil {
			return nil, fmt.Errorf("gamecode qwprogs.dat not found in %s: %w", program, err)
		}
		defer plan.Close()
		payload, err := plan.ReadMember("qwprogs.dat")
		if err != nil {
			return nil, fmt.Errorf("gamecode qwprogs.dat not found in %s", program)
		}
		return payload, nil
	case ".pak":
		return pakMember(program, "qwprogs.dat")
	}
	return nil, fmt.Errorf("unsupported local gamecode format: %s", program)
}

type pakEntry struct {
	name           string
	position, size int64
}

func pakDirectory(file *os.File, name string) ([]pakEntry, error) {
	info, err := file.Stat()
	if err != nil {
		return nil, err
	}
	header := make([]byte, 12)
	if _, err := file.ReadAt(header, 0); err != nil || string(header[:4]) != "PACK" {
		return nil, fmt.Errorf("invalid PAK: %s", name)
	}
	offset := int64(binary.LittleEndian.Uint32(header[4:8]))
	length := int64(binary.LittleEndian.Uint32(header[8:12]))
	if length%64 != 0 || offset+length > info.Size() || length/64 > 4096 {
		return nil, fmt.Errorf("invalid PAK: %s", name)
	}
	directory := make([]byte, length)
	if _, err := file.ReadAt(directory, offset); err != nil {
		return nil, err
	}
	var entries []pakEntry
	for index := int64(0); index < length; index += 64 {
		entry := directory[index : index+64]
		entryName := string(entry[:56])
		if cut := strings.IndexByte(entryName, 0); cut >= 0 {
			entryName = entryName[:cut]
		}
		position := int64(binary.LittleEndian.Uint32(entry[56:60]))
		size := int64(binary.LittleEndian.Uint32(entry[60:64]))
		if position+size > info.Size() {
			return nil, fmt.Errorf("PAK member out of bounds: %s", entryName)
		}
		entries = append(entries, pakEntry{entryName, position, size})
	}
	return entries, nil
}

// PakNames lists the member names of a Quake PAK.
func PakNames(name string) ([]string, error) {
	file, err := os.Open(name)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	entries, err := pakDirectory(file, name)
	if err != nil {
		return nil, err
	}
	names := make([]string, len(entries))
	for index, entry := range entries {
		names[index] = entry.name
	}
	return names, nil
}

// pakMember reads one member of a Quake PAK (case-insensitive exact name).
func pakMember(name, member string) ([]byte, error) {
	file, err := os.Open(name)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	entries, err := pakDirectory(file, name)
	if err != nil {
		return nil, err
	}
	for _, entry := range entries {
		if !strings.EqualFold(entry.name, member) {
			continue
		}
		payload := make([]byte, entry.size)
		if _, err := file.ReadAt(payload, entry.position); err != nil {
			return nil, err
		}
		return payload, nil
	}
	return nil, fmt.Errorf("member %s missing from PAK %s", member, name)
}

// PlaySupportIssues ports gameplay.local_play_support_issues.
func PlaySupportIssues(root string, games []Game) ([]string, map[string][]byte, error) {
	expected, err := ExpectedPlaySupport(root, games)
	if err != nil {
		return nil, nil, err
	}
	recorded := map[string]string{}
	if record, err := receipt.Load(filepath.Join(root, receipt.ComponentsDir, "play-support")); err == nil {
		for _, entry := range record.Inventory.Entries {
			recorded[entry.Path] = entry.SHA256
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return nil, nil, err
	}
	var issues []string
	same := len(recorded) == len(expected)
	for relative, payload := range expected {
		digest := sha256.Sum256(payload)
		if recorded[relative] != hex.EncodeToString(digest[:]) {
			same = false
		}
	}
	if !same {
		issues = append(issues, "play-support receipt missing or different")
	}
	for relative, payload := range expected {
		digest := sha256.Sum256(payload)
		actual, err := fileSHA256(filepath.Join(root, filepath.FromSlash(relative)))
		if err != nil || !regular(filepath.Join(root, filepath.FromSlash(relative))) || actual != hex.EncodeToString(digest[:]) {
			issues = append(issues, "derived gamecode missing or different: "+relative)
		}
	}
	for _, game := range games {
		if !regular(filepath.Join(root, filepath.FromSlash(game.PersonalConfig))) {
			issues = append(issues, "personal settings missing: "+game.PersonalConfig)
		}
	}
	return issues, expected, nil
}
