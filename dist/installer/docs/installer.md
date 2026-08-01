# Manual do x86QW moderno e multiplataforma

Este projeto monta uma instalação autocontida em `quake-world`. O mesmo instalador Python executa no macOS, Linux ou Windows, pode preparar binários para qualquer um dos três sistemas e acrescenta recursos modernos sem substituir arquivos pessoais. Ele não instala pacotes nem arquivos globais.

Requisito: Python 3.10 ou mais recente.

O instalador usa apenas a biblioteca padrão do Python.
O bundle público corrente é `0.4.2`; o catálogo registra 56 pacotes e 21
componentes. Esses fatos são validados contra os inventários canônicos.

## Instalação pública

Jogadores não precisam clonar o repositório. Em macOS e Linux, execute:

```sh
/bin/bash -c "$(curl -fsSL https://x86qw.x86.com.br/install.sh)"
```

No Windows PowerShell:

```powershell
irm https://x86qw.x86.com.br/install.ps1 | iex
```

O bootstrap não usa `exit`: a janela atual do PowerShell permanece aberta ao
fim da instalação. O código devolvido pelo instalador Python fica disponível em
`$LASTEXITCODE`; em caso de falha, o bootstrap também imprime um erro com esse
código antes de devolver o controle ao terminal.

O bootstrap tenta os mirrors GitHub e GitLab, valida o SHA-256 do bundle antes
de extrair e ativa o modo remoto estrito. Nesse modo, o catálogo e todos os
pacotes vêm dos endpoints públicos x86QW; a árvore temporária nunca é tratada
como fonte da distribuição. Antes de criar a instalação, o programa pergunta o
destino e apresenta `~/Games/x86qw` somente como sugestão confirmável.

O bundle contém `x86qw.pyz`, os launchers, `installer.json` e uma ponte mínima
para a atualização iniciada pela CLI instalada. Essa ponte encaminha a execução ao
zipapp e permanece apenas no diretório temporário. O zipapp incorpora a CLI e
um catálogo runtime mínimo. PAKs, mods, configurações,
gamecodes, fontes e inventários de manutenção são pacotes
separados; isso evita duplicação e permite atualizar cada conteúdo sem republicar
o instalador.
O layout atual é criado por um bootstrap limpo. Instalações antigas podem ser
mantidas em outro diretório para consulta, mas não são convertidas pelo fluxo
atual.

Ao concluir, a raiz da instalação contém `x86qw.sh` e `x86qw.cmd`. Esses comandos
usam a aplicação única `.x86qw/cli/x86qw.pyz`; as ações públicas são `play`,
`host`, `proxy`, `qtv`, `hub`, `update`, `upgrade`, `verify`, `repair`,
`cleanup`, `uninstall` e `version`. Sem argumento, a CLI
abre o navegador interativo e não inicia instalação alguma até uma ação ser
confirmada. Setas ou `j`/`k` navegam, Enter seleciona, `←` volta, Esc sai e
`/` busca; um fallback numerado mantém o fluxo utilizável sem TTY. O clone e os comandos
`./dist/installer/bin/manager.py` e `./dist/installer/bin/manager.py play` da raiz são o fluxo de desenvolvimento.
Os dois launchers existem permanentemente em `dist/installer/`, entram no bundle
e são copiados byte a byte para o destino. O instalador não gera scripts em runtime.

O contrato é intencionalmente separado:

- `install.sh`/`install.ps1`: instalação inicial, detecção do SO, canal, versão e componentes;
- `x86qw.sh`: gameplay, servidores, atualização, verificação, limpeza e desinstalação.

A CLI instalada rejeita `install`, `components` e `presets`. Para adicionar um
cliente, canal ou seleção arbitrária, execute novamente o bootstrap público.
Novos componentes oficialmente incorporados ao perfil escolhido entram por
`upgrade`, sem transformar a CLI em um instalador genérico.

## Instalar

Os PAKs registrados originais fazem parte da distribuição em:

```text
dist/game-data/id1/pak0.pak
dist/game-data/id1/pak1.pak
```

Esses arquivos são as fontes canônicas do repositório. O build gera o pacote
obrigatório e independente `x86qw-core-id1`; eles não entram no ZIP do instalador.
Em um checkout de desenvolvimento, os arquivos canônicos são usados diretamente.
Na instalação pública, o pacote-base é baixado de um mirror registrado e validado.

Uma instalação nova não exige que `quake-world/` exista. Depois da detecção do
SO e da escolha de canal e versão, o instalador valida os dois arquivos por
SHA-256, cria `quake-world/id1/` e copia somente os PAKs ausentes. Um PAK já
existente é preservado e precisa corresponder à versão registrada; o instalador
nunca o substitui silenciosamente.

No macOS ou Linux, execute:

```sh
./dist/installer/bin/manager.py install
```

No Windows, execute:

```powershell
py -3 .\dist\installer\bin\manager.py install
```

O SO do host é detectado sem perguntas:

```text
[OK] Sistema detectado automaticamente: macOS.
```

O mapeamento é `Darwin → macOS`, `Linux → Linux x86_64` e `Windows → Windows
x64`. Em seguida, escolha `stable` ou `nightly` e uma versão disponível para
aquele SO. Entradas inválidas não encerram o instalador: ele explica o formato
esperado e pergunta novamente.

Para preparar um cliente diferente do host, informe explicitamente
`--platform macos`, `--platform linux` ou `--platform windows`:

```sh
./dist/installer/bin/manager.py install --platform windows
/bin/bash -c "$(curl -fsSL https://x86qw.x86.com.br/install.sh)" -- --platform windows
```

No PowerShell, o equivalente para preparar Linux a partir do Windows é:

```powershell
& ([scriptblock]::Create((irm https://x86qw.x86.com.br/install.ps1))) --platform linux
```

O argumento substitui apenas a detecção do cliente; não tenta executar o
binário estrangeiro no host e não altera a seleção de canal, versão ou
componentes. Um SO desconhecido exige `--platform` em vez de assumir macOS.

Para manter a seleção legível, o instalador mostra inicialmente as 12 nightlies mais recentes. Digite `t` no prompt de versão para exibir o catálogo completo, ou informe diretamente o número ou identificador exato desejado.

Cada execução instala somente o SO detectado ou informado e o canal escolhido.
Execute novamente com `--platform` para adicionar outro SO, ou selecione outro
canal; os clientes anteriores permanecem instalados. Os nomes não colidem:

```text
macOS stable:    quake-world/ezQuake Stable.app
macOS nightly:   quake-world/ezQuake Nightly.app
Linux stable:    quake-world/ezquake-stable-x86_64.AppImage
Linux nightly:   quake-world/ezquake-nightly-x86_64.AppImage
Windows stable:  quake-world/ezquake-stable.exe
Windows nightly: quake-world/ezquake-nightly.exe
```

Os builds oficiais Linux e Windows são somente x86-64/x64. Eles não executam nativamente em Linux ARM ou Windows ARM sem uma camada de compatibilidade. O AppImage requer um Linux desktop compatível, Bash e o suporte AppImage/FUSE oferecido pela distribuição.

O plano de controle fica agrupado no diretório privado e específico do produto
`.x86qw/`. Ele não contém runtimes nem payload de jogo:

```text
quake-world/.x86qw/
├── state.json
├── sessions/
│   ├── active.lock
│   └── <session-id>/
│       └── session.json
├── cli/
│   ├── x86qw.pyz
│   └── receipt
├── clients/
│   └── ezquake/
│       ├── macos/
│       │   ├── stable.receipt
│       │   └── nightly.receipt
│       ├── linux/
│       │   ├── stable.receipt
│       │   └── nightly.receipt
│       └── windows/
│           ├── stable.receipt
│           └── nightly.receipt
└── components/
    ├── nquake-bootstrap/
    │   ├── receipt
    │   └── inventory
    ├── total-destruction-2/
    │   ├── receipt
    │   └── inventory
    └── <demais componentes x86QW>/
        ├── receipt
        └── inventory
```

Os serviços ficam nos próprios contextos operacionais. Somente a variante da
plataforma selecionada é instalada:

```text
quake-world/
├── mvdsv
├── qtv/
│   ├── qtv
│   ├── qtv.cfg
│   └── <recursos web>
├── qwfwd/
│   ├── qwfwd
│   └── qwfwd.cfg
└── docs/licenses/
```

No Windows, os executáveis recebem a extensão `.exe`. `BUILD.json`, fontes,
patches e as variantes de outros sistemas permanecem na distribuição de
manutenção e nunca são copiados para a instalação do jogador.

Cada componente possui inventário e recibo independentes. Assim ele pode ser
instalado, atualizado, verificado ou removido sem assumir propriedade sobre os
demais componentes e arquivos pessoais. A linha atual pressupõe uma instalação
criada por esse bootstrap e não executa migração do layout anterior.

### Fases

A execução continua dividida em duas fases:

1. **ezQuake:** detecta o SO (ou respeita `--platform`), seleciona, baixa,
   valida e instala o artefato correspondente.
2. **componentes x86QW:** após confirmação explícita, escolhe um perfil ou
   componentes individuais e instala somente o conteúdo selecionado.

Ao terminar a primeira fase, o instalador pergunta:

```text
Deseja instalar/atualizar também os componentes x86QW? [s/N]
```

O padrão é `N`. Se a resposta for positiva, há quatro opções:

- `recomendado`: experiência base nQuake, sem matchinfo, QRP nem mods opcionais;
- `essencial`: bootstrap, interface principal e KTX;
- `completo`: os 21 componentes atuais, incluindo QRP, os cinco jogos, MVDSV, QTV e QWFWD;
- `personalizado`: seleção individual, com dependências acrescentadas de forma explícita.

O executável Windows antigo presente nos distfiles não faz parte do overlay.
O TD2 2.22 entra como diretório `td2/`, sem mapas adicionais. Documentação,
fontes QuakeC, exemplos originais e o `pwd.cfg` histórico permanecem preservados
somente no artefato upstream em `dist/mods/td2/2.22/source/`; não entram no runtime. O pacote x86QW
incorpora gamecode, modelos, sons, perfil de cliente, servidor local e modelo de
configuração pessoal. A camada `play-support` mantém apenas a cópia isolada do
gamecode. Ela é materializada por `install`, `update`, `upgrade` ou `repair`;
`play` e `host` somente validam o resultado e nunca criam recibos nem payload
permanente.
Assim uma nova versão do TD2 pode substituir seu conteúdo upstream sem misturar
ou perder os ajustes do x86QW. As fontes do perfil são arquivos normais em
`dist/mods/td2/2.22/x86qw/`, declarados em `maintenance/inventory/components.json`; não ficam
embutidas no código Python.
O builder também recompõe `sound/weapons/saw_down.wav`, omitido pela distribuição
2.22 apesar de ser pré-carregado pelo gamecode, usando o `saw.wav` byte-idêntico
do mesmo artefato e conferindo tamanho e SHA-256 declarados no inventário.
Configurações pessoais nunca entram nos inventários. O `config.cfg` original do
nQuake é usado apenas quando ainda não existe configuração no destino.
Customizações globais devem ficar em `qw/x86qw-user.cfg`, criado uma única vez
e executado ao final do bootstrap. Os aliases fornecidos pelo x86QW são
temporários e, durante uma atualização, cópias antigas desses aliases são
removidas do `config.cfg` salvo sem tocar em aliases pessoais ou em
`cfg_save_unchanged`. Antes dessa migração, cada arquivo alterado é copiado para
`config.aliases-pre-x86qw.cfg` no mesmo diretório.

- `stable`: releases estáveis aprovadas e espelhadas pelo x86QW;
- `nightly`: snapshots de desenvolvimento aprovados e espelhados pelo x86QW.

As duas listas vêm do catálogo versionado em `site/public/api/v1/catalog.json`;
sem um checkout completo, o mesmo arquivo é obtido em
`https://x86qw.x86.com.br/api/v1/catalog.json`.
Cada entrada registra origem, licença revisada, tamanho, SHA-256 e uma lista
ordenada de mirrors. Se uma cópia estiver indisponível ou entregar um hash
incorreto, o instalador tenta a próxima automaticamente.

Cada componente possui versão própria. Coleções sem release oficial continuam
fixadas no commit exato de `nQuake/distfiles`; componentes com upstream
verificável são construídos de forma independente. O KTX atual parte do
`ktx.pk3` curado pelo nQuake, preserva seus LOCs, modos e sons exclusivos, atualiza
os arquivos compartilhados, o `qwprogs.qvm` e o mapa de símbolos pela versão
oficial 1.47 e aplica a compatibilidade e o perfil x86QW por último. A política
de merge registra todas as divergências; uma colisão nova bloqueia o build até
ser revisada.

Os demais mods seguem contratos próprios. Final Arena conserva seu pacote 1.20
integral; Pro-X usa a cópia nQuake 0.8b somente como referência e instala a
distribuição completa 1.1; Team Fortress combina mapas, mídia e LOCs nQuake com
o gamecode oficial 2.9, removendo do pacote montado o gamecode 2.8 e a cópia
inferior byte-idêntica de `detpack.wav`; TD2 não herda conteúdo do nQuake e é
montado diretamente da distribuição 2.22. Em todos os casos, o perfil x86QW e o
arquivo pessoal entram depois do conteúdo do mod.

O catálogo publica uma versão atual por componente e o recibo
individual grava sua versão. Antes do download, o instalador mostra as versões
escolhidas e, quando disponível, o link das notas de release. Em um checkout,
ele materializa o componente diretamente de `dist/distributions/nquake`,
`dist/mods`, `dist/servers` ou `dist/services`; sem essas fontes, recorre aos
pacotes dos mirrors externos. Conteúdo shareware não faz parte dessa seleção.
Em uma instalação nova, também cria
`ezquake/configs/preset.cfg` com o ajuste mínimo de volume esperado pelo primeiro
start; um preset existente nunca é substituído.

## Recursos modernos opcionais

Cada recurso tem uma ação explícita. Nada abaixo é instalado silenciosamente pela ação `install`:

```sh
./dist/installer/bin/manager.py components
./dist/installer/bin/manager.py presets
./dist/installer/bin/manager.py hub
./dist/installer/bin/manager.py play
```

### Componentes x86QW

`components` instala, atualiza ou remove conteúdo de diferentes origens sem tocar nos binários
ezQuake stable/nightly. O catálogo atual oferece:

- base: bootstrap, interface visual, KTX, skins, miras, skyboxes, modelos,
  bandeiras, texturas, mapas selecionados e documentação; os sons de Clan Arena
  pertencem ao próprio pacote KTX, e matchinfo é
  opcional e aparece no perfil completo ou na seleção personalizada;
- addons: QRP em alta resolução, Final Arena, Pro-X, Team Fortress e TD2.

Dependências são resolvidas antes do download. Na remoção, componentes que
dependem do item escolhido também são incluídos e informados ao usuário. Cada
recibo registra o commit de origem e o SHA-256 de cada arquivo instalado.

Não há download em massa de mapas ou LOCs externos. `nquake-maps` contém apenas
o conjunto que já pertence à distribuição de referência. Novos mapas serão
incluídos pontualmente quando passarem a fazer parte do x86QW.

### Presets

`presets` instala quatro configurações independentes em `ezquake/configs`:

```text
x86-qw-modern.cfg       visual moderno equilibrado
x86-qw-competitive.cfg  clareza e baixa distração
x86-qw-classic.cfg      aparência próxima ao Quake original
x86-qw-stream.cfg       legibilidade para transmissão e gravação
```

Nenhum preset é carregado automaticamente e nenhum deles altera binds, sensibilidade ou rede. Dentro do console, use por exemplo:

```text
cfg_load x86-qw-modern
```

O `config.cfg` pessoal e o `preset.cfg` mínimo do nQuake continuam fora do inventário deste componente.

### Jogo local

`x86qw.sh play` abre um servidor local pelo ezQuake sem exigir que o usuário monte a
linha de comando do mod. O menu lista somente os gamecodes cujos componentes e
arquivos de entrada estão presentes:

A versão da CLI aparece no help e no cabeçalho de toda ação. Para consultá-la
sem iniciar outro fluxo, use `./x86qw.sh version` ou `./x86qw.sh --version`.

No Windows, a entrada equivalente é `py -3 .\dist\installer\bin\manager.py play`.

- KTX em `qw/ktx.pk3`;
- Final Arena em `arena/arena.pk3`;
- Pro-X em `prox/qwprogs.dat`;
- Team Fortress em `fortress/misc.pak`;
- Total Destruction 2 em `td2/qwprogs.dat`.

Ao selecionar KTX, um segundo menu oferece os modos curados pela distribuição:

```text
duel  2on2  3on3  4on4  10on10  ffa  ctf  hoony  blitz-2on2  blitz-4on4
2on2on2  3on3on3  4on4on4  xonx  wipeout  clan-arena  tw-tot
midair  dmm4  instagib  lgc  rocket-arena  race  practice
```

O mesmo fluxo pode ser automatizado, sem atravessar os menus:

```sh
./x86qw.sh play ktx --mode duel
./x86qw.sh play ktx --mode 4on4 --fill-bots --bot-skill 6
./x86qw.sh play ktx --mode duel --bots 1 --bot-skill 8 --bot-names x86qw
./x86qw.sh play ktx --mode ffa --bots 4 --bot-skill random
./x86qw.sh play ktx --mode race --map slide1 --race-style match
```

Cada entrada declara seu modo KTX, mapa padrão, sugestões compatíveis e perfil
de entrada quando necessário. O launcher define `k_defmode` antes de iniciar o
mapa; Midair, Race e Practice usam um evento de entrada descartável para aplicar
o comando somente depois que o KTX está ativo. Capture The Flag usa os ENTs
oficiais do KTX para seis mapas clássicos; o launcher seleciona o diretório CTF
antes de carregar o mapa, garantindo a presença das duas bandeiras.

Os Frogbots são acionados com `--bots <quantidade>` ou `--fill-bots`. A CLI
também aceita habilidade 1–20 ou `random` por bot, equipe, arma e vida e valida previamente se o
mapa possui uma das 77 rotas `.bot` incorporadas. CTF e Race rejeitam bots por
serem combinações não suportadas pelo QVM fixado. Race valida uma das 54 rotas
oficiais e expõe estilo, pontuação e pacemaker; CTF expõe hook e runas.

`--bot-names default` mantém os nomes originais do KTX sem definir cvars de
customização. `--bot-names x86qw` sorteia por lançamento uma lista One Piece,
priorizando os dez Chapéus de Palha. `--bot-names personal` usa na ordem
declarada `quake-world/qw/x86qw-frogbot-names.json`, criado pelo bootstrap e
nunca sobrescrito depois de uma edição. O launcher aplica automaticamente o
prefixo `/ ` e a cor clássica compatível com o protocolo; escreva no JSON
somente o nome, sem prefixo ou códigos no valor. Aparências legadas são ignoradas
para que as cores continuem sob controle do KTX. O contrato completo está em
[`docs/FROGBOTS.md`](../../../docs/FROGBOTS.md).

No menu, `x86QW aleatório` é a seleção inicial e o perfil sem customização
aparece como `KTX Default`. A CLI conserva `default` como padrão por
compatibilidade. Modos de tamanho fixo oferecem somente as vagas restantes —
Duel aceita um bot com o jogador humano — enquanto FFA e Practice mantêm
preenchimento e quantidade personalizada. Vários bots entram em frames
separados.

Todos os itens do menu exibem o número equivalente; `→`/Enter avança, `←`
volta somente para a etapa imediatamente anterior e Esc encerra o navegador.
Em todos os cinco jogos, `F12` é ativado antes do mapa e reaplicado depois da
configuração pessoal para fechar diretamente o QuakeWorld.

O terminal confirma o preset selecionado antes e depois da abertura. Dentro do
console do ezQuake, `ktx_mode` repete exatamente o preset iniciado pelo launcher;
`cmd rules` mostra o estado de regras publicado pelo próprio KTX. Essa distinção é
importante nos presets especiais, que partem de uma base `1on1` ou `ffa` e ativam
Midair, Race ou Practice durante a entrada no mapa.

Antes de abrir o jogo, o launcher valida o recibo do componente e descobre os
mapas disponíveis em arquivos BSP soltos, PK3s e PAKs do gamedir e de `id1`.
Ele oferece sugestões, aceita um nome instalado ou lista o acervo completo. Se
stable e nightly coexistirem, pergunta qual cliente usar.

No macOS com notch, o modo de compatibilidade de área segura pode reduzir a
janela do ezQuake sem reduzir o framebuffer SDL, recortando o topo de telas como
**Options**. Durante a instalação ou o primeiro reparo, o x86QW registra
`NSPrefersDisplaySafeAreaCompatibilityMode=false` no `Info.plist` e assina
novamente o bundle com `codesign`. A fase de instalação ou reparo identifica o
monitor interno pela resolução física e deriva sua área 16:10 segura. Ela grava
fullscreen explícito (`vid_fullscreen 1` e `vid_usedesktopres 0`) com a
resolução 16:10 segura detectada para o painel. Em
um MacBook com painel físico 3024×1964, por exemplo, o jogo abre diretamente em
3024×1890; o modo desktop automático não pode ignorar essas dimensões e ocupar a
área recortada pelo notch. A frequência permanece automática. Alterações
pessoais de vídeo desativam o gerenciamento automático e são preservadas. A
migração da CLI 0.1.7 remove o ajuste temporário de janela sem bordas; instalações
que receberam fullscreen desktop automático são migradas para o modo explícito
seguro na próxima instalação, atualização ou reparo explícito.

A execução sempre configura os dois lados do servidor local, nesta ordem:

```text
-game <mod> +sv_gamedir <mod> +map <mapa> +wait +exec perfil-x86qw.cfg
```

Como o servidor integrado do ezQuake salva variáveis no `config.cfg` pessoal,
o launcher também isola os valores de tick e salto: KTX recebe sua configuração
própria e os gamecodes QuakeC recebem novamente a linha de base nQuake. A
entrada local é sempre iniciada com `spectator 0`; isso evita o caminho de
espectador incompatível entre o QVM KTX 1.47 e o servidor integrado do ezQuake
3.6.9 sem alterar a opção pessoal gravada fora da execução.

`-nohome` isola a execução de qualquer `~/.ezquake` externo. `-game` seleciona
o diretório de arquivos e o gamecode antes da inicialização;
`+sv_gamedir` publica o valor correto de `*gamedir` aos clientes. Isso impede
que o servidor local permaneça em `qw` e carregue KTX ao tentar iniciar outro
mod. O mapa é iniciado antes do perfil; após um frame, o `exec` aplica os binds
x86QW por último, impedindo que a configuração base do nQuake os restaure. O
comando não baixa conteúdo nem transforma a máquina em servidor dedicado público.

Final Arena e Pro-X são componentes completamente independentes no x86QW. O
primeiro continua vindo do snapshot nQuake; o segundo substitui a cópia histórica
armazenada em `addon-clanarena` pela release pública mais recente:

- Final Arena 1.20 usa uma fila individual: o vencedor permanece na arena e o
  perdedor volta ao fim da fila;
- Pro-X QW 1.1 organiza partidas por rounds e equipes, com ready, break,
  entrada, observação e votação de mapas próprios.

Cada um possui pacote, versão, recibo, inventário e perfil próprios. O x86QW
preserva o ZIP original do Pro-X 1.1 em `dist/`, mas não instala o antigo
`configs/config.cfg` do nQuake para que HUD, vídeo e binds históricos não sejam
executados. Se uma instalação anterior já tiver esse arquivo como configuração
pessoal, a atualização cria `config.pre-x86qw.cfg` e migra o perfil ativo para
a base moderna do jogador.

Todos os cinco jogos do menu de `x86qw.sh play` carregam gameplay próprio. A base nQuake
continua responsável por movimento, mouse, rede, vídeo e comunicação geral. O
perfil do mod corrige conflitos e expõe suas mecânicas; o arquivo pessoal é
executado por último:

```text
configuração nQuake -> x86qw-<mod>.cfg -> x86qw-<mod>-user.cfg
```

O KTX imprime ao entrar o plano contextual de `F5`, `F6` e `F11`; quando há
Frogbots, também mostra `INS`, `DEL`, `HOME` e `END`. `F10` reapresenta a ajuda
completa sob demanda. Os
perfis não são cópias entre si:

- **KTX:** `1-8` selecionam armas com fallback; `Q`, `E` e `Mouse2` dão acesso
  rápido a GL, RL e LG. As comunicações do nQuake continuam disponíveis, mas
  `quad morto` e `inimigo com powerup` foram movidos de `1` e `5` para `Z` e
  `X`. `F1-F4` preservam quad, pent e timers; `F5`, `F6` e `F11` assumem as
  ações declaradas pelo modo, enquanto `F7` e `F8` mantêm join e observe.
- **Final Arena:** `F1` entra, `F2` mostra a fila, `F3` estatísticas, `F4`
  pausa, `F5` próximo mapa, `F6` status, `F7` mochilas e `F8` airgib. São os
  impulses publicados pelo gamecode do Final Arena 1.20.
- **Pro-X:** preserva os impulses duplos de armas do perfil histórico; `F1-F9`
  cobrem voto, administração, ready, break, entrada, observação, equipes e
  menu. `M`, `I` e `H` acessam menu, identificação e som de acerto.
  O `qw_server.cfg` solicitado pelo gamecode reaplica esse perfil localmente
  depois que o ezQuake bloqueia os antigos binds enviados pelo servidor.
- **Team Fortress:** mantém granadas, detpacks, recarga, habilidade de classe,
  bandeira, descarte e pedido de médico do addon nQuake. `F1-F9` dão acesso a
  inventário, classes, troca de classe, ajuda do mapa e ações frequentes.
- **TD2:** `1` usa magia, `2-8` armas normais, `9` arma especial e `0` voto
  SIM. `Mouse4/5` acessam magia/especial, `Z/X` descartam runa/especial e `F1`
  propõe o próximo mapa.

Os perfis de servidor selecionam gamecodes exclusivos, ativam antilag e isolam
os gamedirs. Personalizações sobrevivem às atualizações e ficam em:

```text
quake-world/qw/x86qw-ktx-user.cfg
quake-world/arena/x86qw-arena-user.cfg
quake-world/prox/x86qw-prox-user.cfg
quake-world/fortress/x86qw-fortress-user.cfg
quake-world/td2/x86qw-td2-user.cfg
```

No TD2, o instalador acrescenta `+exec x86qw-td2.cfg` depois de `+map` e de um
`wait`. O arquivo gerenciado preserva os recursos modernos já configurados no
ezQuake e substitui apenas os controles e a apresentação específicos do mod:

```text
1           magia
2 a 8       armas normais
9           arma especial
0           votar SIM
Z           largar runa
X           largar arma especial
roda        trocar arma para frente/trás
Mouse 4     magia
Mouse 5     arma especial
F1          propor o próximo mapa
F2          largar runa
F3          largar arma especial
F10         mostrar resumo dos controles
```

O `td2/server.cfg` da camada x86QW seleciona o gamecode exclusivo, ativa antilag
e inicia o perfil completo local: armas especiais, runas, Turbo, votações,
zumbi vingador, poder da Luz e modelos alternativos de pipebomb. O pacote
original continua preservado, inclusive seus arquivos `*.example.cfg`.

Personalizações do jogador devem ser colocadas em:

```text
quake-world/td2/x86qw-td2-user.cfg
```

Esse arquivo é criado somente quando não existe, é executado depois do preset
x86QW e nunca entra em `.x86qw/components/play-support/inventory`. Atualizar o TD2 ou o
ezQuake reaplica a camada do projeto e preserva esse arquivo pessoal. Remover os
componentes também o preserva; `uninstall --purge` continua sendo a ação explícita que
remove toda a instalação.

### Navegador de servidores

`hub` consulta a API pública do [QuakeWorld Hub](https://hub.quakeworld.nu/), mostra servidores ativos com humanos, bots, modo e mapa, e abre um cliente já instalado para:

- jogar: informe o número do servidor;
- observar diretamente: informe `o` seguido do número, como `o3`;
- assistir via QTV: informe `q` seguido do número, como `q3`.

Se stable e nightly estiverem instalados para o sistema atual, o instalador
pergunta qual ezQuake abrir. A execução recebe `-basedir quake-world`, portanto
os dois compartilham os mesmos PAKs e componentes. Não há registro global de
protocolo nem alteração no navegador ou no sistema operacional.

### Conteúdo visual e servidor próprio

O nQuake já fornece seu pacote visual, QRP map textures, skins, HUD e addons. O instalador não baixa automaticamente itens arbitrários de [gfx.quakeworld.nu](https://gfx.quakeworld.nu/): esses arquivos têm autores, licenças, estilos e destinos diferentes e frequentemente colidem entre si. Uma galeria curada exigirá uma lista explícita de itens compatíveis e licenciados, em vez de instalar o site inteiro.

O navegador do Hub moderniza o acesso a servidores públicos. O perfil completo
instala MVDSV, QTV e QWFWD como componentes separados, com fonte, runtime,
SHA-256, recibo e inventário. O x86QW preserva os binários oficiais Linux/Windows
e fornece builds macOS arm64 reproduzidos das fontes fixadas.

Os serviços ficam em loopback por padrão e sempre executam em primeiro plano:

```sh
./x86qw.sh host --mode 4on4 --map dm3
./x86qw.sh host --mode duel --map dm6 --bind 0.0.0.0 --with-qtv
./x86qw.sh proxy --bind 0.0.0.0
./x86qw.sh qtv --upstream 127.0.0.1:28501
```

`host` materializa temporariamente o conteúdo KTX verificado que o MVDSV
precisa ler fora do PK3 e o remove no encerramento. `--with-qtv` e
`--with-proxy` iniciam os serviços opcionais na mesma sessão; `Ctrl+C` encerra
o conjunto sem deixar filhos. Senhas ficam apenas em configurações efêmeras de
permissão privada. Bind externo, firewall, NAT, DNS e TLS continuam sendo
decisões explícitas do administrador.

O cliente macOS é universal (`arm64` + `x86_64`). Os serviços MVDSV, QTV e
QWFWD no macOS são `arm64`; não existe anúncio de serviço para macOS Intel.
Linux usa `amd64`/`x86_64` e Windows usa `x64` para cliente e serviços.

As opções `--prompt-password`, `--prompt-spectator-password`,
`--prompt-rcon-password` e `--prompt-qtv-password` leem sem eco. As variantes
`--password-file`, `--spectator-password-file`, `--rcon-password-file` e
`--qtv-password-file` exigem arquivo regular, sem symlink e, no Unix, privado.
As opções legadas que recebem segredo diretamente continuam aceitas por
compatibilidade, mas podem permanecer no histórico do shell e geram alerta.

Antes de iniciar qualquer filho, a CLI valida componentes, executáveis,
configurações, endpoints e todas as portas. A ordem composta é MVDSV, readiness
e configuração pós-map por RCON, QTV com HTTP/upstream e, por último, QWFWD.
Falha parcial encerra o que já iniciou. O journal privado em
`.x86qw/sessions/` e o lock atômico permitem uma única stack por instalação.
Uma segunda CLI nunca recupera um controlador vivo. Após crash confirmado, PID,
token de criação e executável identificam os filhos órfãos; PID reutilizado ou
identidade inconclusiva são preservados. Temporários não sensíveis modificados
continuam preservados, mas configurações efêmeras com segredo são sempre
removidas por unlink, sem imprimir ou guardar seu conteúdo.

QTV ligado fora de loopback sempre gera alerta de exposição HTTP: a senha do
upstream protege a relação QTV/MVDSV, não autentica visitantes da interface
HTTP. Em `host --with-qtv`, o upstream acompanha o bind alcançável do MVDSV,
incluindo IPv4 e IPv6 (`0.0.0.0` vira loopback IPv4 e `::` vira `[::1]`).

## Preparar outro sistema

O instalador pode ser executado diretamente no sistema de destino ou preparar outro SO. Para transportar uma instalação, copie a pasta `quake-world` inteira, preservando `id1`, `qw`, `ezquake` e os addons.

A cópia deve incluir a pasta oculta `.x86qw`. Não use apenas o glob `quake-world/*`, pois ele ignora arquivos ocultos. No terminal, por exemplo:

```sh
mkdir -p destino/quake-world
cp -a quake-world/. destino/quake-world/
```

No Windows, execute:

```text
ezquake-stable.exe
```

ou:

```text
ezquake-nightly.exe
```

No Linux, execute:

```sh
./ezquake-stable-x86_64.AppImage
```

ou:

```sh
./ezquake-nightly-x86_64.AppImage
```

O instalador já aplica a permissão executável no AppImage. Se o método usado para copiar a pasta remover permissões Unix, execute `chmod +x *.AppImage` no Linux.

## Verificar

```sh
./dist/installer/bin/manager.py verify
```

No Windows, substitua `./dist/installer/bin/manager.py` por `py -3 .\dist\installer\bin\manager.py` nos exemplos.

## Saída e diagnóstico

A saída padrão prioriza decisões, andamento e resultado. Downloads exibem uma barra de progresso quando o instalador roda em um terminal interativo. Em logs, redirecionamentos e automações, a barra é omitida para não poluir a saída.

Para investigar uma falha ou auditar exatamente o que será usado, ative o modo detalhado:

```sh
./dist/installer/bin/manager.py --verbose install
```

Esse modo acrescenta host e versão do Python, URLs consultadas, comandos externos, caminho do cache e checksums. Ele não instala ferramentas nem bibliotecas extras. Para desativar cores explicitamente, use `--no-color`; a variável padrão `NO_COLOR` também é respeitada.

O instalador consulta somente o catálogo público x86QW para ezQuake. Para desenvolvimento, `X86_QW_CATALOG_URL` permite apontar para
outro endpoint HTTPS compatível; a variável não é gravada nos recibos.

Use `./dist/installer/bin/manager.py --help` para consultar ações e opções sem iniciar nenhuma operação.

Cada combinação SO/canal recebe um recibo próprio. A verificação funciona offline e cobre:

- hashes dos PAKs registrados;
- recibos e inventário da instalação;
- integridade dos arquivos gerenciados e dos PK3;
- estrutura, versão, assinatura e arquiteturas `arm64` e `x86_64` dos apps macOS; a assinatura é validada com `codesign` quando a verificação roda no macOS;
- formato ELF x86-64 e permissão dos AppImages Linux;
- formato PE32+ x86-64 dos executáveis Windows;
- identificador oficial selecionado, origem imutável e SHA-256 de cada binário; no macOS, também a versão gravada no bundle.
- hashes e formatos dos arquivos pertencentes a cada componente nQuake;
- hashes dos presets gerenciados.

## Desinstalar e limpar o cache

```sh
./dist/installer/bin/manager.py uninstall
./dist/installer/bin/manager.py uninstall --purge
./dist/installer/bin/manager.py cleanup
```

`uninstall` remove a CLI permanente, todos os binários macOS, Linux e Windows comprovadamente
gerenciados, componentes x86QW, presets próprios do instalador, seus recibos e
os arquivos cujo hash ainda corresponde ao inventário. Arquivos modificados são
preservados. Os PAKs, `config.cfg`, demos, screenshots, logs, presets pessoais e
outros arquivos pessoais permanecem em `quake-world`.

O recibo é a autoridade para a remoção: `uninstall` também conclui quando um app ou executável registrado já está ausente ou incompleto. Use `verify` quando quiser exigir e diagnosticar a integridade dos runtimes instalados.

`uninstall --purge` é a remoção total: apaga o próprio diretório da instalação,
incluindo `id1`, PAKs, arquivos pessoais, configurações, CLI, recibos e conteúdo
desconhecido. Também remove os caches nativos `x86qw` e o legado `x86-qw`, desde
que seus marcadores comprovem que pertencem ao instalador. Um alvo existente sem
identidade x86QW é recusado; se o diretório já não existir, os caches ainda são removidos.

`cleanup` remove o cache criado pelo próprio instalador, incluindo downloads
ezQuake e pacotes dos componentes x86QW. Também elimina dados regeneráveis
classificados dentro da instalação: cache do navegador de servidores, temporários
e demos TD2 vazias. A remoção do cache externo só
ocorre se o marcador de propriedade criado pelo instalador estiver presente. O
diretório é resolvido conforme o host:

- macOS: `$(getconf DARWIN_USER_CACHE_DIR)/x86qw`;
- Linux: `$XDG_CACHE_HOME/x86qw` ou `~/.cache/x86qw`;
- Windows: `%LOCALAPPDATA%\x86qw`.

Para consultar o caminho neste Mac:

```sh
printf '%s/x86qw\n' "$(getconf DARWIN_USER_CACHE_DIR | sed 's#/$##')"
```

Downloads recebidos de servidores Team Fortress e dados pessoais são classes
separadas e permanecem preservados por padrão:

```sh
./dist/installer/bin/manager.py cleanup --downloads
./dist/installer/bin/manager.py cleanup --personal-data
./dist/installer/bin/manager.py cleanup --downloads --personal-data
```

`--downloads` remove apenas arquivos não gerenciados sob `fortress/progs` e
`fortress/sound`. `--personal-data` remove histórico, logs e demos locais
válidas; por isso precisa ser solicitado explicitamente.

## Atualizar ou acrescentar conteúdo

No diretório instalado, execute:

```sh
./x86qw.sh update
./x86qw.sh upgrade
```

Os dois comandos atualizam primeiro a própria CLI por um bundle x86QW validado
e consultam o catálogo atual. `update` é conservador: cada ezQuake
stable/nightly já registrado avança no mesmo SO e canal, e somente componentes
já instalados são atualizados. Itens ausentes são detectados e informados, mas
não são adicionados.

`upgrade` inclui todo o comportamento de `update` e converge os componentes para
o perfil registrado na instalação. Assim, uma funcionalidade nova adicionada
ao perfil `recommended` passa a ser instalada nos clientes desse perfil; o
perfil `complete` recebe todos os componentes atuais. Em `custom`, somente as
escolhas explícitas e suas dependências obrigatórias evoluem. Componentes fora
do perfil são informados e preservados, nunca removidos implicitamente.

O estado fica em `.x86qw/state.json` e registra o perfil, a seleção explícita,
os componentes conhecidos, as capacidades e o fingerprint correspondente. O
bootstrap cria esse estado integralmente; não infere seleção a partir de uma
árvore antiga.

Para diagnosticar e recompor apenas conteúdo gerenciado ausente ou divergente,
use no checkout de desenvolvimento:

```sh
./dist/installer/bin/manager.py repair --dry-run
./dist/installer/bin/manager.py repair
```

`repair` também valida os recibos stable e nightly do ezQuake, runtime, formato,
hash, permissão AppImage e preparação do bundle macOS. Ele repara localmente
permissões, preparação e estado reconstruível; versão e canal registrados são
preservados sem downgrade. Recibo sem inventário, inventário sem recibo, runtime
sem metadata e estados ambíguos são diagnosticados sem exclusão ou inferência
destrutiva. Quando o plano exige payload, a CLI instalada orienta a reexecução
do bootstrap para obtê-lo pelo fluxo público validado. Arquivos pessoais e
arquivos gerenciados modificados são preservados.

Antes de alterar qualquer arquivo, os dois comandos consultam o catálogo e
mostram somente as mudanças reais no formato do Homebrew: manifesto baixado,
pacotes tabulados, versão instalada, versão disponível e tamanho do download.
Itens já atualizados não poluem o plano. Se a tabela estiver vazia, o comando
termina imediatamente, sem confirmação, segunda passagem de atualização ou
verificação integral. Quando há mudanças, o prompt `[y/n]` aceita `y`/`yes` e
também `s`/`sim`. Para automações, `--yes` aceita o plano sem abrir o prompt:

```sh
./x86qw.sh update --yes
./x86qw.sh upgrade --yes
```

Para apresentar o mesmo plano e encerrar sem pedir confirmação nem alterar
arquivos, use:

```sh
./x86qw.sh update --dry-run
./x86qw.sh upgrade --dry-run
```

`n`, `no`, `não` ou uma resposta vazia cancelam a operação; respostas inválidas
são solicitadas novamente. Em um ambiente sem terminal interativo, `update` e
`upgrade` falham com uma orientação explícita para usar `--yes`; não existe
confirmação implícita.

Uma versão local de ezQuake mais nova que o catálogo nunca sofre downgrade.
PAKs e arquivos pessoais são preservados e a instalação passa por verificação
integral ao final.

Para acrescentar outro SO, canal, preset ou componente fora do perfil, execute novamente
`install.sh`/`install.ps1`. Os comandos `install`, `components` e `presets`
continuam existindo apenas nas ferramentas do checkout para desenvolvimento e
montagem da distribuição; não fazem parte da CLI entregue aos jogadores.

Não há uso silencioso do alias `latest`: uma nightly é sempre baixada pelo nome exato, com data, hora e commit.

## Primeira execução no macOS

Feche qualquer ezQuake aberto antes de instalar ou atualizar. O aplicativo oficial usa uma autorização sandbox compartilhada entre as instalações stable e nightly. O instalador remove uma seleção antiga para impedir que uma cópia nova continue lendo outro diretório de jogo.

Abra `quake-world/ezQuake Stable.app` ou `quake-world/ezQuake Nightly.app`. Na janela que pede o diretório contendo `id1/pak0.pak`, escolha exatamente a própria pasta `quake-world` mostrada no resumo do instalador. Essa seleção é obrigatória para que o ezQuake encontre `qw/autoexec.cfg` e carregue a configuração inicial nQuake.

O menu principal pode manter a aparência clássica do Quake. Para verificar o estado real, execute `./dist/installer/bin/manager.py verify`: o resultado informa se as configurações nQuake estão aguardando a primeira abertura ou se já foram carregadas.

Os builds oficiais atuais usam assinatura ad-hoc e podem não estar notarizados. Se o Gatekeeper bloquear a abertura, use **Ajustes do Sistema > Privacidade e Segurança > Abrir Mesmo Assim**. O instalador não remove a quarentena nem contorna as proteções do macOS.

## O que permanece no projeto

O repositório guarda os dois PAKs registrados em `dist/game-data/id1` e as fontes
disponíveis de ezQuake stable/nightly, KTX, Team Fortress e TD2 junto aos seus
contextos em `dist/`. Em `quake-world`, PAKs e configurações pessoais permanecem
quando o runtime for removido. Apps, executáveis, addons, texturas e cache
continuam reconstruíveis.

## Testar o instalador

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest maintenance.tests.test_installer maintenance.tests.test_modern_components -v
```

Os testes usam somente diretórios temporários e não alteram `quake-world` nem o cache real.
