package install

import (
	"crypto/sha256"
	_ "embed"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/archive"
	"github.com/x86dx2/x86qw/go/internal/receipt"
)

// GamesJSON is maintenance/inventory/games.json (kept identical by a test).
//
//go:embed games.json
var GamesJSON []byte

// playSupportVersion mirrors PLAY_SUPPORT_VERSION in gameplay.py.
const playSupportVersion = "8"

// Game is the subset of a local game spec play-support needs.
type Game struct {
	ID                  string   `json:"id"`
	Label               string   `json:"label"`
	Component           string   `json:"component"`
	Marker              string   `json:"marker"`
	Gamecode            string   `json:"gamecode"`
	PlaySupportGamecode string   `json:"play_support_gamecode"`
	PersonalConfig      string   `json:"personal_config"`
	LegacyMarker        string   `json:"legacy_marker"`
	LegacyComponents    []string `json:"legacy_components"`
}

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
			return nil, fmt.Errorf("gamecode local não encontrado: %s", program)
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
			return nil, fmt.Errorf("gamecode qwprogs.dat não encontrado em %s: %w", program, err)
		}
		defer plan.Close()
		payload, err := plan.ReadMember("qwprogs.dat")
		if err != nil {
			return nil, fmt.Errorf("gamecode qwprogs.dat não encontrado em %s", program)
		}
		return payload, nil
	case ".pak":
		return pakMember(program, "qwprogs.dat")
	}
	return nil, fmt.Errorf("formato de gamecode local não suportado: %s", program)
}

// pakMember reads one member of a Quake PAK.
func pakMember(name, member string) ([]byte, error) {
	file, err := os.Open(name)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return nil, err
	}
	header := make([]byte, 12)
	if _, err := io.ReadFull(file, header); err != nil || string(header[:4]) != "PACK" {
		return nil, fmt.Errorf("PAK inválido: %s", name)
	}
	offset := int64(binary.LittleEndian.Uint32(header[4:8]))
	length := int64(binary.LittleEndian.Uint32(header[8:12]))
	if length%64 != 0 || offset+length > info.Size() || length/64 > 4096 {
		return nil, fmt.Errorf("diretório PAK inválido: %s", name)
	}
	directory := make([]byte, length)
	if _, err := file.ReadAt(directory, offset); err != nil {
		return nil, err
	}
	for index := int64(0); index < length; index += 64 {
		entry := directory[index : index+64]
		entryName := strings.TrimRight(string(entry[:56]), "\x00")
		if cut := strings.IndexByte(entryName, 0); cut >= 0 {
			entryName = entryName[:cut]
		}
		if entryName != member {
			continue
		}
		position := int64(binary.LittleEndian.Uint32(entry[56:60]))
		size := int64(binary.LittleEndian.Uint32(entry[60:64]))
		if position+size > info.Size() {
			return nil, fmt.Errorf("membro PAK fora dos limites: %s", member)
		}
		payload := make([]byte, size)
		if _, err := file.ReadAt(payload, position); err != nil {
			return nil, err
		}
		return payload, nil
	}
	return nil, fmt.Errorf("membro %s ausente do PAK %s", member, name)
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
		issues = append(issues, "recibo play-support ausente ou divergente")
	}
	for relative, payload := range expected {
		digest := sha256.Sum256(payload)
		actual, err := fileSHA256(filepath.Join(root, filepath.FromSlash(relative)))
		if err != nil || !regular(filepath.Join(root, filepath.FromSlash(relative))) || actual != hex.EncodeToString(digest[:]) {
			issues = append(issues, "gamecode derivado ausente ou divergente: "+relative)
		}
	}
	for _, game := range games {
		if !regular(filepath.Join(root, filepath.FromSlash(game.PersonalConfig))) {
			issues = append(issues, "configuração pessoal ausente: "+game.PersonalConfig)
		}
	}
	return issues, expected, nil
}
