package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"sync"

	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/menu"
	"github.com/x86dx2/x86qw/go/internal/play"
)

// rulesetKeys keeps PRODUCT_RULESET_ENGINES order for the picker.
var rulesetKeys = []string{"x86qw", "qcon", "smackdown", "thunderdome", "mtfl", "smackdrive"}

// errBack is play_local returning None: nothing launched, exit 0.
var errBack = errors.New("voltar")

// interruptTrap is the single process-wide SIGINT handler; the innermost
// interactive phase (play inside the main menu) chooses the message.
var interruptTrap struct {
	sync.Mutex
	messages []string
	stderr   io.Writer
	signals  chan os.Signal
}

// trapInterrupt mirrors KeyboardInterrupt during the line-based pickers:
// the message, then exit 130. Raw-mode navigation reports Ctrl-C itself.
// The returned function restores the enclosing phase.
func trapInterrupt(stderr io.Writer, message string) func() {
	trap := &interruptTrap
	trap.Lock()
	defer trap.Unlock()
	if trap.signals == nil {
		trap.signals = make(chan os.Signal, 1)
		go func() {
			for range trap.signals {
				trap.Lock()
				if count := len(trap.messages); count > 0 {
					fmt.Fprintln(trap.stderr, "✗ "+trap.messages[count-1])
					os.Exit(130)
				}
				trap.Unlock()
			}
		}()
	}
	if len(trap.messages) == 0 {
		signal.Notify(trap.signals, os.Interrupt)
	}
	trap.messages = append(trap.messages, message)
	trap.stderr = stderr
	var once sync.Once
	return func() {
		once.Do(func() {
			trap.Lock()
			defer trap.Unlock()
			trap.messages = trap.messages[:len(trap.messages)-1]
			if len(trap.messages) == 0 {
				signal.Stop(trap.signals)
			}
		})
	}
}

// chooseRuleset mirrors choose_client_ruleset.
func chooseRuleset(ui *menu.UI, current string) (string, bool, error) {
	options := []menu.Option{
		{Key: "x86qw", Label: "x86QW Ruleset · recomendado", Description: "diversão assistida, nostalgia e controles modernos", Detail: "Experiência original x86QW, pronta para jogar e personalizar."},
		{Key: "qcon", Label: "QuakeCon", Description: "competição moderna", Detail: "Restrições oficiais do ruleset qcon do ezQuake."},
		{Key: "smackdown", Label: "Smackdown", Description: "competição clássica", Detail: "Ruleset tradicional da liga Smackdown."},
		{Key: "thunderdome", Label: "Thunderdome", Description: "duelos de liga", Detail: "Ruleset usado pela comunidade Thunderdome."},
		{Key: "mtfl", Label: "MTFL", Description: "legado competitivo", Detail: "Compatibilidade com regras históricas MTFL."},
		{Key: "smackdrive", Label: "Smackdrive", Description: "legado avançado", Detail: "Compatibilidade com o ruleset Smackdrive."},
	}
	defaultIndex := 0
	for index, key := range rulesetKeys {
		if key == current {
			defaultIndex = index
		}
	}
	return ui.SelectOne("Como você quer jogar?", options, menu.Settings{
		Breadcrumb: "x86QW › Experiência", Default: defaultIndex, Searchable: true, AllowBack: true,
	})
}

// saveRuleset mirrors save_client_ruleset (json.dumps indent=2, sorted).
func saveRuleset(target, ruleset string) error {
	if play.Rulesets[ruleset] == "" {
		return fmt.Errorf("Ruleset de cliente desconhecido: %s.", ruleset)
	}
	payload, _ := json.MarshalIndent(map[string]any{"format": 1, "project": "x86qw", "ruleset": ruleset}, "", "  ")
	return install.SavePersonalFile(target, ".x86qw/launcher/client-ruleset.json", append(payload, '\n'))
}

// playFlow holds one `play` session (GameplayPlayerMixin.play_local).
type playFlow struct {
	ui        *menu.UI
	con       console
	target    string
	games     []play.Game
	available []install.Game
	hosting   bool // the pickers serve host instead of play (activity="hospedar")
}

// area is the breadcrumb root of the current activity.
func (f *playFlow) area() string {
	if f.hosting {
		return "Hospedar"
	}
	return "Jogar"
}

// playChoice is everything needed to launch.
type playChoice struct {
	game    play.Game
	mode    *play.Mode
	mapName string
	options play.Options
	assets  map[string]bool
	runtime install.Runtime
	ruleset string
}

// resolveRuleset mirrors resolve_client_ruleset.
func (f *playFlow) resolveRuleset(requested string, choose bool) (string, error) {
	if requested != "" {
		return requested, nil
	}
	saved, err := readRuleset(f.target)
	if err != nil || saved != "" {
		return saved, err
	}
	if !choose {
		return "x86qw", nil
	}
	selected, back, err := chooseRuleset(f.ui, "")
	if err != nil {
		return "", err
	}
	if back {
		return "", errBack
	}
	if err := saveRuleset(f.target, selected); err != nil {
		return "", err
	}
	f.con.success("Experiência salva: " + play.RulesetLabels[selected] + ".")
	return selected, nil
}

// chooseGame mirrors choose_local_game.
func (f *playFlow) chooseGame(requested string) (*play.Game, bool, error) {
	byID := map[string]play.Game{}
	for _, game := range f.games {
		byID[game.ID] = game
	}
	if requested != "" {
		for _, game := range f.available {
			if strings.EqualFold(game.ID, requested) {
				selected := byID[game.ID]
				return &selected, false, nil
			}
		}
		if known, ok := byID[strings.ToLower(requested)]; ok {
			return nil, false, fmt.Errorf("%s não está instalado. Adicione o componente %s pelo bootstrap x86QW.", known.Label, known.Component)
		}
		keys := make([]string, len(f.available))
		for index, game := range f.available {
			keys[index] = game.ID
		}
		return nil, false, fmt.Errorf("Jogo local desconhecido: %s. Disponíveis: %s.", requested, strings.Join(keys, ", "))
	}
	options := make([]menu.Option, len(f.available))
	for index, game := range f.available {
		options[index] = menu.Option{Key: game.ID, Label: game.Label, Description: "v" + install.InstalledVersion(f.target, game), Detail: game.Description}
	}
	title := "O que deseja jogar localmente?"
	if f.hosting {
		title = "O que deseja hospedar?"
	}
	key, back, err := f.ui.SelectOne(title, options, menu.Settings{Breadcrumb: "x86QW › " + f.area(), Searchable: true, AllowBack: true})
	if err != nil || back {
		return nil, back, err
	}
	selected := byID[key]
	return &selected, false, nil
}

// chooseMode mirrors choose_ktx_mode.
func (f *playFlow) chooseMode(modes []play.Mode, requested string) (*play.Mode, bool, error) {
	if requested != "" {
		mode, err := play.FindMode(modes, requested)
		if err != nil {
			return nil, false, err
		}
		return &mode, false, nil
	}
	groups, err := play.LoadMenuGroups()
	if err != nil {
		return nil, false, err
	}
	root := "x86QW › " + f.area() + " › KTX"
	for {
		var options []menu.Option
		for _, group := range groups {
			options = append(options, menu.Option{Key: group.Key, Label: group.Label, Description: group.Description, Detail: fmt.Sprintf("%d modos", len(group.Modes))})
		}
		options = append(options, menu.Option{Key: "all", Label: "Todos os modos", Description: "catálogo KTX completo", Detail: fmt.Sprintf("%d modos com busca", len(modes))})
		groupKey, back, err := f.ui.SelectOne("Que tipo de modo deseja explorar?", options, menu.Settings{Breadcrumb: root, AllowBack: true})
		if err != nil || back {
			return nil, back, err
		}
		var group *play.MenuGroup
		for index := range groups {
			if groups[index].Key == groupKey {
				group = &groups[index]
			}
		}
		crumb := root + " › Todos os modos"
		if group != nil {
			crumb = root + " › " + group.Label
		}
		var visible []menu.Option
		for _, mode := range modes {
			if group != nil && !contains(group.Modes, mode.Key) {
				continue
			}
			visible = append(visible, menu.Option{Key: mode.Key, Label: mode.Label, Description: play.PlayerCountLabel(mode.RecommendedPlayers), Detail: mode.Description, Aliases: mode.Aliases})
		}
		verb := "jogar"
		if f.hosting {
			verb = "hospedar"
		}
		selected, back, err := f.ui.SelectOne("Qual modo KTX deseja "+verb+"?", visible, menu.Settings{
			Breadcrumb: crumb, Subtitle: "Use a busca para localizar um modo neste grupo.", Searchable: true, AllowBack: true,
		})
		if err != nil {
			return nil, false, err
		}
		if !back {
			for index := range modes {
				if modes[index].Key == selected {
					return &modes[index], false, nil
				}
			}
		}
	}
}

func contains(values []string, wanted string) bool {
	for _, value := range values {
		if value == wanted {
			return true
		}
	}
	return false
}

// chooseMap mirrors choose_local_map.
func (f *playFlow) chooseMap(game play.Game, mode *play.Mode, requested string, requirements []play.Requirement, assets map[string]bool, breadcrumb string) (string, bool, error) {
	label, defaultMap, suggested := game.Label, game.DefaultMap, game.SuggestedMaps
	if mode != nil {
		label = "KTX · " + mode.Label
		if mode.DefaultMap != "" {
			defaultMap = mode.DefaultMap
		}
		if len(mode.SuggestedMaps) > 0 {
			suggested = mode.SuggestedMaps
		}
	}
	maps, err := play.CompatibleMaps(f.target, game, requirements, assets, label)
	if err != nil {
		return "", false, err
	}
	lookup := map[string]string{}
	for _, name := range maps {
		lookup[strings.ToLower(name)] = name
	}
	if requested != "" {
		selected, ok := lookup[strings.ToLower(requested)]
		if !ok {
			return "", false, fmt.Errorf("O mapa %s não está disponível ou não é compatível com %s.", requested, label)
		}
		return selected, false, nil
	}
	var suggestions []string
	isSuggested := map[string]bool{}
	for _, name := range suggested {
		if found, ok := lookup[strings.ToLower(name)]; ok {
			suggestions = append(suggestions, found)
			isSuggested[found] = true
		}
	}
	defaultName, ok := lookup[strings.ToLower(defaultMap)]
	if !ok {
		switch {
		case len(suggestions) > 0:
			defaultName = suggestions[0]
		case len(maps) > 0:
			defaultName = maps[0]
		default:
			return "", false, fmt.Errorf("Nenhum mapa compatível está disponível para %s.", label)
		}
	}
	ordered := append([]string{}, suggestions...)
	for _, name := range maps {
		if !isSuggested[name] {
			ordered = append(ordered, name)
		}
	}
	detail := "mapa instalado"
	if len(requirements) > 0 {
		detail = "recursos compatíveis e instalados"
	}
	defaultIndex := 0
	options := make([]menu.Option, len(ordered))
	for index, name := range ordered {
		description := ""
		if isSuggested[name] {
			description = "sugerido"
		}
		if name == defaultName {
			defaultIndex = index
		}
		options[index] = menu.Option{Key: name, Label: name, Description: description, Detail: detail}
	}
	key, back, err := f.ui.SelectOne("Escolha o mapa para "+label, options, menu.Settings{
		Breadcrumb: breadcrumb,
		Subtitle:   fmt.Sprintf("%d mapa(s) compatível(is) com esta configuração. Use a busca para filtrar.", len(maps)),
		Default:    defaultIndex, Searchable: true, AllowBack: true,
	})
	return key, back, err
}

// chooseRuntime mirrors choose_host_runtime.
func (f *playFlow) chooseRuntime(breadcrumb string) (*install.Runtime, bool, error) {
	runtimes, err := install.HostRuntimes(f.target)
	if err != nil {
		return nil, false, err
	}
	if len(runtimes) == 1 {
		return &runtimes[0], false, nil
	}
	options := make([]menu.Option, len(runtimes))
	for index, runtime := range runtimes {
		options[index] = menu.Option{Key: strconv.Itoa(index), Label: runtime.Label, Description: "runtime instalado"}
	}
	key, back, err := f.ui.SelectOne("Qual cliente deseja abrir?", options, menu.Settings{Breadcrumb: breadcrumb, AllowBack: true})
	if err != nil || back {
		return nil, back, err
	}
	index, _ := strconv.Atoi(key)
	return &runtimes[index], false, nil
}

// run mirrors play_local up to the launch. errBack means it returned
// without launching (a back at the top or a declined confirmation).
func (f *playFlow) run(request *playRequest, configure, chooseRuleset bool) (*playChoice, error) {
	ruleset, err := f.resolveRuleset(request.ruleset, chooseRuleset)
	if err != nil {
		return nil, err
	}
	if err := install.CheckPaks(f.target); err != nil {
		return nil, err
	}
	if len(f.available) == 0 {
		return nil, errors.New("Nenhum mod local gerenciado está instalado. Reexecute o bootstrap x86QW no mesmo destino e adicione ao menos KTX.")
	}
	var (
		game        *play.Game
		mode        *play.Mode
		mapName     string
		assets      map[string]bool
		runtime     *install.Runtime
		previousKey string
	)
	base := request.options
	options := base
	state := "game"
	for {
		switch state {
		case "game":
			selected, back, err := f.chooseGame(request.game)
			if err != nil {
				return nil, err
			}
			if back {
				return nil, errBack
			}
			game = selected
			if request.mode != "" && !game.UsesModeCatalog() {
				return nil, errors.New("--mode só pode ser usado com o jogo KTX.")
			}
			component := install.InstalledComponent(f.target, installGame(f.available, game.ID))
			if component == "" {
				return nil, fmt.Errorf("O componente de %s não está mais instalado.", game.Label)
			}
			count, selection, err := install.VerifyComponent(f.target, component)
			if err != nil {
				return nil, err
			}
			f.con.success(fmt.Sprintf("Componente %s íntegro (%s; seleção %s).", component, fileCount(count), selection))
			mode = nil
			options = base
			state = "map"
			if game.UsesModeCatalog() {
				state = "mode"
			}
		case "mode":
			modes, err := play.LoadModes()
			if err != nil {
				return nil, err
			}
			selected, back, err := f.chooseMode(modes, request.mode)
			if err != nil {
				return nil, err
			}
			if back {
				state = "game"
				continue
			}
			mode = selected
			if previousKey != "" && previousKey != mode.Key {
				options = base
			}
			previousKey = mode.Key
			f.con.success("Modo KTX selecionado: " + mode.Label + ".")
			state = "map"
			if configure {
				state = "options"
			}
		case "options":
			chosen, back, err := f.chooseOptions(*mode, options)
			if err != nil {
				return nil, err
			}
			if back {
				state = "mode"
				continue
			}
			options = chosen
			state = "map"
		case "map":
			var requirements []play.Requirement
			breadcrumb := "x86QW › Jogar › " + game.Label + " › Mapa"
			assets = nil
			if game.UsesModeCatalog() {
				if options, err = play.ResolveNames(f.target, *game, options, mode); err != nil {
					return nil, err
				}
				requirements = play.ActiveRequirements(*mode, options)
				assets = map[string]bool{}
				if len(requirements) > 0 {
					if assets, err = play.KtxAssets(f.target); err != nil {
						return nil, fmt.Errorf("Não foi possível consultar os recursos do KTX: %s", filepath.Join(f.target, "qw", "ktx.pk3"))
					}
				}
				breadcrumb = "x86QW › Jogar › KTX › " + mode.Label + " › Mapa"
			}
			selected, back, err := f.chooseMap(*game, mode, request.mapName, requirements, assets, breadcrumb)
			if err != nil {
				return nil, err
			}
			if back {
				switch {
				case game.UsesModeCatalog() && configure:
					state = "options"
				case game.UsesModeCatalog():
					state = "mode"
				default:
					state = "game"
				}
				continue
			}
			mapName = selected
			state = "runtime"
		case "runtime":
			crumb := "x86QW › Jogar › " + game.Label
			if mode != nil {
				crumb = "x86QW › Jogar › KTX › " + mode.Label
			}
			selected, back, err := f.chooseRuntime(crumb + " › Cliente")
			if err != nil {
				return nil, err
			}
			if back {
				state = "map"
				continue
			}
			runtime = selected
			if configure {
				summary := play.SummaryText(*game, mode, mapName, runtime.Label, options, ruleset)
				yes, back, err := f.ui.Confirm("Iniciar esta partida?", menu.Settings{
					Breadcrumb: crumb + " › Confirmação", Subtitle: "\n" + summary, AllowBack: true,
				}, "abrir o jogo com as escolhas acima", true)
				if err != nil {
					return nil, err
				}
				if back {
					continue
				}
				if !yes {
					f.con.info("Partida cancelada; nenhum cliente foi aberto.")
					return nil, errBack
				}
			}
			if err := install.VerifyPlaySupport(f.target, f.available); err != nil {
				return nil, err
			}
			return &playChoice{game: *game, mode: mode, mapName: mapName, options: options, assets: assets, runtime: *runtime, ruleset: ruleset}, nil
		}
	}
}

func installGame(games []install.Game, id string) install.Game {
	for _, game := range games {
		if game.ID == id {
			return game
		}
	}
	return install.Game{}
}
