package play

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/install"
)

// Request is a local match as asked for; what is empty takes its default:
// KTX when installed, the mode's default map, the saved ruleset and the
// stable client.
type Request struct {
	Game, Mode, Map, Ruleset, Channel string
	Options                           Options
}

// Choice is a match ready to launch, every precondition checked.
type Choice struct {
	Game      Game
	Mode      *Mode
	Map       string
	Options   Options
	Assets    map[string]bool
	Runtime   install.Runtime
	Ruleset   string
	Component string // the game's component, verified
	Files     int
	Selection string
}

// Installed lists the games whose component is installed under root, in
// catalog order.
func Installed(root string) ([]Game, error) {
	games, err := LoadGames()
	if err != nil {
		return nil, err
	}
	return install.AvailableGames(root, games), nil
}

// ClientRuleset is the ruleset ezQuake starts with at root: the saved one,
// else x86qw.
func ClientRuleset(root string) (string, error) {
	ruleset, err := SavedRuleset(root)
	if ruleset == "" && err == nil {
		ruleset = defaultRuleset
	}
	return ruleset, err
}

// SavedRuleset is the client ruleset recorded for root, "" when none is.
func SavedRuleset(root string) (string, error) {
	path := filepath.Join(root, ".x86qw", "launcher", "client-ruleset.json")
	payload, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return "", nil
	}
	if err != nil || len(payload) > 4096 {
		return "", fmt.Errorf("Preferência de ruleset inválida: %s", path)
	}
	var document map[string]any
	if json.Unmarshal(payload, &document) != nil || len(document) != 3 || document["project"] != "x86qw" {
		return "", fmt.Errorf("Preferência de ruleset inválida: %s", path)
	}
	format, _ := document["format"].(float64)
	ruleset, _ := document["ruleset"].(string)
	if format != 1 || Rulesets[ruleset] == "" {
		return "", fmt.Errorf("Preferência de ruleset inválida: %s", path)
	}
	return ruleset, nil
}

// MapList is what a match can be played on: compatible maps with the
// suggested ones first, and the one picked when nothing is asked for.
type MapList struct {
	Maps      []string
	Suggested map[string]bool
	Default   string
	// Requirements is true when the mode's options narrowed the list to
	// maps with the resources they need (bot routes, CTF entities…).
	Requirements bool
	Assets       map[string]bool
	Options      Options // with bot names resolved
}

// Maps lists the maps game (and mode, for KTX) can run with options.
func Maps(root string, game Game, mode *Mode, o Options) (*MapList, error) {
	label, defaultMap, suggested := game.Label, game.DefaultMap, game.SuggestedMaps
	var requirements []Requirement
	var assets map[string]bool
	if mode != nil {
		label = "KTX · " + mode.Label
		if mode.DefaultMap != "" {
			defaultMap = mode.DefaultMap
		}
		if len(mode.SuggestedMaps) > 0 {
			suggested = mode.SuggestedMaps
		}
		var err error
		if o, err = ResolveNames(root, game, o, mode); err != nil {
			return nil, err
		}
		requirements = ActiveRequirements(*mode, o)
		assets = map[string]bool{}
		if len(requirements) > 0 {
			if assets, err = KtxAssets(root); err != nil {
				return nil, fmt.Errorf("Não foi possível consultar os recursos do KTX: %s", filepath.Join(root, "qw", "ktx.pk3"))
			}
		}
	}
	maps, err := CompatibleMaps(root, game, requirements, assets, label)
	if err != nil {
		return nil, err
	}
	lookup := map[string]string{}
	for _, name := range maps {
		lookup[strings.ToLower(name)] = name
	}
	list := &MapList{Suggested: map[string]bool{}, Requirements: len(requirements) > 0, Assets: assets, Options: o}
	for _, name := range suggested {
		if found, ok := lookup[strings.ToLower(name)]; ok && !list.Suggested[found] {
			list.Maps = append(list.Maps, found)
			list.Suggested[found] = true
		}
	}
	for _, name := range maps {
		if !list.Suggested[name] {
			list.Maps = append(list.Maps, name)
		}
	}
	if len(list.Maps) == 0 {
		return nil, fmt.Errorf("Nenhum mapa compatível está disponível para %s.", label)
	}
	if found, ok := lookup[strings.ToLower(defaultMap)]; ok {
		list.Default = found
	} else {
		list.Default = list.Maps[0]
	}
	return list, nil
}

// Resolve turns a request into a launchable match, checking what the old
// interactive flow checked: base PAKs, the game's component, local play
// support and an installed client.
func Resolve(root string, r Request) (*Choice, error) {
	if err := install.CheckPaks(root); err != nil {
		return nil, err
	}
	games, err := Installed(root)
	if err != nil {
		return nil, err
	}
	if len(games) == 0 {
		return nil, errors.New("Nenhum mod local gerenciado está instalado. Reexecute o bootstrap x86QW no mesmo destino e adicione ao menos KTX.")
	}
	var game *Game
	requested := r.Game
	if requested == "" {
		requested = "ktx"
	}
	for index := range games {
		if strings.EqualFold(games[index].ID, requested) {
			game = &games[index]
		}
	}
	if game == nil && r.Game == "" {
		game = &games[0]
	}
	if game == nil {
		all, _ := LoadGames()
		for _, known := range all {
			if strings.EqualFold(known.ID, requested) {
				return nil, fmt.Errorf("%s não está instalado. Adicione o componente %s pelo bootstrap x86QW.", known.Label, known.Component)
			}
		}
		keys := make([]string, len(games))
		for index, candidate := range games {
			keys[index] = candidate.ID
		}
		return nil, fmt.Errorf("Jogo local desconhecido: %s. Disponíveis: %s.", r.Game, strings.Join(keys, ", "))
	}
	choice := &Choice{Game: *game, Options: r.Options}
	if choice.Component = install.InstalledComponent(root, *game); choice.Component == "" {
		return nil, fmt.Errorf("O componente de %s não está mais instalado.", game.Label)
	}
	if choice.Files, choice.Selection, err = install.VerifyComponent(root, choice.Component); err != nil {
		return nil, err
	}
	switch {
	case game.UsesModeCatalog() && r.Mode == "":
		return nil, errors.New("Escolha um modo KTX (--mode).")
	case game.UsesModeCatalog():
		modes, err := LoadModes()
		if err != nil {
			return nil, err
		}
		mode, err := FindMode(modes, r.Mode)
		if err != nil {
			return nil, err
		}
		choice.Mode = &mode
	case r.Mode != "":
		return nil, errors.New("--mode só pode ser usado com o jogo KTX.")
	}
	list, err := Maps(root, *game, choice.Mode, r.Options)
	if err != nil {
		return nil, err
	}
	choice.Options, choice.Assets, choice.Map = list.Options, list.Assets, list.Default
	if r.Map != "" {
		choice.Map = ""
		for _, name := range list.Maps {
			if strings.EqualFold(name, r.Map) {
				choice.Map = name
			}
		}
		if choice.Map == "" {
			label := game.Label
			if choice.Mode != nil {
				label = "KTX · " + choice.Mode.Label
			}
			return nil, fmt.Errorf("O mapa %s não está disponível ou não é compatível com %s.", r.Map, label)
		}
	}
	if choice.Ruleset = r.Ruleset; choice.Ruleset == "" {
		if choice.Ruleset, err = ClientRuleset(root); err != nil {
			return nil, err
		}
	}
	if Rulesets[choice.Ruleset] == "" {
		return nil, fmt.Errorf("Ruleset de cliente desconhecido: %s.", choice.Ruleset)
	}
	runtimes, err := install.HostRuntimes(root)
	if err != nil {
		return nil, err
	}
	for _, runtime := range runtimes {
		if r.Channel == "" || runtime.Channel == r.Channel {
			choice.Runtime = runtime
			break
		}
	}
	if choice.Runtime.Path == "" {
		if r.Channel != "" {
			return nil, fmt.Errorf("O ezQuake %s não está instalado.", r.Channel)
		}
		return nil, errors.New("Nenhum ezQuake instalado; execute x86qw setup.")
	}
	if err := install.VerifyPlaySupport(root, games); err != nil {
		return nil, err
	}
	return choice, nil
}
