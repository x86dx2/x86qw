package main

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/menu"
)

// installRequest is what `install` needs; empty fields are asked for.
type installRequest struct {
	target, platform, channel, release, profile string
	requested                                   []string
}

var profileLabels = map[string]string{"essential": "Essencial", "recommended": "Recomendado", "complete": "Completo", "custom": "Personalizado", "none": "Somente cliente"}

// chooseTarget mirrors choose_public_target.
func chooseTarget(ui *menu.UI) (string, error) {
	home, _ := os.UserHomeDir()
	suggested := filepath.Join(home, "Games", "x86qw")
	answer, err := ui.PromptText("Onde deseja instalar o x86QW?", suggested, "Pressione Enter para aceitar ou informe outro diretório.")
	if err != nil {
		return "", errors.New("Não foi possível ler o diretório de instalação. Execute em um terminal interativo ou informe o caminho na linha de comando.")
	}
	if answer == "~" || strings.HasPrefix(answer, "~/") {
		answer = filepath.Join(home, answer[1:])
	}
	return filepath.Abs(answer)
}

// installWizard mirrors the interactive part of ManagerInstaller.install:
// platform report, channel, release and content pickers, then the plan
// confirmation. It returns false when the user declined the plan.
func installWizard(ui *menu.UI, con console, cat *catalog.Catalog, inventory *catalog.Inventory, request *installRequest, platformGiven, channelGiven bool) (bool, error) {
	var spec *catalog.ClientSpec
	for index := range catalog.ClientSpecs {
		if catalog.ClientSpecs[index].Platform == request.platform {
			spec = &catalog.ClientSpecs[index]
		}
	}
	if spec == nil {
		return false, fmt.Errorf("plataforma desconhecida: %s", request.platform)
	}
	detected := install.HostPlatform()
	switch {
	case !platformGiven:
		con.success("Sistema detectado automaticamente: " + spec.Label + ".")
	case request.platform == detected:
		con.success("Cliente solicitado por --platform: " + spec.Label + ".")
	default:
		for _, other := range catalog.ClientSpecs {
			if other.Platform == detected {
				detected = other.Label
			}
		}
		con.info("Cliente solicitado por --platform: " + spec.Label + "; host detectado: " + detected + ".")
	}
	if !channelGiven {
		channel, _, err := ui.SelectOne("Qual canal deseja instalar?", []menu.Option{
			{Key: "stable", Label: "Stable", Description: "releases oficiais", Aliases: []string{"s"}},
			{Key: "nightly", Label: "Nightly", Description: "snapshots de desenvolvimento", Aliases: []string{"n"}},
		}, menu.Settings{Breadcrumb: "x86QW › Instalação › Canal", InvalidMessage: "Opção inválida. Digite 1 para stable ou 2 para nightly."})
		if err != nil {
			return false, err
		}
		request.channel = channel
	}
	con.success("Canal selecionado: " + request.channel)
	releases, err := cat.ClientReleases(*spec, request.channel)
	if err != nil {
		return false, err
	}
	if request.release == "" {
		if request.release, err = chooseRelease(ui, con, request.channel, releases); err != nil {
			return false, err
		}
	} else if request.release == "latest" {
		request.release = releases[0].Version
	}
	con.success("Versão selecionada: " + request.release)
	if request.profile == "" {
		if err := chooseContent(ui, con, cat, inventory, request); err != nil {
			return false, err
		}
	}
	selected, err := inventory.Desired(request.profile, request.requested)
	if err != nil {
		return false, err
	}
	summary := fmt.Sprintf("Destino: %s\nCliente: ezQuake %s %s %s\nPerfil: %s · %d componentes", request.target, spec.Label, request.channel, request.release, profileLabels[request.profile], len(selected))
	return confirmInstall(ui, con, summary)
}

// chooseRelease mirrors prompt_catalog.
func chooseRelease(ui *menu.UI, con console, channel string, releases []catalog.Package) (string, error) {
	if ui.Navigation {
		options := make([]menu.Option, len(releases))
		for index, release := range releases {
			description := ""
			if index == 0 {
				description = "mais recente"
			}
			options[index] = menu.Option{Key: release.Version, Label: release.Version, Description: description, Aliases: []string{strconv.Itoa(index + 1)}}
		}
		key, _, err := ui.SelectOne("Versões "+channel+" disponíveis", options, menu.Settings{
			Breadcrumb: "x86QW › Instalação › Versão", Subtitle: "Mais recente primeiro. Use a busca para localizar uma versão.", Searchable: true,
		})
		return key, err
	}
	out := ui.Out
	visible := releases
	if len(visible) > 12 {
		visible = visible[:12]
	}
	fmt.Fprintf(out, "\nVersões %s disponíveis (mais recente primeiro):\n", channel)
	for index, release := range visible {
		fmt.Fprintf(out, "  %3d) %s\n", index+1, release.Version)
	}
	hidden := len(releases) - len(visible)
	if hidden > 0 {
		fmt.Fprintf(out, "       ... mais %d versões. Digite t para mostrar todas.\n", hidden)
	}
	expanded := hidden == 0
	for {
		prompt := "Escolha o número ou a versão exata"
		if !expanded {
			prompt += ", ou t para listar todas"
		}
		answer, err := ui.ReadLine(prompt + ": ")
		if err != nil {
			return "", errors.New("Nenhuma versão foi selecionada.")
		}
		answer = strings.TrimSpace(answer)
		if folded := strings.ToLower(answer); !expanded && (folded == "t" || folded == "todas" || folded == "all") {
			for index := len(visible); index < len(releases); index++ {
				fmt.Fprintf(out, "  %3d) %s\n", index+1, releases[index].Version)
			}
			expanded = true
			continue
		}
		if number, err := strconv.Atoi(answer); err == nil && isDigits(answer) && number >= 1 && number <= len(releases) {
			return releases[number-1].Version, nil
		}
		for _, release := range releases {
			if release.Version == answer {
				return answer, nil
			}
		}
		con.warning("Versão não encontrada. Use um número da lista ou informe o identificador completo.")
	}
}

func isDigits(value string) bool {
	for _, r := range value {
		if r < '0' || r > '9' {
			return false
		}
	}
	return value != ""
}

// chooseContent mirrors choose_install_content and _resolve_component_selection.
func chooseContent(ui *menu.UI, con console, cat *catalog.Catalog, inventory *catalog.Inventory, request *installRequest) error {
	path, _, err := ui.SelectOne("Qual conteúdo deseja instalar?", []menu.Option{
		option("recommended", "Recomendado", "KTX, mapas e configuração para jogar agora"),
		option("advanced", "Avançado", "essencial, completo, personalizado ou somente cliente"),
	}, menu.Settings{Breadcrumb: "x86QW › Instalação › Conteúdo", InvalidMessage: "Opção inválida. Digite 1 para recomendado ou 2 para avançado."})
	if err != nil {
		return err
	}
	if path == "recommended" {
		request.profile = "recommended"
		return nil
	}
	for {
		choice, _, err := ui.SelectOne("Opções avançadas de conteúdo", []menu.Option{
			option("essential", "Essencial", "configuração, interface principal e KTX"),
			option("complete", "Completo", fmt.Sprintf("todos os %d componentes atuais", len(inventory.Components))),
			option("custom", "Personalizado", "escolha componentes individualmente"),
			option("client-only", "Somente cliente", "ezQuake sem mods; Jogar recusa até adicionar KTX"),
		}, menu.Settings{Breadcrumb: "x86QW › Instalação › Avançado", InvalidMessage: "Opção inválida. Digite 1 a 4."})
		if err != nil {
			return err
		}
		switch choice {
		case "client-only":
			yes, _, err := ui.Confirm("Instalar somente o cliente ezQuake?", menu.Settings{
				Breadcrumb:     "x86QW › Instalação › Avançado › Somente cliente",
				InvalidMessage: "Resposta inválida. Digite s para sim ou n para não.",
			}, "Esta instalação não será jogável. Jogar recusará até você adicionar ao menos KTX.", false)
			if err != nil {
				return err
			}
			if !yes {
				continue
			}
			con.warning("Somente cliente: Jogar recusará até instalar ao menos KTX.")
			request.profile, request.requested = "none", nil
			return nil
		case "custom":
			options := make([]menu.Option, len(inventory.Components))
			for index, component := range inventory.Components {
				version := ""
				if pkg, err := cat.ComponentPackage(component.ID, inventory.ContentNamespaces); err == nil {
					version = pkg.Version
				}
				options[index] = menu.Option{Key: component.ID, Label: component.Label, Description: version, Detail: component.Description}
			}
			chosen, _, err := ui.SelectMany("Quais componentes deseja instalar?", options, menu.Settings{
				Breadcrumb: "x86QW › Instalação › Perfil personalizado", Subtitle: "Marque com Espaço e pressione Enter para concluir.", Searchable: true,
			}, nil, false)
			if errors.Is(err, menu.ErrInterrupt) || errors.Is(err, menu.ErrExit) {
				return err
			}
			if err != nil || len(chosen) == 0 {
				return errors.New("Nenhum componente x86QW foi selecionado.")
			}
			resolved, err := inventory.Resolve(chosen)
			if err != nil {
				return err
			}
			var added []string
			for _, id := range resolved {
				if !contains(chosen, id) {
					added = append(added, id)
				}
			}
			if len(added) > 0 {
				con.info("Dependências adicionadas automaticamente: " + strings.Join(added, ", "))
			}
			request.profile, request.requested = "custom", chosen
			return nil
		default:
			request.profile = choice
			return nil
		}
	}
}

// confirmInstall mirrors confirm_update_plan("install", assume_yes=False).
func confirmInstall(ui *menu.UI, con console, summary string) (bool, error) {
	if ui.Navigation {
		yes, _, err := ui.Confirm("Deseja iniciar esta instalação?", menu.Settings{Breadcrumb: "x86QW › Instalação › Confirmação", Subtitle: summary}, "instalar os itens apresentados", true)
		return yes, err
	}
	fmt.Fprintf(ui.Out, "\n%s\n", summary)
	for {
		answer, err := ui.ReadLine("\n==> Deseja iniciar a instalação? [Y/n] ")
		if err != nil {
			return false, errors.New("A confirmação não pôde ser lida. Execute em um terminal interativo ou execute install --non-interactive com todas as seleções obrigatórias.")
		}
		switch strings.ToLower(strings.TrimSpace(answer)) {
		case "y", "yes", "s", "sim", "":
			return true, nil
		case "n", "no", "nao", "não":
			con.info("Operação cancelada; nenhum arquivo do jogo foi alterado.")
			return false, nil
		}
		con.warning("Resposta inválida. Digite y para prosseguir ou n para cancelar.")
	}
}
