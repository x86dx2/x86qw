package tui

// Lang is the interface language.
type Lang string

const (
	PT Lang = "pt"
	EN Lang = "en"
)

var texts = map[string][2]string{
	// chrome
	"subtitle":     {"instalador", "installer"},
	"route.intent": {"intenção", "intent"}, "route.config": {"configuração", "setup"}, "route.review": {"pré-voo e revisão", "preflight and review"},
	"route.install": {"instalação", "install"}, "route.done": {"pronto", "done"}, "route.panel": {"painel", "panel"},
	"k.choose": {"escolher", "choose"}, "k.next": {"seguir", "continue"}, "k.back": {"voltar", "back"}, "k.quit": {"sair", "quit"},
	"k.edit": {"editar", "edit"}, "k.install": {"instalar", "install"}, "k.copy": {"copiar receita", "copy recipe"},
	"k.recipe": {"receita", "recipe"}, "k.close": {"fechar", "close"}, "k.tab": {"aba", "tab"}, "k.toggle": {"marcar", "toggle"},
	"k.pause": {"pausar", "pause"}, "k.resume": {"retomar", "resume"}, "k.cancel": {"cancelar", "cancel"}, "k.log": {"log", "log"},
	"k.lang": {"english", "português"}, "k.change": {"mudar", "change"}, "k.theme": {"tema", "theme"},
	"quit.again":   {"pressione de novo para sair", "press again to quit"},
	"cancel.again": {"pressione de novo para cancelar a instalação", "press again to cancel the install"},
	"small":        {"Aumente o terminal para pelo menos 60×18.", "Make the terminal at least 60×18."},
	"demo":         {"demonstração · nada é instalado", "demo · nothing is installed"},

	// intent
	"intent.q":   {"O que você quer fazer?", "What do you want to do?"},
	"play":       {"Jogar", "Play"},
	"play.d":     {"entrar em partidas, treinar com bots, KTX local", "join matches, train with bots, local KTX"},
	"host":       {"Hospedar", "Host"},
	"host.d":     {"servidor dedicado pronto, com QTV e proxy", "a dedicated server ready to run, with QTV and proxy"},
	"both":       {"Jogar e hospedar", "Play and host"},
	"both.d":     {"cliente e servidor na mesma pasta", "client and server in the same folder"},
	"nick":       {"nick", "nick"},
	"nick.ph":    {"como você aparece no placar", "how you show on the scoreboard"},
	"nick.need":  {"Escolha um nick para seguir.", "Pick a nick to continue."},
	"nick.shows": {"aparece como", "shows as"},
	"nick.team":  {"nas mensagens do time", "in team messages"},

	// preflight
	"pre.title":  {"Pré-voo", "Preflight"},
	"pre.lead":   {"Conferindo a máquina com as suas escolhas antes de baixar qualquer coisa.", "Checking the machine against your choices before anything is downloaded."},
	"pre.anyway": {"instalar mesmo assim", "install anyway"}, "k.adjust": {"ajustar", "adjust"},

	// review
	"rev.download": {"download", "download"}, "rev.disk": {"em disco", "on disk"}, "rev.items": {"componentes", "components"}, "rev.time": {"tempo", "time"},
	"sec.comps": {"Componentes", "Components"}, "sec.config": {"Sua configuração", "Your setup"}, "sec.server": {"Servidor", "Server"},
	"sec.integ": {"Integração", "Integration"}, "sec.folder": {"Pasta", "Folder"}, "sec.recipe": {"Receita", "Recipe"},
	"recipe.d":      {"O mesmo resultado em outra máquina, sem perguntas.", "The same result on another machine, no questions asked."},
	"recipe.copied": {"Receita copiada", "Recipe copied"},

	// components
	"profile":     {"Pacote", "Bundle"},
	"p.essential": {"Essencial", "Essential"}, "p.recommended": {"Recomendado", "Recommended"}, "p.complete": {"Completo", "Complete"}, "p.custom": {"Personalizado", "Custom"},
	"p.essential.d": {"KTX com o mínimo", "KTX with the bare minimum"}, "p.recommended.d": {"mapas, skins e texturas", "maps, skins and textures"},
	"p.complete.d": {"todos os mods e o QRP", "every mod and QRP"}, "p.custom.d": {"você escolhe item a item", "pick item by item"},
	"comps.items": {"Itens", "Items"}, "needed.host": {"necessário para hospedar", "needed to host"}, "always": {"sempre", "always"},

	// config tabs
	"tab.keys": {"Teclas", "Keys"}, "tab.mouse": {"Mouse", "Mouse"}, "tab.exp": {"Experiência", "Experience"}, "tab.video": {"Vídeo e som", "Video and sound"},
	"keys.layout": {"Layout de movimento", "Movement layout"},
	"l.wasd":      {"WASD", "WASD"}, "l.esdf": {"ESDF", "ESDF"}, "l.arrows": {"Setas", "Arrows"}, "l.custom": {"Livre", "Free"},
	"l.wasd.d": {"o padrão moderno", "the modern default"}, "l.esdf.d": {"uma coluna à direita", "one column right"},
	"l.arrows.d": {"como em 1996", "the 1996 way"}, "l.custom.d": {"escolha ação e depois a tecla", "pick an action, then a key"},
	"a.fwd": {"frente", "forward"}, "a.back": {"trás", "back"}, "a.left": {"esquerda", "left"}, "a.right": {"direita", "right"}, "a.jump": {"pulo", "jump"},
	"press.key": {"pressione a tecla para", "press the key for"},
	"sens":      {"Sensibilidade", "Sensitivity"}, "dpi": {"DPI do mouse", "Mouse DPI"}, "cm360": {"cm por volta", "cm per turn"},
	"invert":      {"Inverter eixo vertical", "Invert vertical axis"},
	"exp.ruleset": {"x86QW Ruleset", "x86QW Ruleset"}, "exp.ruleset.d": {"assistido: iDrive, smart jump e feedback visual", "assisted: iDrive, smart jump and visual feedback"},
	"exp.comp": {"Competitivo", "Competitive"}, "exp.comp.d": {"o ruleset padrão do ezQuake, como nas ligas", "the default ezQuake ruleset, as in leagues"},
	"res": {"Resolução", "Resolution"}, "res.auto": {"a da tela", "the screen's"}, "display": {"Exibição", "Display"},
	"d.fullscreen": {"tela cheia", "fullscreen"}, "d.borderless": {"sem borda", "borderless"}, "d.windowed": {"janela", "window"},
	"fov": {"Campo de visão", "Field of view"}, "volume": {"Volume", "Volume"},

	// server
	"tab.general": {"Geral", "General"}, "tab.network": {"Rede", "Network"}, "tab.services": {"Serviços", "Services"},
	"srv.name": {"Nome", "Name"}, "srv.base": {"Porta inicial", "First port"}, "srv.inst": {"Instâncias", "Instances"},
	"srv.add": {"adicionar instância", "add instance"}, "bots": {"bots", "bots"}, "rcon": {"rcon", "rcon"}, "rcon.new": {"gerar outra", "generate another"},
	"net.private": {"privado", "private"}, "net.public": {"público", "public"}, "net.hostname": {"hostname", "hostname"},
	"net.hostname.ph": {"qw.seudominio.com (opcional)", "qw.yourdomain.com (optional)"},
	"net.detecting":   {"detectando…", "detecting…"}, "net.none": {"indisponível", "unavailable"},
	"dns.match": {"aponta para este IP público", "points at this public IP"}, "dns.other": {"aponta para outro endereço", "points somewhere else"},
	"dns.proxied": {"passa pelo proxy da Cloudflare; UDP não atravessa. Use DNS only (nuvem cinza).", "goes through the Cloudflare proxy; UDP will not pass. Use DNS only (grey cloud)."},
	"dns.fail":    {"não resolve", "does not resolve"},
	"connect.as":  {"jogadores conectam em", "players connect to"},
	"qtv.d":       {"transmissão ao vivo das partidas", "live broadcast of matches"}, "qwfwd.d": {"proxy para quem tem rota ruim", "proxy for players with a bad route"},
	"off": {"desligado", "off"}, "map": {"mapa", "map"}, "bots.on": {"com bots", "with bots"}, "bots.off": {"sem bots", "no bots"}, "srv.del": {"remover #%d", "remove #%d"},
	"folder.state": {"vazia · gravável · 412 GB livres", "empty · writable · 412 GB free"},

	// integration
	"i.short": {"Atalhos do jogo", "Game shortcuts"}, "i.path": {"Comando x86qw no terminal", "x86qw command in the terminal"},
	"i.qw": {"Links qw:// no navegador", "qw:// links in the browser"}, "i.demo": {"Abrir demos .mvd e .qwd", "Open .mvd and .qwd demos"},
	"i.short.macos": {"atalho em Aplicativos", "shortcut in Applications"}, "i.short.linux": {"entrada no menu de aplicativos", "app menu entry"},
	"i.short.windows": {"Menu Iniciar e Área de Trabalho", "Start menu and Desktop"},
	"i.path.unix":     {"link em ~/.local/bin e PATH no shell", "link in ~/.local/bin and PATH in the shell"}, "i.path.windows": {"pasta no PATH do usuário", "folder on the user PATH"},
	"i.qw.d":   {"clicar num servidor numa página conecta direto", "clicking a server on a page connects right away"},
	"i.demo.d": {"duplo clique abre o replay no ezQuake", "double-click replays it in ezQuake"},

	// folder
	"folder.d": {"Fora de pastas protegidas; o jogo grava ao lado de si mesmo.", "Outside protected folders; the game writes next to itself."},

	// install
	"inst.title": {"Instalando", "Installing"},
	"inst.lead":  {"Cada arquivo é conferido pelo SHA-256 assinado antes de entrar na pasta.", "Every file is checked against its signed SHA-256 before it lands."},
	"inst.left":  {"restantes", "left"}, "inst.of": {"de", "of"}, "inst.files": {"arquivos", "files"}, "inst.paused": {"pausado", "paused"},
	"unchanged": {"inalterado", "unchanged"},

	// done
	"done.title": {"Pronto para jogar", "Ready to play"}, "done.host": {"Servidor pronto", "Server ready"},
	"done.lead": {"Instalado, conferido e integrado ao sistema.", "Installed, verified and wired into the system."},
	"go.play":   {"Jogar agora", "Play now"}, "go.play.d": {"abre o ezQuake com a sua configuração", "opens ezQuake with your setup"},
	"go.srv": {"Subir servidor", "Start server"}, "go.srv.d": {"inicia as instâncias, QTV e QWFWD", "starts the instances, QTV and QWFWD"},
	"go.find": {"Encontrar partida", "Find a match"}, "go.find.d": {"servidores públicos com ping", "public servers with ping"},
	"go.quit": {"Sair", "Quit"}, "go.quit.d": {"volta ao terminal", "back to the terminal"},
	"srv.yours": {"Seu servidor", "Your server"}, "ports.open": {"Libere estas portas no roteador para jogadores de fora.", "Open these ports on your router for outside players."},
	"servers": {"Servidores", "Servers"}, "sample": {"dados de exemplo", "sample data"},
	"would.play":    {"Abriria o ezQuake com o seu preset.", "Would open ezQuake with your preset."},
	"would.srv":     {"Subiria as instâncias e mostraria o status.", "Would start the instances and show status."},
	"would.connect": {"Conectaria em", "Would connect to"},

	// panel
	"panel.title": {"Instalação encontrada", "Installation found"},
	"m.update":    {"Atualizar", "Update"}, "m.update.d": {"versões novas disponíveis", "new versions available"},
	"m.change": {"Componentes e configuração", "Components and setup"}, "m.change.d": {"mudar itens, teclas, vídeo e servidor", "change items, keys, video and server"},
	"m.repair": {"Reparar e verificar", "Repair and verify"}, "m.repair.d": {"confere cada arquivo com o recibo assinado", "checks every file against the signed receipt"},
	"m.remove": {"Desinstalar", "Uninstall"}, "m.remove.d": {"remove a instalação e os caches", "removes the install and the caches"},
	"keep.personal": {"Manter dados pessoais", "Keep personal data"}, "remove.again": {"pressione de novo para desinstalar", "press again to uninstall"},
	"removed":         {"A instalação seria removida aqui (demonstração).", "The install would be removed here (demo)."},
	"verify.ok":       {"arquivos conferidos, nenhum divergente", "files checked, none differ"},
	"pre.ok":          {"Tudo certo. Confirme para instalar.", "All clear. Confirm to install."},
	"pre.warn":        {"Uma observação. Corrija nas escolhas acima ou instale mesmo assim.", "One note. Fix it in the choices above or install anyway."},
	"pre.fail":        {"Algo impede a instalação. Corrija nas escolhas acima.", "Something blocks the install. Fix it in the choices above."},
	"conf.title":      {"Configuração", "Setup"},
	"conf.lead":       {"Os padrões já servem. Enter abre cada item para ajustar.", "The defaults already work. Enter opens each item to adjust it."},
	"conf.go":         {"Instalar", "Install"},
	"rev.title":       {"Pré-voo e revisão", "Preflight and review"},
	"rev.lead":        {"A máquina é conferida com as suas escolhas. Nada é instalado até você confirmar.", "The machine is checked against your choices. Nothing is installed until you confirm."},
	"rule.pre":        {"pré-voo", "preflight"},
	"rule.choices":    {"suas escolhas", "your choices"},
	"rev.confirm":     {"Confirmar e instalar", "Confirm and install"},
	"rev.anyway":      {"Instalar mesmo assim", "Install anyway"},
	"rev.checking":    {"Conferindo…", "Checking…"},
	"rev.blocked":     {"Corrija os itens acima para instalar", "Fix the items above to install"},
	"k.confirm":       {"confirmar", "confirm"},
	"d.bundle":        {"pacote", "bundle"},
	"d.items":         {"itens", "items"},
	"d.size":          {"tamanho", "size"},
	"d.video":         {"vídeo", "video"},
	"d.sound":         {"som", "sound"},
	"d.address":       {"endereço", "address"},
	"d.axis.normal":   {"eixo normal", "normal axis"},
	"d.axis.inverted": {"eixo invertido", "inverted axis"},
	"d.recipe":        {"c copia · enter mostra", "c copies · enter shows"},
}

// T returns the text for key in lang.
func T(lang Lang, key string) string {
	pair, ok := texts[key]
	if !ok {
		return key
	}
	if lang == EN {
		return pair[1]
	}
	return pair[0]
}
