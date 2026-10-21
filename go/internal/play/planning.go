package play

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"
)

// length counts characters like Python's len(str).
func length(value string) int { return utf8.RuneCountInString(value) }

// Ported from x86qw_runtime/gameplay/planning.py; strings are part of the
// observable behaviour (ezQuake console input) and must not drift.

const (
	frogbotAddWaitFrames = 8
	botNameSlots         = 32
)

var (
	botNamePrefixes = []string{"k_fb_name", "k_fb_name_team", "k_fb_name_enemy"}
	frogbotName     = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9 .'-]{0,12}$`)
	positiveInt     = regexp.MustCompile(`^[1-9][0-9]*$`)
)

// Options mirrors KtxLaunchOptions. Pointers mean "not given".
type Options struct {
	Bots            int
	FillBots        bool
	BotSkill        string // "1".."20" or "random"; default "5"
	BotTeam         *string
	BotWeapon       *string
	BotHealth       *int
	BotBreakOnDeath *bool
	BotNamesProfile string
	BotNamePool     []string
	CTFHook         *string
	CTFRunes        *string
	CTFBasedSpawn   bool
	RaceStyle       *string
	RaceScoring     *string
	RacePacemaker   *int
	RaceHidePlayers bool
}

// DefaultOptions are the options when no flag is given.
func DefaultOptions() Options {
	return Options{BotSkill: "5", BotNamesProfile: "default"}
}

// BotOptionsRequested mirrors ktx_bot_options_requested.
func BotOptionsRequested(o Options) bool {
	return o.Bots > 0 || o.FillBots || o.BotSkill != "5" || o.BotTeam != nil ||
		o.BotWeapon != nil || o.BotHealth != nil || o.BotBreakOnDeath != nil
}

// BotLimit is recommended_players-1 when numeric (capped at 31).
func BotLimit(mode Mode) (int, bool) {
	if !positiveInt.MatchString(mode.RecommendedPlayers) {
		return 0, false
	}
	value, _ := strconv.Atoi(mode.RecommendedPlayers)
	value--
	if value > 31 {
		value = 31
	}
	if value < 0 {
		value = 0
	}
	return value, true
}

// RequestedNames is how many bots will be named.
func RequestedNames(o Options, mode *Mode) int {
	if !o.FillBots {
		return o.Bots
	}
	if mode != nil {
		if limit, ok := BotLimit(*mode); ok {
			return limit
		}
	}
	return 8
}

// ColoredName encodes a Frogbot name with Quake high-bit colors.
func ColoredName(name string) string {
	var builder strings.Builder
	builder.WriteString("/$xa0")
	for _, r := range name {
		if r == ' ' {
			builder.WriteString("$xa0")
		} else {
			fmt.Fprintf(&builder, "$x%02x", int(r)|0x80)
		}
	}
	return builder.String()
}

// BotNameSettings returns (cvar, colored name) pairs for the name pool.
func BotNameSettings(o Options, mode *Mode) ([][2]string, error) {
	count := RequestedNames(o, mode)
	if len(o.BotNamePool) == 0 || count == 0 {
		return nil, nil
	}
	if len(o.BotNamePool) < count {
		return nil, fmt.Errorf("O perfil de nomes não cobre todos os Frogbots solicitados.")
	}
	var settings [][2]string
	for _, prefix := range botNamePrefixes {
		for index := 0; index < count; index++ {
			name := o.BotNamePool[index]
			if !frogbotName.MatchString(name) {
				return nil, fmt.Errorf("Nome Frogbot inválido em opções de lançamento: use de 1 a 13 caracteres ASCII.")
			}
			settings = append(settings, [2]string{fmt.Sprintf("%s_%d", prefix, index), ColoredName(name)})
		}
	}
	return settings, nil
}

// BotNameCleanupCommands unsets every name slot, 12 per line.
func BotNameCleanupCommands() []string {
	var names []string
	for _, prefix := range botNamePrefixes {
		for index := 0; index < botNameSlots; index++ {
			names = append(names, fmt.Sprintf("%s_%d", prefix, index))
		}
	}
	return unsetLines(names)
}

func unsetLines(names []string) []string {
	var lines []string
	for offset := 0; offset < len(names); offset += 12 {
		end := offset + 12
		if end > len(names) {
			end = len(names)
		}
		lines = append(lines, "unset "+strings.Join(names[offset:end], " "))
	}
	return lines
}

// QuoteConsoleCommand keeps a body inside one ezQuake argument.
func QuoteConsoleCommand(command string) (string, error) {
	if strings.Contains(command, `"`) {
		return "", fmt.Errorf("Comando interno do ezQuake contém caracteres inválidos.")
	}
	for _, r := range command {
		if r < 32 {
			return "", fmt.Errorf("Comando interno do ezQuake contém caracteres inválidos.")
		}
	}
	return `"` + command + `"`, nil
}

// quoter quotes console commands and keeps the first error, so a list of
// aliases builds without a check after every line.
type quoter struct{ err error }

func (q *quoter) quote(command string) string {
	quoted, err := QuoteConsoleCommand(command)
	if err != nil && q.err == nil {
		q.err = err
	}
	return quoted
}

// KeyAliasCommands are the aliases behind the mode's context keys and,
// when bots are requested, the bot keys.
func KeyAliasCommands(mode Mode, o Options) ([]string, error) {
	var commands []string
	for _, key := range ContextKeys {
		alias := strings.ToLower(key)
		commands = append(commands,
			"tempalias x86qw_ktx_key_"+alias+" $qt$qt",
			"tempalias x86qw_ktx_key_help_"+alias+" $qt$qt")
	}
	for _, binding := range mode.KeyBindings {
		alias := strings.ToLower(binding.Key)
		var colored strings.Builder
		for _, r := range binding.Key {
			colored.WriteString("^" + string(r))
		}
		commands = append(commands,
			"tempalias x86qw_ktx_key_"+alias+" "+binding.Command,
			"tempalias x86qw_ktx_key_help_"+alias+" echo "+colored.String()+"$x20$x7c$x20"+binding.Description)
	}
	if !BotOptionsRequested(o) {
		return commands, nil
	}
	bots, err := botManagementAliases(mode, o)
	return append(commands, bots...), err
}

func botManagementAliases(mode Mode, o Options) ([]string, error) {
	var q quoter
	initial := RequestedNames(o, &mode)
	maximum := 31
	if limit, ok := BotLimit(mode); ok {
		maximum = limit
	}
	team := ""
	if o.BotTeam != nil {
		team = " " + *o.BotTeam
	}
	var commands []string
	for count := 0; count <= maximum; count++ {
		action := "echo Limite de Frogbots deste modo atingido"
		if count < maximum {
			action = fmt.Sprintf("x86qw_ktx_bot_add_command;x86qw_ktx_bot_roster_%d", count+1)
		}
		commands = append(commands, fmt.Sprintf("tempalias x86qw_ktx_bot_add_%d %s", count, q.quote(action)))
		action = "echo Nenhum Frogbot para remover"
		if count > 0 {
			action = fmt.Sprintf("cmd botcmd removebot;x86qw_ktx_bot_roster_%d", count-1)
		}
		commands = append(commands, fmt.Sprintf("tempalias x86qw_ktx_bot_remove_%d %s", count, q.quote(action)))
		roster := fmt.Sprintf("tempalias x86qw_ktx_bot_add x86qw_ktx_bot_add_%d;tempalias x86qw_ktx_bot_remove x86qw_ktx_bot_remove_%d", count, count)
		commands = append(commands, fmt.Sprintf("tempalias x86qw_ktx_bot_roster_%d %s", count, q.quote(roster)))
	}
	if o.BotSkill == "random" {
		message := "echo Habilidade aleatoria ativa para cada novo Frogbot"
		commands = append(commands,
			"tempalias x86qw_ktx_bot_add_command "+q.quote("cmd botcmd addbot random"+team),
			"tempalias x86qw_ktx_bot_skill_down "+q.quote(message),
			"tempalias x86qw_ktx_bot_skill_up "+q.quote(message))
	} else {
		for skill := 1; skill <= 20; skill++ {
			lower, higher := skill-1, skill+1
			if lower < 1 {
				lower = 1
			}
			if higher > 20 {
				higher = 20
			}
			down := fmt.Sprintf("echo Habilidade dos proximos Frogbots: %d;cmd botcmd skill %d;x86qw_ktx_bot_skill_%d", lower, lower, lower)
			up := fmt.Sprintf("echo Habilidade dos proximos Frogbots: %d;cmd botcmd skill %d;x86qw_ktx_bot_skill_%d", higher, higher, higher)
			setup := strings.Join([]string{
				fmt.Sprintf("tempalias x86qw_ktx_bot_skill_down x86qw_ktx_bot_skill_down_%d", skill),
				fmt.Sprintf("tempalias x86qw_ktx_bot_skill_up x86qw_ktx_bot_skill_up_%d", skill),
				fmt.Sprintf("tempalias x86qw_ktx_bot_add_command cmd botcmd addbot %d%s", skill, team),
			}, ";")
			commands = append(commands,
				fmt.Sprintf("tempalias x86qw_ktx_bot_skill_down_%d %s", skill, q.quote(down)),
				fmt.Sprintf("tempalias x86qw_ktx_bot_skill_up_%d %s", skill, q.quote(up)),
				fmt.Sprintf("tempalias x86qw_ktx_bot_skill_%d %s", skill, q.quote(setup)))
		}
		commands = append(commands, "x86qw_ktx_bot_skill_"+o.BotSkill)
	}
	return append(commands,
		fmt.Sprintf("x86qw_ktx_bot_roster_%d", initial),
		"tempalias x86qw_ktx_bot_help exec x86qw-ktx-help-bots.cfg"), q.err
}

// ChunkedSetupPlan mirrors ktx_chunked_setup_alias_plan.
func ChunkedSetupPlan(commands []string, maximum int) ([]string, string, error) {
	var q quoter
	var chunks [][]string
	var current []string
	total := 0
	for _, command := range commands {
		if length(command) > maximum {
			return nil, "", fmt.Errorf("Comando de inicialização KTX excede o limite seguro.")
		}
		added := length(command)
		if len(current) > 0 {
			added++
		}
		if len(current) > 0 && total+added > maximum {
			chunks = append(chunks, current)
			current, total, added = nil, 0, length(command)
		}
		current = append(current, command)
		total += added
	}
	if len(current) > 0 {
		chunks = append(chunks, current)
	}
	var definitions, aliases []string
	for index, chunk := range chunks {
		alias := fmt.Sprintf("x86qw_ktx_launch_setup_%d", index+1)
		aliases = append(aliases, alias)
		body := strings.ReplaceAll(strings.Join(chunk, ";"), `"`, "$qt")
		quoted, err := QuoteConsoleCommand(body)
		if err != nil {
			return nil, "", err
		}
		definitions = append(definitions, "tempalias "+alias+" "+quoted)
	}
	level := 1
	for length(strings.Join(aliases, ";")) > maximum {
		var parents, group []string
		groupLength := 0
		flush := func() {
			parent := fmt.Sprintf("x86qw_ktx_launch_group_%d_%d", level, len(parents)+1)
			definitions = append(definitions, "tempalias "+parent+" "+q.quote(strings.Join(group, ";")))
			parents = append(parents, parent)
		}
		for _, alias := range aliases {
			added := len(alias)
			if len(group) > 0 {
				added++
			}
			if len(group) > 0 && groupLength+added > maximum {
				flush()
				group, groupLength, added = nil, 0, len(alias)
			}
			group = append(group, alias)
			groupLength += added
		}
		if len(group) > 0 {
			flush()
		}
		aliases = parents
		level++
	}
	return definitions, strings.Join(aliases, ";"), q.err
}

// ChunkedSetupCommands adds the compatibility entry alias.
func ChunkedSetupCommands(commands []string) ([]string, error) {
	definitions, invocation, err := ChunkedSetupPlan(commands, 700)
	if err != nil {
		return nil, err
	}
	quoted, err := QuoteConsoleCommand(invocation)
	if err != nil {
		return nil, err
	}
	return append(definitions, "tempalias x86qw_ktx_launch_setup "+quoted), nil
}

// ActiveRequirements are the mode's assets needed with these options.
func ActiveRequirements(mode Mode, o Options) []Requirement {
	frogbots := BotOptionsRequested(o)
	var active []Requirement
	for _, requirement := range mode.Requirements {
		if requirement.When == "always" || (requirement.When == "frogbots" && frogbots) {
			active = append(active, requirement)
		}
	}
	return active
}

func teamSequence(mode Mode, o Options) []string {
	count := RequestedNames(o, &mode)
	result := make([]string, count)
	if o.BotTeam != nil {
		for index := range result {
			result[index] = *o.BotTeam
		}
		return result
	}
	if len(mode.BotTeams) == 0 {
		return result
	}
	populations := make([]int, len(mode.BotTeams))
	populations[0] = 1
	for index := 0; index < count; index++ {
		selected := 0
		for candidate := range populations {
			if populations[candidate] < populations[selected] {
				selected = candidate
			}
		}
		result[index] = mode.BotTeams[selected]
		populations[selected]++
	}
	return result
}

// LaunchCommands mirrors ktx_launch_commands (post-map setup).
func LaunchCommands(mode Mode, mapName string, assets map[string]bool, o Options) ([]string, error) {
	var commands []string
	for _, requirement := range ActiveRequirements(mode, o) {
		asset := strings.ToLower(strings.ReplaceAll(requirement.Asset, "{map}", strings.ToLower(mapName)))
		if !assets[asset] {
			return nil, fmt.Errorf("O mapa %s não possui o recurso %s exigido pelo modo %s (%s).", mapName, requirement.Label, mode.Label, asset)
		}
	}
	if BotOptionsRequested(o) {
		if !mode.Bots {
			return nil, fmt.Errorf("Bots Frogbot não são compatíveis com o modo %s.", mode.Label)
		}
		if limit, ok := BotLimit(mode); ok && RequestedNames(o, &mode) > limit {
			noun := "Frogbots"
			if limit == 1 {
				noun = "Frogbot"
			}
			return nil, fmt.Errorf("%s aceita no máximo %d %s com um jogador humano.", mode.Label, limit, noun)
		}
		if o.BotTeam != nil && o.Bots == 0 {
			return nil, fmt.Errorf("--bot-team exige --bots; o comando fill distribui as equipes.")
		}
		if o.BotTeam != nil && len(mode.BotTeams) > 0 {
			known := false
			for _, team := range mode.BotTeams {
				known = known || strings.EqualFold(team, *o.BotTeam)
			}
			if !known {
				return nil, fmt.Errorf("A equipe %s não pertence ao modo %s; use uma destas: %s.", *o.BotTeam, mode.Label, strings.Join(mode.BotTeams, ", "))
			}
		}
		if mode.Key != "tot" && (o.BotWeapon != nil || o.BotHealth != nil || o.BotBreakOnDeath != nil) {
			return nil, fmt.Errorf("--bot-weapon, --bot-health e --[no-]bot-break-on-death só podem ser usados com o modo KTX tot.")
		}
		count := RequestedNames(o, &mode)
		if !o.FillBots || len(mode.BotTeams) > 0 {
			required := count + 1
			commands = append(commands,
				fmt.Sprintf("if ($k_maxclients < %d) then k_maxclients %d", required, required),
				fmt.Sprintf("if ($maxclients < %d) then maxclients %d", required, required))
		}
		if len(mode.BotTeams) > 0 && o.BotTeam == nil {
			commands = append(commands, "team "+mode.BotTeams[0])
		}
		commands = append(commands, "cmd botcmd skill "+o.BotSkill)
		if o.BotHealth != nil {
			commands = append(commands, fmt.Sprintf("cmd botcmd health %d", *o.BotHealth))
		}
		if o.BotWeapon != nil {
			commands = append(commands, "cmd botcmd weapon "+*o.BotWeapon)
		}
		if o.FillBots && len(mode.BotTeams) == 0 {
			commands = append(commands, "cmd botcmd fill "+o.BotSkill)
		} else {
			sequence := teamSequence(mode, o)
			for index, selected := range sequence {
				team := ""
				if o.BotTeam != nil && selected != "" {
					team = " " + selected
				}
				commands = append(commands, "cmd botcmd addbot "+o.BotSkill+team)
				if index+1 < len(sequence) {
					for wait := 0; wait < frogbotAddWaitFrames; wait++ {
						commands = append(commands, "wait")
					}
				}
			}
		}
		if len(o.BotNamePool) > 0 {
			for wait := 0; wait < frogbotAddWaitFrames; wait++ {
				commands = append(commands, "wait")
			}
			settings, err := BotNameSettings(o, &mode)
			if err != nil {
				return nil, err
			}
			names := make([]string, len(settings))
			for index, setting := range settings {
				names[index] = setting[0]
			}
			commands = append(commands, unsetLines(names)...)
		}
	}
	if (o.CTFHook != nil || o.CTFRunes != nil || o.CTFBasedSpawn) && mode.Key != "ctf" {
		return nil, fmt.Errorf("Opções --ctf-* só podem ser usadas com o modo KTX ctf.")
	}
	if mode.Key == "ctf" {
		hooks := map[string]string{"off": "nohook", "smooth": "hook_smooth", "fast": "hook_fast", "classic": "hook_classic", "crhook": "hook_crhook"}
		if o.CTFHook != nil {
			commands = append(commands, "cmd "+hooks[*o.CTFHook])
		}
		if o.CTFRunes != nil && *o.CTFRunes == "off" {
			commands = append(commands, "cmd norunes")
		}
		if o.CTFBasedSpawn {
			commands = append(commands, "cmd ctfbasedspawn")
		}
	}
	if (o.RaceStyle != nil || o.RaceScoring != nil || o.RacePacemaker != nil || o.RaceHidePlayers) && mode.Key != "race" {
		return nil, fmt.Errorf("Opções --race-* só podem ser usadas com o modo KTX race.")
	}
	if mode.Key == "race" {
		style := ""
		if o.RaceStyle != nil {
			style = *o.RaceStyle
		}
		if o.RaceScoring != nil && (style == "solo" || style == "simultaneous") {
			return nil, fmt.Errorf("--race-scoring exige --race-style match (ou omitir o estilo).")
		}
		if style == "solo" {
			commands = append(commands, "cmd race_simultaneous")
		}
		if style == "match" || (o.RaceStyle == nil && o.RaceScoring != nil) {
			commands = append(commands, "cmd race_match")
		}
		steps := 0
		if o.RaceScoring != nil {
			steps = map[string]int{"win": 0, "scaled": 1, "formula1": 2}[*o.RaceScoring]
		}
		for index := 0; index < steps; index++ {
			commands = append(commands, "cmd race_scoring")
		}
		if o.RacePacemaker != nil {
			commands = append(commands, fmt.Sprintf("cmd race_pacemaker %d", *o.RacePacemaker))
		}
		if o.RaceHidePlayers {
			commands = append(commands, "cmd race_hide_players")
		}
	}
	return commands, nil
}
