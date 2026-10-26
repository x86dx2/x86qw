package play

import (
	"fmt"
	"strings"
)

const (
	// RuntimeConfigPlaceholder is replaced by the ephemeral cfg file name.
	RuntimeConfigPlaceholder = "__X86QW_KTX_RUNTIME_CONFIG__"
	defaultRuleset           = "x86qw"
)

// RulesetKeys are the client rulesets, in the order they are offered.
var RulesetKeys = []string{"x86qw", "qcon", "smackdown", "thunderdome", "mtfl", "smackdrive"}

// Rulesets maps product rulesets to engine rulesets.
var Rulesets = map[string]string{
	"x86qw": "default", "qcon": "qcon", "smackdown": "smackdown",
	"thunderdome": "thunderdome", "mtfl": "mtfl", "smackdrive": "smackdrive",
}

var rulesetConfigs = map[string]string{"x86qw": "x86qw-ruleset.cfg"}

// Launch is everything needed to start ezQuake for a local match.
type Launch struct {
	// Arguments follow the executable and -nohome -basedir TARGET.
	Arguments []string
	// StartupCommands and BotNameSettings go to the ephemeral KTX cfg;
	// empty means no cfg is needed.
	StartupCommands []string
	BotNameSettings [][2]string
}

// RulesetArguments start ezQuake under ruleset, with its x86QW profile.
func RulesetArguments(ruleset string) []string {
	var args []string
	if ruleset != defaultRuleset {
		args = append(args, "-ruleset", Rulesets[ruleset])
	}
	flag := "0"
	if ruleset == defaultRuleset {
		flag = "1"
	}
	args = append(args, "+set", "x86qw_ruleset", flag)
	if profile, ok := rulesetConfigs[ruleset]; ok {
		args = append(args, "+exec", profile)
	}
	return args
}

// Build assembles the ezQuake arguments of a local match and, when the mode
// needs them, the commands of its KTX cfg.
func Build(game Game, mode *Mode, mapName string, assets map[string]bool, o Options, ruleset string) (*Launch, error) {
	args := RulesetArguments(ruleset)
	flag := "0"
	if ruleset == defaultRuleset {
		flag = "1"
	}
	args = append(args, "+sb_listcache", "0", "+spectator", "0", "+bind", "F12", "quit")
	for _, setting := range game.LocalServerSettings {
		args = append(args, "+"+setting[0], setting[1])
	}
	if game.UsesModeCatalog() {
		args = append(args, "+set", "k_x86qw_quadcoach", flag)
	}
	args = append(args, game.ClientGameArguments...)
	args = append(args, game.PreConnectArguments...)
	if len(game.LegacyRemoteCapabilities) > 0 {
		args = append(args, "+cl_remote_capabilities", "$cl_remote_capabilities,"+strings.Join(game.LegacyRemoteCapabilities, ","))
	}
	args = append(args, game.PreMapArguments...)
	args = append(args, game.ClientCompatibilityArguments...)
	launch := &Launch{}
	if game.UsesModeCatalog() {
		if mode == nil {
			return nil, fmt.Errorf("KTX mode missing")
		}
		settings, err := BotNameSettings(o, mode)
		if err != nil {
			return nil, err
		}
		launch.BotNameSettings = settings
		aliases, err := KeyAliasCommands(*mode, o)
		if err != nil {
			return nil, err
		}
		keyAliases := append([]string{"tempalias ktx_mode echo x86QW KTX preset: " + mode.Label + " [" + mode.Key + "]"}, aliases...)
		postMap, err := LaunchCommands(*mode, mapName, assets, o)
		if err != nil {
			return nil, err
		}
		postMap = append(postMap, "x86qw_ktx_mode_help")
		setup := append(append([]string{}, keyAliases...), postMap...)
		bots := BotOptionsRequested(o)
		if bots {
			if mode.Key == "tot" {
				args = append(args, "+unset", "k_fb_enabled", "k_fb_break_on_death")
			} else {
				args = append(args, "+set", "k_fb_enabled", "1")
			}
		}
		for _, setting := range mode.LaunchSettings {
			args = append(args, "+"+setting[0], setting[1])
		}
		event := map[string]string{"ffa": "on_enter_ffa", "tot": "on_enter_ffa", "ctf": "on_enter_ctf"}[mode.Usermode]
		if event == "" {
			event = "on_enter"
		}
		eventBody := "exec x86qw-ktx.cfg;x86qw_ktx_launch_setup"
		if mode.EntryConfig != "" {
			eventBody = "exec " + mode.EntryConfig
		}
		// The setup goes through the runtime cfg: on the command line it
		// would outgrow the console's 1024-byte line.
		if bots {
			chunked, err := ChunkedSetupCommands(postMap)
			if err != nil {
				return nil, err
			}
			quoted, err := QuoteConsoleCommand(eventBody)
			if err != nil {
				return nil, err
			}
			launch.StartupCommands = append(append(append([]string{}, keyAliases...), chunked...), "tempalias "+event+" "+quoted)
		} else {
			definitions, invocation, err := ChunkedSetupPlan(setup, 700)
			if err != nil {
				return nil, err
			}
			var compatibility []string
			if mode.EntryConfig == "" {
				eventBody = "exec x86qw-ktx.cfg;" + invocation
			} else {
				quoted, err := QuoteConsoleCommand(invocation)
				if err != nil {
					return nil, err
				}
				compatibility = []string{"tempalias x86qw_ktx_launch_setup " + quoted}
			}
			quoted, err := QuoteConsoleCommand(eventBody)
			if err != nil {
				return nil, err
			}
			launch.StartupCommands = append(append(definitions, compatibility...), "tempalias "+event+" "+quoted)
		}
		args = append(args, "+exec", RuntimeConfigPlaceholder)
		args = append(args, "+set", "k_defmap", mapName, "+set", "k_defmode", mode.Usermode)
	}
	args = append(args, "+map", mapName)
	if mode != nil && mode.Key == "tot" && o.BotBreakOnDeath != nil {
		value := "0"
		if *o.BotBreakOnDeath {
			value = "1"
		}
		args = append(args, "+k_fb_break_on_death", value)
	}
	args = append(args, game.PostMapArguments...)
	args = append(args, "+bind", "F12", "quit")
	launch.Arguments = args
	return launch, nil
}
