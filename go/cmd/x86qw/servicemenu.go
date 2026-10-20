package main

import (
	"errors"
	"fmt"
	"io"
	"strconv"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/menu"
	"github.com/x86dx2/x86qw/go/internal/output"
	"github.com/x86dx2/x86qw/go/internal/play"
	"github.com/x86dx2/x86qw/go/internal/services"
)

// Interactive host, QTV and QWFWD configuration (the --menu path of
// services.py): the menus only fill the command-line options, and the
// validated non-interactive path then runs exactly what was chosen.

// errCancelledByUser ends a service menu with "nada foi iniciado".
var errCancelledByUser = errors.New("cancelado")

// serviceMain runs host, qtv or proxy; with --menu the choices are collected
// first. propagateExit lets q end the enclosing main menu.
func serviceMain(action string, args []string, stdout, stderr io.Writer, propagateExit bool) (int, error) {
	var op *services.Operation
	var common *services.Common
	switch action {
	case "host":
		games, err := play.LoadGames()
		if err != nil {
			fmt.Fprintf(stderr, "✗ %v\n", err)
			return output.ExitFailure, nil
		}
		options, err := services.ParseHost(args, games)
		if err != nil {
			return serviceUsage("host", err, stderr), nil
		}
		op, common = &services.Operation{Action: "host", Host: options, Args: args}, &options.Common
	case "qtv":
		options, err := services.ParseQtv(args)
		if err != nil {
			return serviceUsage("qtv", err, stderr), nil
		}
		op, common = &services.Operation{Action: "qtv", Qtv: options, Args: args}, &options.Common
	default:
		options, err := services.ParseProxy(args)
		if err != nil {
			return serviceUsage("proxy", err, stderr), nil
		}
		op, common = &services.Operation{Action: "proxy", Proxy: options, Args: args}, &options.Common
	}
	if common.Menu && !common.BackgroundChild {
		con := console{out: stdout, err: stderr, color: !common.NoColor, verbose: common.Verbose}
		if common.Target == "" {
			common.Target = defaultTarget()
		}
		ui := interactiveUI(common.NoColor)
		var err error
		if action == "host" {
			err = hostMenu(ui, con, op.Host)
		} else {
			err = serviceMenu(ui, op)
		}
		switch {
		case err == nil:
		case errors.Is(err, errCancelledByUser):
			if action == "host" {
				con.info("Hospedagem cancelada; nenhum serviço foi iniciado.")
			} else {
				con.info("Serviço cancelado; nenhum processo foi iniciado.")
			}
			return output.ExitSuccess, nil
		case errors.Is(err, menu.ErrExit):
			if propagateExit {
				return 0, err
			}
			con.info("Menu encerrado; nenhum serviço foi iniciado.")
			return output.ExitSuccess, nil
		case errors.Is(err, menu.ErrCancelled), errors.Is(err, menu.ErrInterrupt):
			con.info("Operação cancelada; nenhum serviço foi iniciado.")
			return exitInterrupted, nil
		default:
			con.failure(err.Error())
			return output.ExitFailure, nil
		}
		common.Menu = false
	}
	return runService(op, common, stdout, stderr), nil
}

// hostMenu mirrors select_hosted_game plus the profile, advanced and
// confirmation loop of services.main.
func hostMenu(ui *menu.UI, con console, o *services.HostOptions) error {
	if err := install.CheckPaks(o.Target); err != nil {
		return err
	}
	games, err := play.LoadGames()
	if err != nil {
		return err
	}
	installGames, err := install.LoadGames()
	if err != nil {
		return err
	}
	flow := &playFlow{ui: ui, con: con, target: o.Target, games: games, available: install.AvailableGames(o.Target, installGames), hosting: true}
	if len(flow.available) == 0 {
		return errors.New("Nenhum mod gerenciado está instalado. Execute o bootstrap e selecione um jogo.")
	}
	var (
		game    *play.Game
		mode    *play.Mode
		mapName string
	)
	base := o.KtxOptions
	options := base
	previousKey := ""
	requestedGame, requestedMode, requestedMap := o.Game, "", o.Map
	if o.ModeGiven {
		requestedMode = o.Mode
	}
	state := "game"
	for state != "done" {
		switch state {
		case "game":
			selected, back, err := flow.chooseGame(requestedGame)
			if err != nil {
				return err
			}
			if back {
				return errCancelledByUser
			}
			requestedGame = ""
			game, mode, options, previousKey = selected, nil, base, ""
			component := install.InstalledComponent(o.Target, installGame(flow.available, game.ID))
			if component == "" {
				return fmt.Errorf("O componente de %s não está mais instalado.", game.Label)
			}
			count, selection, err := install.VerifyComponent(o.Target, component)
			if err != nil {
				return err
			}
			con.success(fmt.Sprintf("Componente %s íntegro (%s; seleção %s).", component, fileCount(count), selection))
			state = "map"
			if game.UsesModeCatalog() {
				state = "mode"
			}
		case "mode":
			modes, err := play.LoadModes()
			if err != nil {
				return err
			}
			selected, back, err := flow.chooseMode(modes, requestedMode)
			if err != nil {
				return err
			}
			requestedMode = ""
			if back {
				state = "game"
				continue
			}
			mode = selected
			if previousKey != "" && previousKey != mode.Key {
				options = base
			}
			previousKey = mode.Key
			con.success("Modo KTX selecionado: " + mode.Label + ".")
			state = "options"
		case "options":
			chosen, back, err := flow.chooseOptions(*mode, options)
			if err != nil {
				return err
			}
			if back {
				state = "mode"
				continue
			}
			options, state = chosen, "map"
		case "map":
			var requirements []play.Requirement
			var assets map[string]bool
			crumb := "x86QW › Hospedar › " + game.Label + " › Mapa"
			if mode != nil {
				resolved, err := play.ResolveNames(o.Target, *game, options, mode)
				if err != nil {
					return err
				}
				if assets, err = play.KtxAssets(o.Target); err != nil {
					return err
				}
				requirements = play.ActiveRequirements(*mode, resolved)
				crumb = "x86QW › Hospedar › KTX › " + mode.Label + " › Mapa"
			}
			chosen, back, err := flow.chooseMap(*game, mode, requestedMap, requirements, assets, crumb)
			if err != nil {
				return err
			}
			requestedMap = ""
			if back {
				state = "game"
				if mode != nil {
					state = "options"
				}
				continue
			}
			mapName = chosen
			_, err = hostProfileLoop(ui, o, game, mode, mapName, options)
			if errors.Is(err, errBackToMap) {
				continue
			}
			if err != nil {
				return err
			}
			state = "done"
		}
	}
	o.Game, o.Map, o.KtxOptions = game.ID, mapName, options
	if mode != nil {
		o.Mode, o.ModeGiven = mode.Key, true
	}
	return nil
}

// errBackToMap returns from the profile choice to the map picker.
var errBackToMap = errors.New("voltar ao mapa")

// hostProfileLoop chooses quick or advanced settings, then confirms the
// summary; back on the profile returns to the map.
func hostProfileLoop(ui *menu.UI, o *services.HostOptions, game *play.Game, mode *play.Mode, mapName string, options play.Options) (string, error) {
	current := ""
	for {
		profile, back, err := ui.SelectOne("Como deseja configurar o servidor?", []menu.Option{
			{Key: "quick", Label: "Rápido local", Description: "padrões seguros em 127.0.0.1", Detail: "MVD ativo, sem QTV/QWFWD e sem exposição à rede"},
			{Key: "advanced", Label: "Avançado", Description: "rede, portas, capacidade e serviços", Detail: "controle completo de MVDSV, QTV, QWFWD e senhas"},
		}, menu.Settings{Breadcrumb: "x86QW › Hospedar › Configuração", Default: boolIndex(current == "advanced"), AllowBack: true})
		if err != nil {
			return "", err
		}
		if back {
			return "", errBackToMap
		}
		current = profile
		if profile == "quick" {
			applyQuickHostDefaults(o)
			ok, err := menuExecutionMode(ui, &o.Background, "x86QW › Hospedar › Rápido local")
			if err != nil {
				return "", err
			}
			if !ok {
				continue
			}
		} else {
			ok, err := configureAdvancedHost(ui, o)
			if err != nil {
				return "", err
			}
			if !ok {
				continue
			}
		}
		summary := hostSummaryText(o, game, mode, mapName, options, profile)
		confirmed, back, err := ui.Confirm("Iniciar esta hospedagem?", menu.Settings{
			Breadcrumb: "x86QW › Hospedar › Confirmação", Subtitle: "\n" + summary, AllowBack: true,
		}, "iniciar MVDSV e os serviços selecionados", true)
		if err != nil {
			return "", err
		}
		if back {
			continue
		}
		if !confirmed {
			return "", errCancelledByUser
		}
		return profile, nil
	}
}

func boolIndex(value bool) int {
	if value {
		return 1
	}
	return 0
}

// menuText mirrors menu_text: a line with a default; b goes back, q exits.
func menuText(ui *menu.UI, prompt, defaultValue string, parse func(string) (string, error)) (string, bool, error) {
	for {
		answer, err := ui.ReadLine(fmt.Sprintf("%s (padrão: %s): ", prompt, defaultValue))
		if err != nil {
			return "", false, fmt.Errorf("Nenhum valor foi informado para %s.", strings.ToLower(prompt))
		}
		answer = strings.TrimSpace(answer)
		if answer == "" {
			answer = defaultValue
		}
		switch strings.ToLower(answer) {
		case "b", "voltar":
			return "", true, nil
		case "q", "sair":
			return "", false, menu.ErrExit
		}
		value, err := parse(answer)
		if err == nil {
			return value, false, nil
		}
		fmt.Fprintf(ui.Out, "! %v\n", err)
	}
}

// menuBind mirrors menu_bind.
func menuBind(ui *menu.UI, label, current, breadcrumb string) (string, bool, error) {
	known := []string{"127.0.0.1", "0.0.0.0", "::"}
	defaultIndex := 3
	for index, address := range known {
		if address == current {
			defaultIndex = index
		}
	}
	if breadcrumb == "" {
		breadcrumb = "x86QW › Serviços › " + label + " › Rede"
	}
	selected, back, err := ui.SelectOne("Interface de rede do "+label, []menu.Option{
		{Key: "loopback", Label: "Somente este computador", Description: "127.0.0.1"},
		{Key: "lan", Label: "Todas as interfaces IPv4", Description: "0.0.0.0 · revise senhas e firewall"},
		{Key: "ipv6", Label: "Todas as interfaces IPv6", Description: ":: · revise senhas e firewall"},
		{Key: "custom", Label: "Endereço personalizado", Description: "IPv4 ou IPv6 específico"},
	}, menu.Settings{Breadcrumb: breadcrumb, Default: defaultIndex, AllowBack: true})
	if err != nil || back {
		return "", back, err
	}
	switch selected {
	case "custom":
		return menuText(ui, "Endereço IP", current, services.BindAddress)
	case "lan":
		return "0.0.0.0", false, nil
	case "ipv6":
		return "::", false, nil
	}
	return "127.0.0.1", false, nil
}

// menuPort mirrors menu_port.
func menuPort(ui *menu.UI, label string, current int, breadcrumb string) (int, bool, error) {
	if breadcrumb == "" {
		breadcrumb = "x86QW › Serviços › " + label + " › Rede"
	}
	selected, back, err := ui.SelectOne("Porta do "+label, []menu.Option{
		{Key: "default", Label: strconv.Itoa(current), Description: "porta padrão"},
		{Key: "custom", Label: "Outra porta", Description: "escolher entre 1024 e 65535"},
	}, menu.Settings{Breadcrumb: breadcrumb, AllowBack: true})
	if err != nil || back {
		return 0, back, err
	}
	if selected != "custom" {
		return current, false, nil
	}
	value, back, err := menuText(ui, "Porta", strconv.Itoa(current), func(text string) (string, error) {
		port, err := strconv.Atoi(text)
		if err != nil {
			return "", errors.New("deve ser um número inteiro")
		}
		if port < 1024 || port > 65535 {
			return "", errors.New("deve estar entre 1024 e 65535")
		}
		return text, nil
	})
	if err != nil || back {
		return 0, back, err
	}
	port, _ := strconv.Atoi(value)
	return port, false, nil
}

// menuExecutionMode mirrors menu_execution_mode; false means back.
func menuExecutionMode(ui *menu.UI, background *bool, breadcrumb string) (bool, error) {
	selected, back, err := ui.SelectOne("Como manter o serviço?", []menu.Option{
		{Key: "foreground", Label: "Primeiro plano", Description: "terminal acompanha a execução", Detail: "Ctrl+C encerra a stack coordenadamente."},
		{Key: "background", Label: "Segundo plano", Description: "liberar o terminal após a inicialização", Detail: "Consulte e encerre pela área Serviços."},
	}, menu.Settings{Breadcrumb: breadcrumb + " › Execução", Default: boolIndex(*background), AllowBack: true})
	if err != nil || back {
		return false, err
	}
	*background = selected == "background"
	return true, nil
}

// applyQuickHostDefaults mirrors apply_quick_host_defaults.
func applyQuickHostDefaults(o *services.HostOptions) {
	o.Bind, o.Port, o.Maxclients, o.NoMVD = "127.0.0.1", 28501, 16, false
	o.WithQTV, o.QtvBind, o.QtvPort = false, "127.0.0.1", 28000
	o.WithProxy, o.ProxyBind, o.ProxyPort = false, "127.0.0.1", 30000
	o.Background = false
	o.PromptPassword, o.PromptSpectatorPassword, o.PromptRconPassword, o.PromptQtvPassword = false, false, false, false
	o.Password, o.SpectatorPassword, o.RconPassword, o.QtvPassword = "", "", "", ""
	o.PasswordFile, o.SpectatorPasswordFile, o.RconPasswordFile, o.QtvPasswordFile = "", "", "", ""
}

// configureAdvancedHost mirrors configure_advanced_host_menu; false
// returns to the profile choice.
func configureAdvancedHost(ui *menu.UI, o *services.HostOptions) (bool, error) {
	const mvdsvNet = "x86QW › Hospedar › Avançado › MVDSV › Rede"
	const qtvNet = "x86QW › Hospedar › Avançado › QTV › Rede"
	const proxyNet = "x86QW › Hospedar › Avançado › QWFWD › Rede"
	stage := 0
	for stage < 12 {
		switch stage {
		case 0:
			bind, back, err := menuBind(ui, "MVDSV", o.Bind, mvdsvNet)
			if err != nil {
				return false, err
			}
			if back {
				return false, nil
			}
			o.Bind = bind
		case 1:
			port, back, err := menuPort(ui, "MVDSV", o.Port, mvdsvNet)
			if err != nil {
				return false, err
			}
			if back {
				stage--
				continue
			}
			o.Port = port
		case 2:
			clients, back, err := ui.SelectOne("Máximo de clientes", []menu.Option{
				{Key: "8", Label: "8", Description: "partida pequena"},
				{Key: "16", Label: "16", Description: "padrão recomendado"},
				{Key: "24", Label: "24", Description: "servidor grande"},
				{Key: "32", Label: "32", Description: "limite atual"},
			}, menu.Settings{Breadcrumb: "x86QW › Hospedar › Capacidade", Default: 1, AllowBack: true})
			if err != nil {
				return false, err
			}
			if back {
				stage--
				continue
			}
			o.Maxclients, _ = strconv.Atoi(clients)
		case 3:
			record, back, err := ui.Confirm("Gravar demos MVD automaticamente?", menu.Settings{Breadcrumb: "x86QW › Hospedar › Gravação", AllowBack: true}, "", true)
			if err != nil {
				return false, err
			}
			if back {
				stage--
				continue
			}
			o.NoMVD = !record
		case 4:
			withQTV, back, err := ui.Confirm("Iniciar QTV conectado ao servidor?", menu.Settings{Breadcrumb: "x86QW › Hospedar › Serviços adicionais", AllowBack: true}, "", false)
			if err != nil {
				return false, err
			}
			if back {
				stage--
				continue
			}
			o.WithQTV = withQTV
			if !withQTV {
				stage = 6
			}
		case 5:
			bind, back, err := menuBind(ui, "QTV", o.QtvBind, qtvNet)
			if err != nil {
				return false, err
			}
			if back {
				stage--
				continue
			}
			o.QtvBind = bind
		case 6:
			if o.WithQTV {
				port, back, err := menuPort(ui, "QTV", o.QtvPort, qtvNet)
				if err != nil {
					return false, err
				}
				if back {
					stage--
					continue
				}
				o.QtvPort = port
			}
		case 7:
			withProxy, back, err := ui.Confirm("Iniciar também o proxy QWFWD?", menu.Settings{Breadcrumb: "x86QW › Hospedar › Serviços adicionais", AllowBack: true}, "", false)
			if err != nil {
				return false, err
			}
			if back {
				stage = 4
				if o.WithQTV {
					stage = 6
				}
				continue
			}
			o.WithProxy = withProxy
			if !withProxy {
				stage = 9
			}
		case 8:
			bind, back, err := menuBind(ui, "QWFWD", o.ProxyBind, proxyNet)
			if err != nil {
				return false, err
			}
			if back {
				stage--
				continue
			}
			o.ProxyBind = bind
		case 9:
			if o.WithProxy {
				port, back, err := menuPort(ui, "QWFWD", o.ProxyPort, proxyNet)
				if err != nil {
					return false, err
				}
				if back {
					stage--
					continue
				}
				o.ProxyPort = port
			}
		case 10:
			passwords, back, err := ui.Confirm("Configurar senhas com entrada oculta?", menu.Settings{Breadcrumb: "x86QW › Hospedar › Segurança", AllowBack: true},
				"As senhas não serão colocadas na linha de comando.", false)
			if err != nil {
				return false, err
			}
			if back {
				stage = 7
				if o.WithProxy {
					stage = 9
				}
				continue
			}
			if passwords {
				o.PromptPassword, o.PromptSpectatorPassword, o.PromptRconPassword = true, true, true
				if o.WithQTV {
					o.PromptQtvPassword = true
				}
			}
		default:
			ok, err := menuExecutionMode(ui, &o.Background, "x86QW › Hospedar › Avançado")
			if err != nil {
				return false, err
			}
			if !ok {
				stage = 10
				continue
			}
		}
		stage++
	}
	return true, nil
}

// hostCommandArguments mirrors host_command_arguments (no secrets).
func hostCommandArguments(o *services.HostOptions, game *play.Game, mode *play.Mode, mapName string, options play.Options) []string {
	arguments := []string{"host", game.ID}
	if mode != nil {
		arguments = append(arguments, "--mode", mode.Key)
		arguments = append(arguments, play.OptionsCLIArguments(options, true)...)
	}
	arguments = append(arguments, "--map", mapName, "--bind", o.Bind, "--port", strconv.Itoa(o.Port), "--maxclients", strconv.Itoa(o.Maxclients))
	if o.Hostname != "" {
		arguments = append(arguments, "--hostname", o.Hostname)
	}
	if o.NoMVD {
		arguments = append(arguments, "--no-mvd")
	}
	if o.WithQTV {
		arguments = append(arguments, "--with-qtv", "--qtv-bind", o.QtvBind, "--qtv-port", strconv.Itoa(o.QtvPort))
	}
	if o.WithProxy {
		arguments = append(arguments, "--with-proxy", "--proxy-bind", o.ProxyBind, "--proxy-port", strconv.Itoa(o.ProxyPort))
	}
	if o.Background {
		arguments = append(arguments, "--background")
	}
	for _, flag := range []struct {
		enabled bool
		name    string
	}{{o.PromptPassword, "--prompt-password"}, {o.PromptSpectatorPassword, "--prompt-spectator-password"},
		{o.PromptRconPassword, "--prompt-rcon-password"}, {o.PromptQtvPassword, "--prompt-qtv-password"}} {
		if flag.enabled {
			arguments = append(arguments, flag.name)
		}
	}
	return arguments
}

func plane(background bool) string {
	if background {
		return "segundo plano"
	}
	return "primeiro plano"
}

// hostSummaryText mirrors host_summary_text.
func hostSummaryText(o *services.HostOptions, game *play.Game, mode *play.Mode, mapName string, options play.Options, profile string) string {
	lines := []string{"Resumo da hospedagem", "  Jogo       | " + game.Label}
	if mode != nil {
		lines = append(lines, "  Modo       | "+mode.Label)
		for _, line := range play.SummaryLines(options) {
			label, value, _ := strings.Cut(line, "|")
			lines = append(lines, fmt.Sprintf("  %-11s|%s", strings.TrimSpace(label), value))
		}
	}
	profileLabel := "Avançado"
	if profile == "quick" {
		profileLabel = "Rápido local"
	}
	recording := "MVD automática"
	if o.NoMVD {
		recording = "desativada"
	}
	qtv, proxy := "desativado", "desativado"
	if o.WithQTV {
		qtv = "ativo em " + services.Endpoint(o.QtvBind, o.QtvPort)
	}
	if o.WithProxy {
		proxy = "ativo em " + services.Endpoint(o.ProxyBind, o.ProxyPort)
	}
	secrets := 0
	for _, value := range []string{o.Password, o.SpectatorPassword, o.RconPassword, o.QtvPassword} {
		if value != "" {
			secrets++
		}
	}
	for _, prompt := range []bool{o.PromptPassword, o.PromptSpectatorPassword, o.PromptRconPassword, o.PromptQtvPassword} {
		if prompt {
			secrets++
		}
	}
	lines = append(lines,
		"  Mapa       | "+mapName,
		"  Perfil     | "+profileLabel,
		"  Execução   | "+plane(o.Background),
		fmt.Sprintf("  MVDSV      | %s · %d clientes", services.Endpoint(o.Bind, o.Port), o.Maxclients),
		"  Gravação   | "+recording,
		"  QTV        | "+qtv,
		"  QWFWD      | "+proxy,
		fmt.Sprintf("  Segurança  | %d segredo(s) configurado(s); valores redigidos", secrets),
		"",
		"Comando equivalente seguro:",
		"  "+play.PublicCommand(hostCommandArguments(o, game, mode, mapName, options)),
	)
	return strings.Join(lines, "\n")
}

// serviceMenu mirrors configure_service_menu plus the confirmation loop.
func serviceMenu(ui *menu.UI, op *services.Operation) error {
	for {
		var ok bool
		var err error
		if op.Action == "proxy" {
			ok, err = configureProxy(ui, op.Proxy)
		} else {
			ok, err = configureQtv(ui, op.Qtv)
		}
		if err != nil {
			return err
		}
		if !ok {
			return errCancelledByUser
		}
		crumb, description := "x86QW › Serviços › QWFWD › Confirmação", "iniciar o proxy QWFWD com os parâmetros acima"
		if op.Action == "qtv" {
			crumb, description = "x86QW › Serviços › QTV › Confirmação", "iniciar o relay QTV com os parâmetros acima"
		}
		title := "Iniciar este serviço?"
		confirmed, back, err := ui.Confirm(title, menu.Settings{Breadcrumb: crumb, Subtitle: "\n" + serviceSummaryText(op), AllowBack: true}, description, true)
		if err != nil {
			return err
		}
		if back {
			continue
		}
		if !confirmed {
			return errCancelledByUser
		}
		return nil
	}
}

func configureProxy(ui *menu.UI, o *services.ProxyOptions) (bool, error) {
	for {
		bind, back, err := menuBind(ui, "QWFWD", o.ProxyBind, "")
		if err != nil || back {
			return false, err
		}
		o.ProxyBind = bind
		port, back, err := menuPort(ui, "QWFWD", o.ProxyPort, "")
		if err != nil {
			return false, err
		}
		if back {
			continue
		}
		o.ProxyPort = port
		break
	}
	return menuExecutionMode(ui, &o.Background, "x86QW › Serviços › QWFWD")
}

func configureQtv(ui *menu.UI, o *services.QtvOptions) (bool, error) {
	stage := 0
	for stage < 5 {
		switch stage {
		case 0:
			bind, back, err := menuBind(ui, "QTV", o.Bind, "")
			if err != nil || back {
				return false, err
			}
			o.Bind = bind
		case 1:
			port, back, err := menuPort(ui, "QTV", o.Port, "")
			if err != nil {
				return false, err
			}
			if back {
				stage--
				continue
			}
			o.Port = port
		case 2:
			upstream, back, err := ui.SelectOne("Origem da transmissão", []menu.Option{
				{Key: "none", Label: "Sem upstream inicial", Description: "iniciar QTV isoladamente"},
				{Key: "custom", Label: "Conectar a um MVDSV", Description: "informar host e porta"},
			}, menu.Settings{Breadcrumb: "x86QW › Serviços › QTV", Default: boolIndex(o.Upstream != ""), AllowBack: true})
			if err != nil {
				return false, err
			}
			if back {
				stage--
				continue
			}
			if upstream != "custom" {
				o.Upstream = ""
				stage = 4
				continue
			}
			value, back, err := menuText(ui, "Endpoint MVDSV", "127.0.0.1:28501", services.ParseNetworkEndpoint)
			if err != nil {
				return false, err
			}
			if back {
				continue
			}
			o.Upstream = value
		case 3:
			prompt, back, err := ui.Confirm("Solicitar senha do upstream?", menu.Settings{Breadcrumb: "x86QW › Serviços › QTV › Segurança", AllowBack: true}, "", false)
			if err != nil {
				return false, err
			}
			if back {
				stage--
				continue
			}
			o.PromptQtvPassword = prompt
		default:
			ok, err := menuExecutionMode(ui, &o.Background, "x86QW › Serviços › QTV")
			if err != nil {
				return false, err
			}
			if !ok {
				stage = 2
				if o.Upstream != "" {
					stage = 3
				}
				continue
			}
		}
		stage++
	}
	return true, nil
}

// serviceCommandArguments mirrors service_command_arguments (no secrets).
func serviceCommandArguments(op *services.Operation) []string {
	if op.Action == "proxy" {
		arguments := []string{"proxy", "--bind", op.Proxy.ProxyBind, "--port", strconv.Itoa(op.Proxy.ProxyPort)}
		if op.Proxy.Background {
			arguments = append(arguments, "--background")
		}
		return arguments
	}
	o := op.Qtv
	arguments := []string{"qtv", "--bind", o.Bind, "--port", strconv.Itoa(o.Port), "--hostname", o.Hostname}
	if o.Upstream != "" {
		arguments = append(arguments, "--upstream", o.Upstream)
	}
	if o.QtvPassword != "" || o.PromptQtvPassword || o.QtvPasswordFile != "" {
		arguments = append(arguments, "--prompt-qtv-password")
	}
	if o.Background {
		arguments = append(arguments, "--background")
	}
	return arguments
}

// serviceSummaryText mirrors service_summary_text.
func serviceSummaryText(op *services.Operation) string {
	var lines []string
	background := false
	if op.Action == "proxy" {
		o := op.Proxy
		background = o.Background
		lines = []string{"Resumo do serviço", "  Serviço   | QWFWD", "  Endpoint  | " + services.Endpoint(o.ProxyBind, o.ProxyPort) + "/UDP", "  Protocolo | proxy UDP QuakeWorld"}
	} else {
		o := op.Qtv
		background = o.Background
		upstream := o.Upstream
		if upstream == "" {
			upstream = "isolado; sem origem inicial"
		}
		security := "sem segredo de upstream"
		if o.QtvPassword != "" || o.PromptQtvPassword || o.QtvPasswordFile != "" {
			security = "segredo do upstream configurado; valor redigido"
		}
		lines = []string{"Resumo do serviço", "  Serviço   | QTV", "  Nome      | " + o.Hostname,
			"  HTTP      | http://" + services.Endpoint(o.Bind, o.Port) + "/", "  Upstream  | " + upstream, "  Segurança | " + security}
	}
	lines = append(lines, "  Execução  | "+plane(background), "", "Comando equivalente seguro:", "  "+play.PublicCommand(serviceCommandArguments(op)))
	return strings.Join(lines, "\n")
}
