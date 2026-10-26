// Package play builds local matches like `x86qw play`: game and KTX mode
// catalogs, map discovery, the KTX command plan and the ezQuake arguments.
package play

import (
	_ "embed"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/install"
)

// KtxModesJSON is dist/mods/ktx/1.47/x86qw/catalog/modes.json.
//
//go:embed ktx-modes.json
var KtxModesJSON []byte

// ContextKeys are the KTX keys whose aliases are reset on every launch.
var ContextKeys = []string{"F5", "F6", "H", "I", "M", "X", "Z", "F11"}

// Game is a games.json entry; install owns the model.
type Game = install.Game

// LoadGames parses the games catalog shipped with the CLI.
var LoadGames = install.LoadGames

// Requirement is one map asset a mode needs.
type Requirement struct {
	Key, Asset, When, Label string
}

// Binding is one context key.
type Binding struct{ Key, Command, Description string }

// Mode is one KTX mode.
type Mode struct {
	Key                string
	Aliases            []string
	Label              string
	RecommendedPlayers string
	Description        string
	Usermode           string
	DefaultMap         string
	SuggestedMaps      []string
	KeyBindings        []Binding
	LaunchSettings     [][2]string
	EntryConfig        string
	Requirements       []Requirement
	Bots               bool
	BotTeams           []string
}

// LoadModes parses the KTX mode catalog.
func LoadModes() ([]Mode, error) {
	var document struct {
		Format   int `json:"format"`
		Policies map[string]struct {
			Asset string `json:"asset"`
			When  string `json:"when"`
			Label string `json:"label"`
		} `json:"map_asset_policies"`
		Defaults struct {
			FrogbotMapPolicies []string `json:"frogbot_map_policies"`
		} `json:"defaults"`
		Modes []struct {
			ID                 string      `json:"id"`
			Aliases            []string    `json:"aliases"`
			Label              string      `json:"label"`
			RecommendedPlayers string      `json:"recommended_players"`
			Description        string      `json:"description"`
			Usermode           string      `json:"usermode"`
			DefaultMap         string      `json:"default_map"`
			SuggestedMaps      []string    `json:"suggested_maps"`
			KeyBindings        [][3]string `json:"key_bindings"`
			LaunchSettings     [][2]string `json:"launch_settings"`
			EntryConfig        *string     `json:"entry_config"`
			MapPolicies        []string    `json:"map_policies"`
			Bots               *bool       `json:"bots"`
			BotTeams           []string    `json:"bot_teams"`
		} `json:"modes"`
	}
	if err := json.Unmarshal(KtxModesJSON, &document); err != nil || document.Format != 1 {
		return nil, fmt.Errorf("invalid KTX mode catalog: %v", err)
	}
	requirement := func(key string) (Requirement, error) {
		policy, ok := document.Policies[key]
		if !ok {
			return Requirement{}, fmt.Errorf("unknown KTX map policy: %s", key)
		}
		label := policy.Label
		if english, ok := requirementLabels[key]; ok {
			label = english
		}
		return Requirement{Key: key, Asset: policy.Asset, When: policy.When, Label: label}, nil
	}
	var modes []Mode
	for _, raw := range document.Modes {
		mode := Mode{
			Key: raw.ID, Aliases: raw.Aliases, Label: raw.Label, RecommendedPlayers: raw.RecommendedPlayers, Description: raw.Description,
			Usermode: raw.Usermode, DefaultMap: raw.DefaultMap, SuggestedMaps: raw.SuggestedMaps,
			LaunchSettings: raw.LaunchSettings, Bots: raw.Bots == nil || *raw.Bots, BotTeams: raw.BotTeams,
		}
		if raw.EntryConfig != nil {
			mode.EntryConfig = *raw.EntryConfig
		}
		for _, binding := range raw.KeyBindings {
			mode.KeyBindings = append(mode.KeyBindings, Binding{binding[0], binding[1], binding[2]})
		}
		var keys []string
		if mode.Bots {
			keys = append(keys, document.Defaults.FrogbotMapPolicies...)
		}
		keys = append(keys, raw.MapPolicies...)
		for _, key := range keys {
			item, err := requirement(key)
			if err != nil {
				return nil, err
			}
			mode.Requirements = append(mode.Requirements, item)
		}
		modes = append(modes, mode)
	}
	return modes, nil
}

// FindMode resolves --mode by id or alias, case-insensitively.
func FindMode(modes []Mode, requested string) (Mode, error) {
	answer := strings.ToLower(requested)
	var matches []Mode
	for _, mode := range modes {
		hit := answer == strings.ToLower(mode.Key)
		for _, alias := range mode.Aliases {
			hit = hit || answer == strings.ToLower(alias)
		}
		if hit {
			matches = append(matches, mode)
		}
	}
	if len(matches) == 1 {
		return matches[0], nil
	}
	keys := make([]string, len(modes))
	for index, mode := range modes {
		keys[index] = mode.Key
	}
	return Mode{}, fmt.Errorf("Unknown KTX mode: %s. Available: %s.", requested, strings.Join(keys, ", "))
}

// requirementLabels name the map assets KTX modes need; the signed
// modes.json names them in Portuguese.
var requirementLabels = map[string]string{
	"frogbot-route": "Frogbot route",
	"ctf-entities":  "CTF flag entities",
	"race-route":    "Race route",
}

// MenuGroup is one entry of modes.json menu_groups.
type MenuGroup struct {
	Key, Label, Description string
	Modes                   []string
}

// LoadMenuGroups parses the KTX mode groups shown by the mode picker.
func LoadMenuGroups() ([]MenuGroup, error) {
	var document struct {
		Groups []struct {
			ID          string   `json:"id"`
			Label       string   `json:"label"`
			Description string   `json:"description"`
			Modes       []string `json:"modes"`
		} `json:"menu_groups"`
	}
	if err := json.Unmarshal(KtxModesJSON, &document); err != nil {
		return nil, fmt.Errorf("invalid KTX mode catalog: %v", err)
	}
	groups := make([]MenuGroup, len(document.Groups))
	for index, raw := range document.Groups {
		groups[index] = MenuGroup{Key: raw.ID, Label: raw.Label, Description: raw.Description, Modes: raw.Modes}
	}
	return groups, nil
}
