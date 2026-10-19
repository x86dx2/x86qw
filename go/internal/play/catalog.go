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

// Game is the launch-relevant part of a games.json entry.
type Game struct {
	ID                           string      `json:"id"`
	Label                        string      `json:"label"`
	Component                    string      `json:"component"`
	Gamedir                      string      `json:"gamedir"`
	Marker                       string      `json:"marker"`
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
	Description                  string      `json:"description"`
	PersonalConfig               string      `json:"personal_config"`
	DedicatedArguments           []string    `json:"dedicated_arguments"`
	DedicatedSettings            [][2]string `json:"dedicated_settings"`
}

// UsesModeCatalog reports whether the game is played through KTX modes.
func (g Game) UsesModeCatalog() bool { return g.ModeCatalog != "" }

// LoadGames parses the games catalog shipped with the CLI.
func LoadGames() ([]Game, error) {
	var document struct {
		Games []Game `json:"games"`
	}
	if err := json.Unmarshal(install.GamesJSON, &document); err != nil {
		return nil, err
	}
	return document.Games, nil
}

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
		return nil, fmt.Errorf("catálogo de modos KTX inválido: %v", err)
	}
	requirement := func(key string) (Requirement, error) {
		policy, ok := document.Policies[key]
		if !ok {
			return Requirement{}, fmt.Errorf("política de mapa KTX desconhecida: %s", key)
		}
		return Requirement{Key: key, Asset: policy.Asset, When: policy.When, Label: policy.Label}, nil
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
	return Mode{}, fmt.Errorf("Modo KTX desconhecido: %s. Disponíveis: %s.", requested, strings.Join(keys, ", "))
}

func errorf(format string, args ...any) error { return fmt.Errorf(format, args...) }
