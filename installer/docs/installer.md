# QuakeWorld moderno e multiplataforma

Este projeto monta uma instalação autocontida em `quake-world`. O mesmo instalador Python executa no macOS, Linux ou Windows, pode preparar binários para qualquer um dos três sistemas e acrescenta recursos modernos sem substituir arquivos pessoais. Ele não instala pacotes nem arquivos globais.

Requisito: Python 3.10 ou mais recente.

O instalador usa apenas a biblioteca padrão do Python.

## Instalar

Os PAKs registrados originais fazem parte da distribuição em:

```text
dist/id1/pak0.pak
dist/id1/pak1.pak
```

Uma instalação nova não exige que `quake-world/` exista. Depois da escolha de
SO, canal e versão, o instalador valida os dois arquivos permanentes por
SHA-256, cria `quake-world/id1/` e copia somente os PAKs ausentes. Um PAK já
existente é preservado e precisa corresponder à versão registrada; o instalador
nunca o substitui silenciosamente.

No macOS ou Linux, execute:

```sh
./install-qw.py install
```

No Windows, execute:

```powershell
py -3 .\install-qw.py install
```

A primeira pergunta escolhe o SO de destino:

```text
1) macOS         - universal arm64 + x86_64 (padrão)
2) Linux x86_64  - AppImage
3) Windows x64   - executável .exe
```

Pressionar Enter seleciona macOS. Em seguida, escolha `stable` ou `nightly` e uma versão disponível para aquele SO. Entradas inválidas não encerram o instalador: ele explica o formato esperado e pergunta novamente.

Para manter a seleção legível, o instalador mostra inicialmente as 12 nightlies mais recentes. Digite `t` no prompt de versão para exibir o catálogo completo, ou informe diretamente o número ou identificador exato desejado.

Cada execução instala somente o SO e o canal escolhidos. Execute novamente para adicionar outro SO ou canal; os anteriores permanecem instalados. Os nomes não colidem:

```text
macOS stable:    quake-world/ezQuake Stable.app
macOS nightly:   quake-world/ezQuake Nightly.app
Linux stable:    quake-world/ezquake-stable-x86_64.AppImage
Linux nightly:   quake-world/ezquake-nightly-x86_64.AppImage
Windows stable:  quake-world/ezquake-stable.exe
Windows nightly: quake-world/ezquake-nightly.exe
```

Os builds oficiais Linux e Windows são somente x86-64/x64. Eles não executam nativamente em Linux ARM ou Windows ARM sem uma camada de compatibilidade. O AppImage requer um Linux desktop compatível, Bash e o suporte AppImage/FUSE oferecido pela distribuição.

O estado do instalador fica em um único diretório neutro, sem arquivos soltos na raiz nem subpastas por produto:

```text
quake-world/.install/
├── ezquake-macos-stable.receipt
├── ezquake-macos-nightly.receipt
├── ezquake-linux-stable.receipt
├── ezquake-linux-nightly.receipt
├── ezquake-windows-stable.receipt
├── ezquake-windows-nightly.receipt
├── presets.{receipt,inventory}
├── nquake-bootstrap.{receipt,inventory}
├── nquake-visual-core.{receipt,inventory}
├── nquake-ktx.{receipt,inventory}
└── <demais componentes x86QW>.{receipt,inventory}
```

Cada componente possui inventário e recibo independentes. Assim ele pode ser
instalado, atualizado, verificado ou removido sem assumir propriedade sobre os
demais componentes e arquivos pessoais.

### Fases

A execução continua dividida em duas fases:

1. **ezQuake:** seleciona, baixa, valida e instala o artefato do SO escolhido.
2. **componentes x86QW:** após confirmação explícita, escolhe um perfil ou
   componentes individuais e instala somente o conteúdo selecionado.

Ao terminar a primeira fase, o instalador pergunta:

```text
Deseja instalar/atualizar também os componentes x86QW? [s/N]
```

O padrão é `N`. Se a resposta for positiva, há quatro opções:

- `recomendado`: toda a experiência base nQuake, sem os três addons maiores;
- `essencial`: bootstrap, interface principal e KTX;
- `completo`: os 18 componentes, incluindo QRP, Clan Arena, Team Fortress e Total Destruction 2;
- `personalizado`: seleção individual, com dependências acrescentadas de forma explícita.

O executável Windows antigo presente nos distfiles não faz parte do overlay.
O TD2 2.22 entra como diretório `td2/`, sem mapas adicionais. Seus arquivos de
servidor são exemplos inertes (`*.example.cfg`), e o `pwd.cfg` histórico com
senha padrão não entra na instalação. Depois do pacote original, o instalador
aplica separadamente a camada x86QW de execução local. Assim uma nova versão do
TD2 pode substituir seu conteúdo upstream sem misturar ou perder os ajustes do
x86QW. As fontes dessa camada são arquivos normais do repositório em
`dist/mods/td2/2.22/x86qw/`, declarados em `maintenance/inventory/components.json`; não ficam
embutidas no código Python.
Configurações pessoais nunca entram nos inventários. O `config.cfg` original do
nQuake é usado apenas quando ainda não existe configuração no destino.

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
verificável podem receber overlays independentes. O KTX atual combina os
recursos nQuake com o `qwprogs.qvm` oficial 1.47, substituindo o `1.46-dev`
embarcado. O catálogo publica uma versão atual por componente e o recibo
individual grava sua versão. Antes do download, o instalador mostra as versões
escolhidas e, quando disponível, o link das notas de release. Em um checkout,
ele materializa o componente diretamente de `dist/nquake` e `dist/mods`; sem
essas fontes, recorre aos pacotes dos mirrors externos. Servidores e shareware
ficam de fora. Em uma instalação nova, também cria
`ezquake/configs/preset.cfg` com o ajuste mínimo de volume esperado pelo primeiro
start; um preset existente nunca é substituído.

## Recursos modernos opcionais

Cada recurso tem uma ação explícita. Nada abaixo é instalado silenciosamente pela ação `install`:

```sh
./install-qw.py components
./install-qw.py presets
./install-qw.py play
./install-qw.py hub
```

### Componentes x86QW

`components` instala, atualiza ou remove conteúdo de diferentes origens sem tocar nos binários
ezQuake stable/nightly. O catálogo atual oferece:

- base: bootstrap, interface visual, KTX, skins, miras, skyboxes, modelos,
  bandeiras, sons, texturas, mapas selecionados, matchinfo e documentação;
- addons: QRP em alta resolução, Clan Arena/Pro-X, Team Fortress e TD2.

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

`play` abre um servidor local pelo ezQuake sem exigir que o usuário monte a
linha de comando do mod. O menu lista somente os gamecodes cujos componentes e
arquivos de entrada estão presentes:

- KTX em `qw/ktx.pk3`;
- Clan Arena em `arena/arena.pk3`;
- Pro-X em `prox/prox.pk3`;
- Team Fortress em `fortress/misc.pak`;
- Total Destruction 2 em `td2/qwprogs.dat`.

Antes de abrir o jogo, o instalador valida o recibo do componente e descobre os
mapas disponíveis em arquivos BSP soltos, PK3s e PAKs do gamedir e de `id1`.
Ele oferece sugestões, aceita um nome instalado ou lista o acervo completo. Se
stable e nightly coexistirem, pergunta qual cliente usar.

A execução sempre configura os dois lados do servidor local, nesta ordem:

```text
-game <mod> +gamedir <mod> +sv_gamedir <mod> +map <mapa>
```

`-game` prepara o caminho do cliente, `+gamedir` seleciona o gamecode e
`+sv_gamedir` publica o valor correto de `*gamedir` aos clientes. Isso impede
que o servidor local permaneça em `qw` e carregue KTX ao tentar iniciar outro
mod. O comando não baixa conteúdo nem transforma a máquina em servidor dedicado
público.

No TD2, o instalador acrescenta `+exec x86qw-td2.cfg` antes de `+map`. O arquivo
gerenciado `td2/x86qw-td2.cfg` preserva os recursos modernos já configurados no
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
x86QW e nunca entra em `.install/play-support.inventory`. Atualizar o TD2 ou o
ezQuake reaplica a camada do projeto e preserva esse arquivo pessoal. Remover os
componentes também o preserva; `purge` continua sendo a ação explícita que
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

O navegador do Hub moderniza o acesso a servidores públicos. Hospedar um servidor MVDSV/KTX não foi misturado à instalação do cliente: a release atual do KTX publica SHA-256, mas os binários atuais do MVDSV não publicam checksum no GitHub, e não há binário macOS oficial. Isso evita executar silenciosamente um binário de servidor sem verificação ou exigir toolchains extras no Mac.

## Preparar outro sistema

O instalador pode ser executado diretamente no sistema de destino ou preparar outro SO. Para transportar uma instalação, copie a pasta `quake-world` inteira, preservando `id1`, `qw`, `ezquake` e os addons.

A cópia deve incluir a pasta oculta `.install`. Não use apenas o glob `quake-world/*`, pois ele ignora arquivos ocultos. No terminal, por exemplo:

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
./install-qw.py verify
```

No Windows, substitua `./install-qw.py` por `py -3 .\install-qw.py` nos exemplos.

## Saída e diagnóstico

A saída padrão prioriza decisões, andamento e resultado. Downloads exibem uma barra de progresso quando o instalador roda em um terminal interativo. Em logs, redirecionamentos e automações, a barra é omitida para não poluir a saída.

Para investigar uma falha ou auditar exatamente o que será usado, ative o modo detalhado:

```sh
./install-qw.py --verbose install
```

Esse modo acrescenta host e versão do Python, URLs consultadas, comandos externos, caminho do cache e checksums. Ele não instala ferramentas nem bibliotecas extras. Para desativar cores explicitamente, use `--no-color`; a variável padrão `NO_COLOR` também é respeitada.

O instalador consulta somente o catálogo público x86QW para ezQuake. Para desenvolvimento, `X86_QW_CATALOG_URL` permite apontar para
outro endpoint HTTPS compatível; a variável não é gravada nos recibos.

Use `./install-qw.py --help` para consultar ações e opções sem iniciar nenhuma operação.

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
./install-qw.py uninstall
./install-qw.py purge
./install-qw.py cleanup
```

`uninstall` remove todos os binários macOS, Linux e Windows comprovadamente
gerenciados, componentes x86QW, presets próprios do instalador, seus recibos e
os arquivos cujo hash ainda corresponde ao inventário. Arquivos modificados são
preservados. Os PAKs, `config.cfg`, demos, screenshots, logs, presets pessoais e
outros arquivos pessoais permanecem em `quake-world`.

O recibo é a autoridade para a remoção: `uninstall` também conclui quando um app ou executável registrado já está ausente ou incompleto. Use `verify` quando quiser exigir e diagnosticar a integridade dos runtimes instalados.

`purge` é a remoção total: apaga tudo dentro de `quake-world`, incluindo arquivos pessoais e metadados desconhecidos, preservando somente a árvore `id1`. Também remove o cache nativo criado pelo instalador. A ação recusa alvos sem um diretório `id1` real.

`cleanup` remove somente o cache criado pelo próprio instalador, incluindo
downloads ezQuake e pacotes dos componentes x86QW. A remoção só
ocorre se o marcador de propriedade criado pelo instalador estiver presente. O
diretório é resolvido conforme o host:

- macOS: `$(getconf DARWIN_USER_CACHE_DIR)/x86-qw`;
- Linux: `$XDG_CACHE_HOME/x86-qw` ou `~/.cache/x86-qw`;
- Windows: `%LOCALAPPDATA%\x86-qw`.

Para consultar o caminho neste Mac:

```sh
printf '%s/x86-qw\n' "$(getconf DARWIN_USER_CACHE_DIR | sed 's#/$##')"
```

## Atualizar ou trocar de canal

Execute a ação correspondente novamente: `install` para ezQuake e uma seleção
x86QW, `components` para conteúdo adicional ou `presets` para configurações.
Somente o componente escolhido é substituído; os demais binários e arquivos
pessoais permanecem preservados.

Não há uso silencioso do alias `latest`: uma nightly é sempre baixada pelo nome exato, com data, hora e commit.

## Primeira execução no macOS

Feche qualquer ezQuake aberto antes de instalar ou atualizar. O aplicativo oficial usa uma autorização sandbox compartilhada entre as instalações stable e nightly. O instalador remove uma seleção antiga para impedir que uma cópia nova continue lendo outro diretório de jogo.

Abra `quake-world/ezQuake Stable.app` ou `quake-world/ezQuake Nightly.app`. Na janela que pede o diretório contendo `id1/pak0.pak`, escolha exatamente a própria pasta `quake-world` mostrada no resumo do instalador. Essa seleção é obrigatória para que o ezQuake encontre `qw/autoexec.cfg` e carregue a configuração inicial nQuake.

O menu principal pode manter a aparência clássica do Quake. Para verificar o estado real, execute `./install-qw.py verify`: o resultado informa se as configurações nQuake estão aguardando a primeira abertura ou se já foram carregadas.

Os builds oficiais atuais usam assinatura ad-hoc e podem não estar notarizados. Se o Gatekeeper bloquear a abertura, use **Ajustes do Sistema > Privacidade e Segurança > Abrir Mesmo Assim**. O instalador não remove a quarentena nem contorna as proteções do macOS.

## O que permanece no projeto

O repositório também guarda a fonte permanente dos dois PAKs registrados em
`dist/id1`. Em `quake-world`, eles e as configurações pessoais permanecem quando
o runtime for removido. Apps, executáveis, addons, texturas, fontes upstream e
cache continuam reconstruíveis.

## Testar o instalador

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s installer/tests -v
```

Os testes usam somente diretórios temporários e não alteram `quake-world` nem o cache real.
