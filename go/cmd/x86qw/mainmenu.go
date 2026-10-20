package main

import (
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"

	"github.com/x86dx2/x86qw/go/internal/buildinfo"
	"github.com/x86dx2/x86qw/go/internal/menu"
	"github.com/x86dx2/x86qw/go/internal/output"
	"github.com/x86dx2/x86qw/go/internal/play"
)

const (
	unixBootstrap       = "curl -fsS https://qw.x86.com.br/install.sh | bash"
	powershellBootstrap = "& { Add-Type -AssemblyName System.Net.Http; $h = [System.Net.Http.HttpClientHandler]::new(); " +
		"$h.AllowAutoRedirect = $false; $c = [System.Net.Http.HttpClient]::new($h); " +
		"$c.Timeout = [TimeSpan]::FromSeconds(60); $c.MaxResponseContentBufferSize = 262144; " +
		"$r = $null; try { $r = $c.GetAsync('https://qw.x86.com.br/install.ps1')." +
		"GetAwaiter().GetResult(); if (-not $r.IsSuccessStatusCode) { throw \"x86QW: HTTP " +
		"$([int]$r.StatusCode).\" }; if ($r.Content.Headers.ContentLength -gt 262144) { " +
		"throw 'x86QW: bootstrap excedeu 262144 bytes.' }; $s = $r.Content.ReadAsStringAsync()." +
		"GetAwaiter().GetResult(); & ([scriptblock]::Create($s)) @args } finally { if ($null -ne $r) " +
		"{ $r.Dispose() }; $c.Dispose(); $h.Dispose() } }"
	commandList = "Comandos: play, host, proxy, qtv, status, hub, update, upgrade, verify, doctor, ui, profile, library, changes, migrate, repair, cleanup, uninstall e version."
)

var mainMenuOptions = []menu.Option{
	{Key: "play", Label: "Jogar", Description: "mods locais e modos KTX", Detail: "Escolha jogo, modo, mapa e regras.", Group: "Partida"},
	{Key: "experience", Label: "Experiência de jogo", Description: "x86QW Ruleset ou regras competitivas", Detail: "Altere a preferência usada ao abrir o cliente.", Group: "Partida"},
	{Key: "hub", Label: "Encontrar servidor", Description: "jogar, observar ou assistir QTV", Detail: "Servidores públicos com busca.", Group: "Partida"},
	{Key: "host", Label: "Hospedar partida", Description: "MVDSV com QTV e QWFWD opcionais", Detail: "Servidor dedicado em primeiro plano.", Group: "Partida"},
	{Key: "services", Label: "Serviços", Description: "visualizar, transmitir ou usar proxy", Detail: "Estado da stack, QTV e QWFWD isolados.", Group: "Instalação"},
	{Key: "manage", Label: "Gerenciar instalação", Description: "atualizar, reparar ou limpar", Detail: "Operações seguras sobre conteúdo instalado.", Group: "Instalação"},
	{Key: "info", Label: "Ajuda e informações", Description: "versão, caminhos e comandos", Detail: "A CLI por argumentos continua disponível.", Group: "Instalação"},
	{Key: "exit", Label: "Sair", Description: "encerrar o menu", Group: "Instalação"},
}

var serviceOptions = []menu.Option{
	{Key: "status", Label: "Visualizar serviços ativos", Description: "PIDs, endpoints e parâmetros não sensíveis", Detail: "Use também em outro terminal enquanto a stack estiver ativa."},
	{Key: "stop", Label: "Encerrar serviços ativos", Description: "shutdown coordenado da stack atual", Detail: "Confirma identidade, encerra filhos e remove temporários."},
	{Key: "qtv", Label: "QTV", Description: "relay HTTP/MVD", Detail: "Pode iniciar isolado ou conectado a um MVDSV."},
	{Key: "proxy", Label: "QWFWD", Description: "proxy UDP QuakeWorld", Detail: "Encaminha conexões sem exigir um MVDSV local."},
}

var manageOptions = []menu.Option{
	option("content", "Alterar conteúdo pelo bootstrap", "mostrar o comando para adicionar ou remover componentes"),
	option("update", "Atualizar", "atualizar somente o que já está instalado"),
	option("upgrade", "Incorporar novidades", "convergir com o perfil atual"),
	option("verify", "Verificar integridade", "operação somente leitura"),
	option("doctor", "Diagnosticar instalação", "somente leitura, sem alterar arquivos"),
	option("ui", "Painel local", "HTML somente leitura sobre doctor e library"),
	option("profile", "Perfil pessoal", "backup e restore das configurações user-owned"),
	option("library", "Favoritos e recentes", "servidores locais com origem e freshness"),
	option("changes", "Ver mudanças locais", "comparar a instalação com o baseline registrado"),
	option("migrate", "Migrar metadados", "converter o estado legado para o contrato 1.0"),
	option("repair", "Diagnosticar e reparar", "preserva arquivos pessoais"),
	option("cleanup", "Limpar dados locais", "cache e dados regeneráveis"),
	option("uninstall", "Desinstalar", "preservar ou remover todos os dados"),
}

// stdinIsTerminal is sys.stdin.isatty() (replaced by the tests).
var stdinIsTerminal = func() bool { return isTerminalFile(os.Stdin) }

func runMenuCommand(args []string, stdout, stderr io.Writer) int {
	target := defaultTarget()
	verbose, noColor := false, false
	for _, arg := range args {
		switch arg {
		case "-v", "--verbose":
			verbose = true
		case "--no-color":
			noColor = true
		default:
			target = arg
		}
	}
	absolute, _ := filepath.Abs(target)
	return runMainMenu(absolute, verbose, noColor, stdout, stderr)
}

// runMainMenu mirrors manager.run_main_menu plus manager.main's handling of
// MenuExit, MenuCancelled and KeyboardInterrupt around it.
func runMainMenu(target string, verbose, noColor bool, stdout, stderr io.Writer) int {
	con := newConsole(stdout, stderr, noColor, verbose)
	stop := trapInterrupt(stderr, "Operação cancelada. Nenhuma seleção pendente foi aplicada.")
	defer stop()
	code, err := mainMenuLoop(interactiveUI(noColor), con, target, verbose, noColor)
	switch {
	case err == nil:
		return code
	case errors.Is(err, menu.ErrInterrupt):
		con.failure("Operação cancelada. Nenhuma seleção pendente foi aplicada.")
		return exitInterrupted
	case errors.Is(err, menu.ErrExit):
		con.info("Menu encerrado; nenhuma seleção pendente foi aplicada.")
		return output.ExitSuccess
	case errors.Is(err, menu.ErrCancelled):
		con.info("Operação cancelada; nenhuma seleção pendente foi aplicada.")
		return exitInterrupted
	}
	return con.failed(err)
}

func mainMenuLoop(ui *menu.UI, con console, target string, verbose, noColor bool) (int, error) {
	breadcrumb := "x86QW " + buildinfo.Version
	common := func(args ...string) []string {
		if verbose {
			args = append(args, "--verbose")
		}
		if noColor {
			args = append(args, "--no-color")
		}
		return args
	}
	stdout, stderr := con.out, con.err
	pause := func(message string) bool { return ui.Pause(message) }
	for {
		selected, back, err := ui.SelectOne("O que você quer fazer?", mainMenuOptions, menu.Settings{Breadcrumb: breadcrumb, Searchable: true, AllowBack: true})
		if errors.Is(err, menu.ErrCancelled) && !stdinIsTerminal() {
			launcher := play.Launcher()
			fmt.Fprintf(stdout, "\nUso: %s <comando> [opções]\n", launcher)
			fmt.Fprintf(stdout, "Exemplo: %s play\n", launcher)
			fmt.Fprintln(stdout, commandList)
			return output.ExitSuccess, nil
		}
		if err != nil {
			return 0, err
		}
		if back || selected == "exit" {
			fmt.Fprintln(stdout, "\nAté a próxima partida.")
			return output.ExitSuccess, nil
		}
		switch selected {
		case "play", "experience":
			flag := "--menu"
			if selected == "experience" {
				flag = "--configure-ruleset"
			}
			result, err := playMain(common("--target", target, flag), stdout, stderr, true)
			if err != nil {
				return 0, err
			}
			if result == exitInterrupted {
				return result, nil
			}
			if !pause("\nPressione Enter para retornar ao menu principal...") {
				return result, nil
			}
		case "host", "hub":
			var result int
			if selected == "host" {
				code, err := serviceMain("host", common("--target", target, "--menu"), stdout, stderr, true)
				if err != nil {
					return 0, err
				}
				result = code
			} else {
				result = dispatch(con, selected, common(target))
			}
			if result == exitInterrupted {
				return result, nil
			}
			if !pause("\nPressione Enter para retornar ao menu principal...") {
				return result, nil
			}
		case "services":
			for {
				service, back, err := ui.SelectOne("Serviços x86QW", serviceOptions, menu.Settings{Breadcrumb: breadcrumb + " › Serviços", Searchable: true, AllowBack: true})
				if err != nil {
					return 0, err
				}
				if back {
					break
				}
				var result int
				switch service {
				case "qtv", "proxy":
					code, err := serviceMain(service, common("--target", target, "--menu"), stdout, stderr, true)
					if err != nil {
						return 0, err
					}
					result = code
				case "stop":
					result = dispatch(con, "status", common("--target", target, "--menu", "--stop"))
				default:
					result = dispatch(con, service, common("--target", target, "--menu"))
				}
				if result == exitInterrupted {
					return result, nil
				}
				if !pause("\nPressione Enter para retornar ao menu de serviços...") {
					return result, nil
				}
			}
		case "manage":
			code, done, err := manageMenu(ui, con, target, breadcrumb, common)
			if err != nil || done {
				return code, err
			}
		case "info":
			fmt.Fprintf(stdout, "\nx86QW %s\n", buildinfo.Version)
			fmt.Fprintf(stdout, "Instalação: %s\n", target)
			fmt.Fprintln(stdout, "Comandos: play, host, hub, qtv, proxy, status, update, upgrade, verify, doctor, ui, profile, library, changes, migrate, repair, cleanup, uninstall e version.")
			fmt.Fprintf(stdout, "Use %s <comando> --help para ver todas as opções avançadas.\n", play.Launcher())
			fmt.Fprintln(stdout, "No menu: ↑↓ navega, →/Enter seleciona, ← volta e Esc sai; / busca quando aparecer na legenda.")
			if !pause("\nPressione Enter para voltar ao menu...") {
				return output.ExitSuccess, nil
			}
		}
	}
}

// manageMenu is the "Gerenciar instalação" branch; done=true ends the menu.
func manageMenu(ui *menu.UI, con console, target, breadcrumb string, common func(...string) []string) (int, bool, error) {
	stdout := con.out
	crumb := breadcrumb + " › Gerenciar instalação"
	action, back, err := ui.SelectOne("Gerenciar instalação", manageOptions, menu.Settings{Breadcrumb: crumb, Searchable: true, AllowBack: true})
	if err != nil || back {
		return 0, false, err
	}
	if action == "content" {
		bootstrap := unixBootstrap
		if runtime.GOOS == "windows" {
			bootstrap = powershellBootstrap
		}
		fmt.Fprintln(stdout, "\nA CLI instalada não baixa componentes novos durante o gameplay.")
		fmt.Fprintln(stdout, "Reexecute o bootstrap e informe este mesmo destino:")
		fmt.Fprintf(stdout, "\n  %s\n", bootstrap)
		fmt.Fprintf(stdout, "\nDestino atual: %s\n", target)
		if !ui.Pause("\nPressione Enter para voltar ao menu...") {
			return output.ExitSuccess, true, nil
		}
		return 0, false, nil
	}
	var extra []string
	switch action {
	case "cleanup":
		scope := ""
		for scope == "" {
			chosen, back, err := ui.SelectOne("O que deseja limpar?", []menu.Option{
				option("cache", "Somente cache", "mantém downloads, histórico, logs e demos"),
				option("downloads", "Cache e downloads", "mantém histórico, logs e demos"),
				option("personal", "Cache e dados pessoais", "remove histórico, logs e demos"),
				option("all", "Todos os dados locais", "remove downloads, histórico, logs e demos"),
			}, menu.Settings{Breadcrumb: crumb + " › Limpeza", Searchable: true, AllowBack: true})
			if err != nil {
				return 0, false, err
			}
			if back {
				return 0, false, nil
			}
			if chosen == "personal" || chosen == "all" {
				fmt.Fprintln(stdout, "\nSerão removidos dados pessoais locais:")
				fmt.Fprintln(stdout, "  - histórico do console")
				fmt.Fprintln(stdout, "  - logs do ezQuake")
				fmt.Fprintln(stdout, "  - demos de Total Destruction 2")
				if chosen == "all" {
					fmt.Fprintln(stdout, "  - downloads recebidos de servidores")
				}
				yes, back, err := ui.Confirm("Confirma a limpeza destes dados?", menu.Settings{Breadcrumb: crumb + " › Limpeza › Confirmação", AllowBack: true}, "remover permanentemente os dados listados", false)
				if err != nil {
					return 0, false, err
				}
				if back {
					continue
				}
				if !yes {
					return 0, false, nil
				}
			}
			scope = chosen
		}
		if scope == "downloads" || scope == "all" {
			extra = append(extra, "--downloads")
		}
		if scope == "personal" || scope == "all" {
			extra = append(extra, "--personal-data")
		}
	case "uninstall":
		scope := ""
		for scope == "" {
			chosen, back, err := ui.SelectOne("Como deseja desinstalar?", []menu.Option{
				option("preserve", "Preservar dados pessoais", "mantém PAKs, configurações, logs e demos"),
				option("purge", "Remover completamente", "apaga toda a instalação e o cache"),
			}, menu.Settings{Breadcrumb: crumb + " › Desinstalar", Searchable: true, AllowBack: true})
			if err != nil {
				return 0, false, err
			}
			if back {
				return 0, false, nil
			}
			yes, back, err := ui.Confirm("Confirma a desinstalação?", menu.Settings{Breadcrumb: crumb + " › Desinstalar › Confirmação", AllowBack: true}, "Esta operação altera o conteúdo instalado.", false)
			if err != nil {
				return 0, false, err
			}
			if back {
				continue
			}
			if !yes {
				return 0, false, nil
			}
			scope = chosen
		}
		if scope == "purge" {
			extra = append(extra, "--purge")
		}
	}
	result := dispatch(con, action, common(append([]string{target}, extra...)...))
	if action == "uninstall" && result == output.ExitSuccess {
		return output.ExitSuccess, true, nil
	}
	if !ui.Pause("\nPressione Enter para retornar ao menu principal...") {
		return result, true, nil
	}
	return 0, false, nil
}

// dispatch runs another registered command from the menu; commands not yet
// ported to the native CLI fail clearly instead of falling back to Python.
func dispatch(con console, name string, args []string) int {
	cmd, ok := commands[name]
	if !ok {
		con.failure(fmt.Sprintf("O comando %s ainda não está disponível na CLI nativa.", name))
		return output.ExitFailure
	}
	return cmd.run(name, args, con.out, con.err)
}

func init() {
	commands["menu"] = command{order: -1, run: noName(runMenuCommand)}
}
