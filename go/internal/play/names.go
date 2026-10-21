package play

import (
	"crypto/rand"
	_ "embed"
	"encoding/json"
	"fmt"
	"math/big"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

//go:embed names.x86qw.json
var namesX86QW []byte

//go:embed names.one-piece.json
var namesOnePiece []byte

var groupID = regexp.MustCompile(`^[a-z0-9]+(?:-[a-z0-9]+)*$`)

func parseNames(payload []byte, profile, label string) ([]string, error) {
	var document map[string]any
	if err := json.Unmarshal(payload, &document); err != nil {
		return nil, fmt.Errorf("Invalid Frogbot name catalog: %s", label)
	}
	if format, _ := document["format"].(float64); format != 1 || document["game"] != "ktx" || document["profile"] != profile ||
		document["prefix"] != "/" || document["color"] != "quake-high-bit" {
		return nil, fmt.Errorf("Invalid Frogbot name catalog: %s", label)
	}
	var raw []any
	if profile == "personal" {
		characters, hasCharacters := document["characters"].([]any)
		names, hasNames := document["names"].([]any)
		switch {
		case hasCharacters && document["names"] == nil:
			raw = characters
		case hasNames && document["characters"] == nil:
			raw = names
		default:
			return nil, fmt.Errorf("Invalid personal Frogbot name list: %s", label)
		}
	} else {
		if document["theme"] != profile || document["randomize"] != true {
			return nil, fmt.Errorf("Invalid Frogbot name catalog: %s", label)
		}
		groups, _ := document["groups"].([]any)
		if len(groups) == 0 {
			return nil, fmt.Errorf("Empty Frogbot name catalog: %s", label)
		}
		seen := map[string]bool{}
		for _, item := range groups {
			group, _ := item.(map[string]any)
			id, _ := group["id"].(string)
			characters, _ := group["characters"].([]any)
			if !groupID.MatchString(id) || seen[id] || len(characters) == 0 {
				return nil, fmt.Errorf("Invalid Frogbot name group: %s", label)
			}
			seen[id] = true
			raw = append(raw, characters...)
		}
	}
	var names []string
	folded := map[string]bool{}
	for _, value := range raw {
		name := ""
		switch item := value.(type) {
		case string:
			if profile != "personal" {
				return nil, fmt.Errorf("Invalid Frogbot identity: %s", label)
			}
			name = item
		case map[string]any:
			if len(item) != 1 {
				return nil, fmt.Errorf("Invalid Frogbot identity: %s", label)
			}
			name, _ = item["name"].(string)
		default:
			return nil, fmt.Errorf("Invalid Frogbot identity: %s", label)
		}
		if !frogbotName.MatchString(name) {
			return nil, fmt.Errorf("Invalid Frogbot name in %s: use 1 to 13 ASCII characters.", label)
		}
		if folded[strings.ToLower(name)] {
			return nil, fmt.Errorf("Frogbot name list has duplicates: %s", label)
		}
		folded[strings.ToLower(name)] = true
		names = append(names, name)
	}
	return names, nil
}

func shuffle(names []string) []string {
	result := append([]string(nil), names...)
	for index := len(result) - 1; index > 0; index-- {
		pick, _ := rand.Int(rand.Reader, big.NewInt(int64(index+1)))
		other := int(pick.Int64())
		result[index], result[other] = result[other], result[index]
	}
	return result
}

// ResolveNames fills o.BotNamePool for the chosen --bot-names profile.
func ResolveNames(root string, game Game, o Options, mode *Mode) (Options, error) {
	count := RequestedNames(o, mode)
	if o.BotNamesProfile == "default" || count == 0 {
		o.BotNamePool = nil
		return o, nil
	}
	var pool []string
	var err error
	switch o.BotNamesProfile {
	case "x86qw":
		pool, err = parseNames(namesX86QW, "x86qw", "_x86qw/ktx-frogbot-names.json")
		pool = shuffle(pool)
	case "one-piece":
		pool, err = parseNames(namesOnePiece, "one-piece", "_x86qw/ktx-frogbot-names-one-piece.json")
		pool = shuffle(pool)
	case "personal":
		if game.BotNamesPersonalConfig == "" {
			return o, fmt.Errorf("%s declares no personal bot list.", game.Label)
		}
		path := filepath.Join(root, filepath.FromSlash(game.BotNamesPersonalConfig))
		if !regularFile(path) {
			return o, fmt.Errorf("Personal Frogbot name list missing or unsafe: %s. Run repair or edit the file the bootstrap created.", path)
		}
		payload, readErr := os.ReadFile(path)
		if readErr != nil {
			return o, fmt.Errorf("Invalid personal Frogbot name list: %s", path)
		}
		pool, err = parseNames(payload, "personal", path)
	default:
		return o, fmt.Errorf("Unknown Frogbot name profile: %s", o.BotNamesProfile)
	}
	if err != nil {
		return o, err
	}
	if len(pool) < count {
		return o, fmt.Errorf("Profile %s has %d name(s), but this launch can create %d Frogbot(s).", o.BotNamesProfile, len(pool), count)
	}
	o.BotNamePool = pool
	return o, nil
}
