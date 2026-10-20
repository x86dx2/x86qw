package play

import (
	"encoding/json"
	"fmt"
	"regexp"
	"runtime"
	"strconv"
	"strings"
)

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
		return nil, fmt.Errorf("catálogo de modos KTX inválido: %v", err)
	}
	groups := make([]MenuGroup, len(document.Groups))
	for index, raw := range document.Groups {
		groups[index] = MenuGroup{Key: raw.ID, Label: raw.Label, Description: raw.Description, Modes: raw.Modes}
	}
	return groups, nil
}

// PlayerCountLabel mirrors player_count_label.
func PlayerCountLabel(value string) string {
	if value == "1" {
		return "1 jogador"
	}
	return value + " jogadores"
}

// RosterDescription mirrors ktx_mode_roster_description.
func RosterDescription(mode Mode) string {
	if len(mode.BotTeams) > 0 && positiveInt.MatchString(mode.RecommendedPlayers) {
		players, _ := strconv.Atoi(mode.RecommendedPlayers)
		return fmt.Sprintf("%d equipes de %d jogadores", len(mode.BotTeams), players/len(mode.BotTeams))
	}
	return "completar " + mode.RecommendedPlayers + " jogadores"
}

// WithoutFrogbots mirrors without_frogbots.
func WithoutFrogbots(o Options) Options {
	o.Bots, o.FillBots, o.BotSkill = 0, false, "5"
	o.BotTeam, o.BotWeapon, o.BotHealth, o.BotBreakOnDeath = nil, nil, nil, nil
	o.BotNamesProfile, o.BotNamePool = "default", nil
	return o
}

// CompatibleMaps mirrors the filtering half of choose_local_map: installed
// maps that carry every required asset.
func CompatibleMaps(root string, game Game, requirements []Requirement, assets map[string]bool, label string) ([]string, error) {
	maps, err := LocalMapNames(root, game.Gamedir)
	if err != nil {
		return nil, err
	}
	if len(requirements) == 0 {
		return maps, nil
	}
	var filtered []string
	for _, name := range maps {
		ok := true
		for _, requirement := range requirements {
			ok = ok && assets[strings.ToLower(strings.ReplaceAll(requirement.Asset, "{map}", strings.ToLower(name)))]
		}
		if ok {
			filtered = append(filtered, name)
		}
	}
	if len(filtered) == 0 {
		keys := make([]string, len(requirements))
		for index, requirement := range requirements {
			keys[index] = requirement.Asset
		}
		return nil, errorf("Nenhum mapa instalado possui o recurso exigido por %s: %s.", label, strings.Join(keys, ", "))
	}
	return filtered, nil
}

// OptionsCLIArguments mirrors ktx_options_cli_arguments.
func OptionsCLIArguments(o Options, dedicated bool) []string {
	var arguments []string
	if o.FillBots {
		arguments = append(arguments, "--fill-bots")
	} else if o.Bots > 0 {
		arguments = append(arguments, "--bots", strconv.Itoa(o.Bots))
	}
	if o.Bots > 0 || o.FillBots {
		arguments = append(arguments, "--bot-skill", o.BotSkill)
		if o.BotNamesProfile != "default" {
			arguments = append(arguments, "--bot-names", o.BotNamesProfile)
		}
		if o.BotTeam != nil {
			arguments = append(arguments, "--bot-team", *o.BotTeam)
		}
		if o.BotWeapon != nil {
			arguments = append(arguments, "--bot-weapon", *o.BotWeapon)
		}
		if o.BotHealth != nil {
			arguments = append(arguments, "--bot-health", strconv.Itoa(*o.BotHealth))
		}
		if o.BotBreakOnDeath != nil {
			if *o.BotBreakOnDeath {
				arguments = append(arguments, "--bot-break-on-death")
			} else {
				arguments = append(arguments, "--no-bot-break-on-death")
			}
		}
	}
	if o.CTFHook != nil {
		arguments = append(arguments, "--ctf-hook", *o.CTFHook)
	}
	if o.CTFRunes != nil {
		arguments = append(arguments, "--ctf-runes", *o.CTFRunes)
	}
	if o.CTFBasedSpawn {
		arguments = append(arguments, "--ctf-based-spawn")
	}
	if o.RaceStyle != nil {
		arguments = append(arguments, "--race-style", *o.RaceStyle)
	}
	if o.RaceScoring != nil {
		arguments = append(arguments, "--race-scoring", *o.RaceScoring)
	}
	if !dedicated {
		if o.RacePacemaker != nil {
			arguments = append(arguments, "--race-pacemaker", strconv.Itoa(*o.RacePacemaker))
		}
		if o.RaceHidePlayers {
			arguments = append(arguments, "--race-hide-players")
		}
	}
	return arguments
}

var botNameLabels = map[string]string{"default": "KTX Default", "x86qw": "x86QW aleatório", "one-piece": "One Piece (opcional)", "personal": "lista pessoal"}

// SummaryLines mirrors ktx_summary_lines.
func SummaryLines(o Options) []string {
	var lines []string
	switch {
	case o.FillBots:
		lines = append(lines, "  Bots    | preencher servidor · habilidade "+o.BotSkill)
	case o.Bots > 0:
		lines = append(lines, fmt.Sprintf("  Bots    | %d · habilidade %s", o.Bots, o.BotSkill))
	default:
		lines = append(lines, "  Bots    | sem bots")
	}
	if o.Bots > 0 || o.FillBots {
		names, ok := botNameLabels[o.BotNamesProfile]
		if !ok {
			names = o.BotNamesProfile
		}
		lines = append(lines, "  Nomes   | "+names)
		if o.BotTeam != nil {
			lines = append(lines, "  Equipe  | "+*o.BotTeam)
		}
		if o.BotWeapon != nil {
			lines = append(lines, "  Arma    | "+*o.BotWeapon)
		}
		if o.BotHealth != nil {
			lines = append(lines, fmt.Sprintf("  Vida    | %d HP", *o.BotHealth))
		}
		if o.BotBreakOnDeath != nil {
			if *o.BotBreakOnDeath {
				lines = append(lines, "  Morte   | encerra a tentativa")
			} else {
				lines = append(lines, "  Morte   | mantém a tentativa")
			}
		}
	}
	if o.CTFHook != nil {
		runes := "padrão"
		if o.CTFRunes != nil && *o.CTFRunes != "" {
			runes = *o.CTFRunes
		}
		line := "  CTF     | gancho " + *o.CTFHook + " · runas " + runes
		if o.CTFBasedSpawn {
			line += " · spawn na base"
		}
		lines = append(lines, line)
	}
	if o.RaceStyle != nil {
		race := *o.RaceStyle
		if o.RaceScoring != nil {
			race += " · pontuação " + *o.RaceScoring
		}
		if o.RacePacemaker != nil {
			race += fmt.Sprintf(" · pacemaker %d", *o.RacePacemaker)
		}
		if o.RaceHidePlayers {
			race += " · jogadores ocultos"
		}
		lines = append(lines, "  Race    | "+race)
	}
	return lines
}

// RulesetLabels mirrors PRODUCT_RULESET_LABELS.
var RulesetLabels = map[string]string{
	"x86qw": "x86QW Ruleset", "qcon": "QuakeCon", "smackdown": "Smackdown",
	"thunderdome": "Thunderdome", "mtfl": "MTFL", "smackdrive": "Smackdrive",
}

// SummaryText mirrors play_summary_text (without the first-match guide,
// which no command line reaches).
func SummaryText(game Game, mode *Mode, mapName, runtimeLabel string, o Options, ruleset string) string {
	lines := []string{"Resumo da partida", "  Jogo    | " + game.Label}
	if mode != nil {
		lines = append(lines, "  Modo    | "+mode.Label)
	}
	lines = append(lines, "  Mapa    | "+mapName, "  Experiência | "+RulesetLabels[ruleset], "  Cliente | "+runtimeLabel)
	if mode != nil {
		lines = append(lines, SummaryLines(o)...)
	}
	cli := []string{"play", game.ID, "--ruleset", ruleset}
	if mode != nil {
		cli = append(cli, "--mode", mode.Key)
		cli = append(cli, OptionsCLIArguments(o, false)...)
	}
	cli = append(cli, "--map", mapName)
	lines = append(lines, "", "Comando equivalente:", "  "+PublicCommand(cli))
	return strings.Join(lines, "\n")
}

// Launcher mirrors public_launcher_name.
func Launcher() string {
	if runtime.GOOS == "windows" {
		return "x86qw.cmd"
	}
	return "./x86qw.sh"
}

// PublicCommand mirrors render_public_command: shlex.join on POSIX,
// subprocess.list2cmdline on Windows.
func PublicCommand(arguments []string) string {
	command := append([]string{Launcher()}, arguments...)
	if runtime.GOOS == "windows" {
		return ListToCommandLine(command)
	}
	quoted := make([]string, len(command))
	for index, argument := range command {
		quoted[index] = ShellQuote(argument)
	}
	return strings.Join(quoted, " ")
}

var shellSafe = regexp.MustCompile(`^[A-Za-z0-9_@%+=:,./-]+$`)

// ShellQuote mirrors shlex.quote.
func ShellQuote(value string) string {
	if value == "" {
		return "''"
	}
	if shellSafe.MatchString(value) {
		return value
	}
	return "'" + strings.ReplaceAll(value, "'", `'"'"'`) + "'"
}

// ListToCommandLine mirrors subprocess.list2cmdline.
func ListToCommandLine(arguments []string) string {
	var result strings.Builder
	for index, argument := range arguments {
		if index > 0 {
			result.WriteByte(' ')
		}
		quote := argument == "" || strings.ContainsAny(argument, " \t")
		if quote {
			result.WriteByte('"')
		}
		backslashes := 0
		for _, r := range argument {
			switch r {
			case '\\':
				backslashes++
				continue
			case '"':
				result.WriteString(strings.Repeat(`\`, backslashes*2) + `\"`)
			default:
				result.WriteString(strings.Repeat(`\`, backslashes))
				result.WriteRune(r)
			}
			backslashes = 0
		}
		if quote {
			result.WriteString(strings.Repeat(`\`, backslashes*2) + `"`)
		} else {
			result.WriteString(strings.Repeat(`\`, backslashes))
		}
	}
	return result.String()
}

// ResolveMap is the non-interactive half of choose_local_map: the
// compatible installed map matching requested, case-insensitively.
func ResolveMap(root string, game Game, requested string, requirements []Requirement, assets map[string]bool, label string) (string, error) {
	maps, err := CompatibleMaps(root, game, requirements, assets, label)
	if err != nil {
		return "", err
	}
	for _, name := range maps {
		if strings.EqualFold(name, requested) {
			return name, nil
		}
	}
	return "", errorf("O mapa %s não está disponível ou não é compatível com %s.", requested, label)
}
