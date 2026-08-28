# Manual do x86QW moderno e multiplataforma

Este projeto monta uma instalação autocontida em `quake-world`. O mesmo instalador Python executa no macOS, Linux ou Windows, pode preparar binários para qualquer um dos três sistemas e acrescenta recursos modernos sem substituir arquivos pessoais. Ele não instala pacotes nem arquivos globais.

Requisito: Python 3.10 ou mais recente.

O bundle da árvore-fonte é a baseline `1.0.4`, com o catálogo current apontando
para esse instalador e 21 componentes registrados. A `1.0.3`, a `1.0.2`, a `1.0.1`, a `0.7.13` e a `1.0.0`
owner-only permanecem históricas e imutáveis. A audiência continua
`owner-only`: disponibilidade do artefato e GitHub Latest não autorizam
usuários externos nem transformam as plataformas preview em suporte. Essa
versão confere o requisito Python por
`sys.version_info` antes de qualquer download ou mutação: macOS/Linux testam
`python3` e `python`, nessa ordem; Windows testa `py -3`, `python3` e `python`.
A instalação grava no launcher o executável validado e o launcher repete a
resolução caso esse caminho desapareça ou fique incompatível. O contrato
`portable-contract` obrigatório cobre macOS Python 3.10 e 3.13; Linux e
Windows permanecem preview manual. Isso não é smoke nativo de runtime. O bundle `0.7.3` permanece
imutável no histórico.

O instalador usa apenas a biblioteca padrão do Python.
Esses fatos públicos são validados contra os inventários canônicos.

## Instalação owner-only

O comando abaixo pertence ao fluxo publicado para o escopo `owner-only`. Antes
de compartilhar ou automatizar o fluxo, consulte a [verdade de release e
audiência](../../../docs/post-1.0/RELEASE-TRUTH-CURRENT.md). A transição para
`external-public` exige decisão e os gates EP-0–EP-5; ela não é implícita pela
existência do bootstrap.

Jogadores não precisam clonar o repositório. Em macOS e Linux, execute:

```sh
curl -fsS https://qw.x86.com.br/install.sh | bash
```

No Windows PowerShell:

```powershell
& { Add-Type -AssemblyName System.Net.Http; $h = [System.Net.Http.HttpClientHandler]::new(); $h.AllowAutoRedirect = $false; $c = [System.Net.Http.HttpClient]::new($h); $c.Timeout = [TimeSpan]::FromSeconds(60); $c.MaxResponseContentBufferSize = 262144; $r = $null; try { $r = $c.GetAsync('https://qw.x86.com.br/install.ps1').GetAwaiter().GetResult(); if (-not $r.IsSuccessStatusCode) { throw "x86QW: HTTP $([int]$r.StatusCode)." }; if ($r.Content.Headers.ContentLength -gt 262144) { throw 'x86QW: bootstrap excedeu 262144 bytes.' }; $s = $r.Content.ReadAsStringAsync().GetAwaiter().GetResult(); & ([scriptblock]::Create($s)) @args } finally { if ($null -ne $r) { $r.Dispose() }; $c.Dispose(); $h.Dispose() } }
```

O bootstrap não usa `exit`: a janela atual do PowerShell permanece aberta ao
fim da instalação. O código devolvido pelo instalador Python fica disponível em
`$LASTEXITCODE`; em caso de falha, o bootstrap também imprime um erro com esse
código antes de devolver o controle ao terminal.

O comando Windows recusa scripts acima de 256 KiB antes de executá-los: o
buffer limitado do `HttpClient` substitui o fluxo ilimitado `irm | iex`. No
Unix, o corpo do bootstrap só corre na última linha `x86qw_install_main "$@"`;
um download truncado falha na análise e não executa o instalador. O comando
público é `curl -fsS https://qw.x86.com.br/install.sh | bash`.

Na `0.7.1`, o corpo executa em um escopo de script próprio dentro da
mesma sessão PowerShell. Isso não cria outra janela nem outro processo: apenas
impede que variáveis internas e `$ErrorActionPreference` vazem para o chamador,
restaura as codificações de saída e mantém como efeito intencional somente o
`$global:LASTEXITCODE` do instalador. O isolamento passou nos jobs nativos do
runner Windows.

No Windows, o bootstrap exige Python 3.10 ou mais recente e testa `py -3`,
`python3` e `python`, nessa ordem. A presença do alias `python.exe` de
`WindowsApps` não basta: o comando precisa responder com uma versão compatível.
Quando nenhuma instalação real for encontrada, instale-a, abra um novo
PowerShell e repita o bootstrap:

```powershell
winget install --id Python.Python.3.13 -e
```

O bootstrap público tenta os mirrors GitHub e GitLab e valida o SHA-256 do
bundle antes de extrair. A `0.7.1` calcula esse hash em blocos de 1 MiB no
Unix. O instalador
ativa o modo remoto estrito. Nesse modo, o catálogo e todos os
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

## Fronteira de downloads

O código publicado na `0.7.2`
centraliza bytes HTTP recebidos pelo Python. Artefatos persistentes exigem antes
da chamada uma URL HTTPS, tamanho exato, SHA-256, limite máximo e deadline total.
O limite global aceito pelo catálogo é 512 MiB; o catálogo do instalador tem
limite de 2 MiB e a consulta do Hub, 1 MiB.

`Content-Length` é validado quando existe. Sua ausência não remove o limite de
streaming. O deadline monotônico cobre conexão, leitura, pausas e todas as
tentativas. Somente falhas transitórias recebem retry no mesmo mirror; uma falha
de integridade pode avançar para outro mirror equivalente, mas erros de política,
protocolo, limite, armazenamento e deadline encerram a operação. Corpos
intermediários de redirect são fechados sem leitura. Um destino anterior
permanece intacto: o conteúdo entra primeiro em temporário exclusivo, recebe
modo `0600` no POSIX, `flush` e `fsync` e só é promovido por substituição atômica
depois de tamanho e hash corresponderem.

Metadados dinâmicos são efêmeros e limitados, mas limite de recursos e TLS não
equivalem a autenticação versionada do catálogo. Esse contrato é uma etapa de
segurança separada. A decisão completa está no
[`ADR 0001`](../../../docs/adr/0001-fronteira-limitada-de-bytes-remotos.md).

URLs persistidas no catálogo, manifesto e inventários de releases e upstreams
usam o mesmo validador HTTPS e não aceitam credenciais, fragmentos, queries,
espaços ou controles. No intake de manutenção, um arquivo remoto novo também
precisa corresponder exatamente a uma release, fonte preservada, pacote público
proposto ou referência nQuake, dentro do namespace e consumidor declarados. Um
pacote ezQuake usa obrigatoriamente
`clients/ezquake/<canal>/<versão>/<plataforma>-<arquitetura>/<arquivo>`; essas
coordenadas precisam coincidir com os metadados do pacote.

No POSIX, arquivos e diretórios privados usam `0600` e `0700`. No código
corretivo da PR 4, objetos privados gerenciados no Windows nascem com DACL
protegida e exatamente duas ACEs: usuário atual e `LOCAL SYSTEM`. O diretório
de trabalho do bootstrap PowerShell recebe essa política antes de helper,
bundle ou extração serem escritos. Arquivos de senha externos são apenas
validados por handle e nunca têm owner, DACL ou conteúdo reescritos. Se a ACL
persistente não puder ser comprovada, a operação falha de forma conservadora e
sem solicitar elevação. A matriz nativa Windows com Python 3.10 e 3.13 validou
DACL, herança hostil, arquivos de senha e bootstrap. O smoke de runtime sob uma
conta padrão sem elevação continua pendente na evidência de release; a release
pública `0.7.2` contém a mudança. Consulte o
[`ADR 0003`](../../../docs/adr/0003-dacl-privada-windows.md).

Ao concluir, a raiz da instalação contém `x86qw.sh` e `x86qw.cmd`. Esses comandos
usam a aplicação única `.x86qw/cli/x86qw.pyz`; as ações públicas são `play`,
`host`, `proxy`, `qtv`, `status`, `hub`, `update`, `upgrade`, `verify`, `doctor`, `ui`, `repair`,
`cleanup`, `uninstall` e `version`. Sem argumento, a CLI
abre o navegador interativo e não inicia instalação alguma até uma ação ser
confirmada. Setas ou `j`/`k` navegam, Enter seleciona, `←` volta, Esc sai e
`/` busca; números com dois ou mais dígitos usam Enter para confirmar. Listas
longas indicam a faixa visível e o total, opções indisponíveis explicam o motivo
e o layout quebra linhas em terminais estreitos. Um fallback numerado mantém o
fluxo utilizável sem TTY. Depois de uma execução ou diagnóstico, o resultado
permanece na tela até Enter; em seguida o navegador retorna ao submenu que
iniciou a ação. O clone e os comandos
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
curl -fsS https://qw.x86.com.br/install.sh | bash -s -- --platform windows
```

No PowerShell, o equivalente para preparar Linux a partir do Windows é:

```powershell
& { Add-Type -AssemblyName System.Net.Http; $h = [System.Net.Http.HttpClientHandler]::new(); $h.AllowAutoRedirect = $false; $c = [System.Net.Http.HttpClient]::new($h); $c.Timeout = [TimeSpan]::FromSeconds(60); $c.MaxResponseContentBufferSize = 262144; $r = $null; try { $r = $c.GetAsync('https://qw.x86.com.br/install.ps1').GetAwaiter().GetResult(); if (-not $r.IsSuccessStatusCode) { throw "x86QW: HTTP $([int]$r.StatusCode)." }; if ($r.Content.Headers.ContentLength -gt 262144) { throw 'x86QW: bootstrap excedeu 262144 bytes.' }; $s = $r.Content.ReadAsStringAsync().GetAwaiter().GetResult(); & ([scriptblock]::Create($s)) @args } finally { if ($null -ne $r) { $r.Dispose() }; $c.Dispose(); $h.Dispose() } } --platform linux
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
    ├── x86qw-client-bootstrap/
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
2. **componentes x86QW:** o caminho recomendado instala um perfil jogável.
   Somente cliente fica em Avançado, com confirmação da consequência.

Ao terminar a primeira fase, o instalador pergunta:

```text
Qual conteúdo deseja instalar?
  1) Recomendado (padrão)
  2) Avançado
```

O padrão é `Recomendado` e produz KTX, mapas e configuração para jogar agora.
A conclusão mostra o comando `Jogar agora`. Em Avançado há:

- `essencial`: bootstrap, interface principal e KTX;
- `completo`: os 21 componentes atuais, incluindo QRP, os cinco jogos, MVDSV, QTV e QWFWD;
- `personalizado`: seleção individual, com dependências acrescentadas de forma explícita;
- `somente cliente`: ezQuake sem mods, só depois de confirmar que Jogar recusará até adicionar KTX.

O perfil `recomendado` permanece a experiência base nQuake, sem matchinfo, QRP nem mods opcionais.

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
Na primeira execução, `qw/x86qw-default.cfg` é aplicado uma vez depois de
`nquake_default.cfg` e `ezquake/configs/preset.cfg`; o resultado combinado é
salvo pelo ezQuake no `config.cfg` pessoal. Essa camada inicial é gerenciada
pelo produto, mas não é reaplicada depois que `_nquake_first_startup` muda para
`0`, permitindo que o jogador altere os valores normalmente.
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
`https://qw.x86.com.br/api/v1/catalog.json`.
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

Ao selecionar KTX, o navegador agrupa os modos curados em **Recomendados**,
**Competitivo individual**, **Equipes**, **Arena e alternativos** e **Treino e
movimento**. **Todos os modos** preserva uma lista pesquisável do catálogo
completo:

```text
duel  2on2  3on3  4on4  10on10  ffa  ctf  hoony  blitz-2on2  blitz-4on4  2on2on2
3on3on3  4on4on4  xonx  wipeout  clan-arena  tw-tot
midair  dmm4  instagib  lgc  rocket-arena  race  practice
```

`3on3` significa duas equipes de três jogadores; `2on2on2`, três equipes de
dois. Os aliases `3v3` e `2v2v2` continuam aceitos pela CLI.

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
também aceita habilidade 1–20 ou `random` por bot e equipe. Arma, vida e
interrupção por morte ficam restritas ao ToT. O menu oferece somente mapas que
tenham BSP e uma das 77 rotas `.bot` incorporadas ou uma rota pessoal regular.
CTF e Race rejeitam bots por serem combinações não suportadas pelo QVM fixado.
Race cruza os BSPs com as 54 rotas oficiais ou pessoais; CTF cruza os BSPs com
os seis ENTs gerenciados ou um ENT pessoal seguro.

`--bot-names default` mantém os nomes originais do KTX sem definir cvars de
customização. `--bot-names x86qw` sorteia por lançamento uma lista One Piece,
priorizando os dez Chapéus de Palha. `--bot-names personal` usa na ordem
declarada `quake-world/qw/x86qw-frogbot-names.json`, criado pelo bootstrap e
nunca sobrescrito depois de uma edição. O launcher aplica automaticamente o
prefixo `/` e a cor clássica compatível com o protocolo; escreva no JSON
somente o nome, sem prefixo ou códigos no valor. Campos de aparência são
rejeitados para que as cores continuem inequivocamente sob controle do KTX. O contrato completo está em
[`docs/FROGBOTS.md`](../../../docs/FROGBOTS.md).

No menu, `x86QW aleatório` é a seleção inicial e o perfil sem customização
aparece como `KTX Default`. A CLI conserva `default` como padrão por
compatibilidade. Modos de tamanho fixo oferecem somente as vagas restantes —
Duel aceita um bot com o jogador humano — enquanto FFA e Practice mantêm
preenchimento e quantidade personalizada. Vários bots entram em frames
separados. Durante a sessão, `INS` respeita a lotação, `DEL` reabre a vaga e
`HOME`/`END` percorrem cumulativamente a habilidade 1–20 dos próximos bots.

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
stable e nightly coexistirem, pergunta qual cliente usar. Por fim, mostra um
resumo de jogo, modo, mapa, cliente e Frogbots, imprime o comando equivalente e
pede confirmação. Voltar retorna à escolha de cliente; recusar não abre nenhum
processo.

No macOS com notch, o modo de compatibilidade de área segura pode reduzir a
janela do ezQuake sem reduzir o framebuffer SDL, recortando o topo de telas como
**Options**. O canal stable preserva integralmente `Info.plist`, sandbox,
entitlements e assinatura do bundle upstream: o x86QW não o re-assina nem
promete alterar a política de área segura. O nightly mantém provisoriamente a
preparação local preexistente, isolada do stable e declarada condicional.

A configuração pessoal de vídeo ainda pode usar fullscreen explícito
(`vid_fullscreen 1` e `vid_usedesktopres 0`) com a resolução 16:10 segura
detectada para o painel. Em
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
protocolo nem alteração no navegador ou no sistema operacional. Antes de abrir
o cliente, o menu revisa servidor, endereço, ação, stream QTV quando aplicável e
cliente; voltar retorna à escolha do cliente e recusar não inicia processo.

### Conteúdo visual e servidor próprio

O nQuake já fornece seu pacote visual, QRP map textures, skins, HUD e addons. O instalador não baixa automaticamente itens arbitrários de [gfx.quakeworld.nu](https://gfx.quakeworld.nu/): esses arquivos têm autores, licenças, estilos e destinos diferentes e frequentemente colidem entre si. Uma galeria curada exigirá uma lista explícita de itens compatíveis e licenciados, em vez de instalar o site inteiro.

O navegador do Hub moderniza o acesso a servidores públicos. O perfil completo
instala MVDSV, QTV e QWFWD como componentes separados, com fonte, runtime,
SHA-256, recibo e inventário. O x86QW preserva os binários oficiais Linux/Windows
e fornece builds macOS arm64 reproduzidos das fontes fixadas.

Os serviços ficam em loopback por padrão. Podem acompanhar o terminal ou usar
`--background`, mantendo o mesmo lock, journal, readiness e cleanup:

```sh
./x86qw.sh host --mode 4on4 --map dm3
./x86qw.sh host --mode duel --map dm6 --bind 0.0.0.0 --with-qtv
./x86qw.sh host ktx --mode duel --map dm6 --save-preset local-duel
./x86qw.sh host --preset local-duel
./x86qw.sh proxy --bind 0.0.0.0
./x86qw.sh qtv --upstream 127.0.0.1:28501
./x86qw.sh proxy --background
./x86qw.sh status
./x86qw.sh status --stop
```

No navegador interativo, **Hospedar** segue a ordem jogo → modo e regras → mapa
→ configuração → resumo. **Rápido local** usa `127.0.0.1:28501`, 16 clientes,
gravação MVD e nenhum QTV/QWFWD. **Avançado** revela interfaces, portas,
capacidade, gravação, serviços adicionais e entrada oculta de senhas. O resumo
final redige segredos e a confirmação acontece antes do lock, do preflight e da
criação de processos.

QTV e QWFWD iniciados isoladamente seguem o mesmo contrato: configuração,
resumo de endpoints e parâmetros não sensíveis, comando equivalente seguro e
confirmação antes do lock. Voltar reabre a configuração e recusar não inicia
processo. O estado e o resultado de cada serviço permanecem visíveis até Enter
antes do retorno ao submenu **Serviços**.

No perfil avançado e nos serviços isolados, o menu escolhe primeiro ou segundo
plano. Uma stack destacada registra seu log privado em `.x86qw/logs/`. A área
**Visualizar serviços ativos** mostra modo de execução, controlador, processos,
endpoints e parâmetros; **Encerrar serviços ativos** confirma e solicita ao
próprio controlador o shutdown ordenado. Na CLI, use `status --stop` e acrescente
`--yes` somente em automação.

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
Em outro terminal, `status` consulta esse estado sem adquirir lock ou alterar a
stack. Ele mostra controlador, processos, PIDs, executáveis, endpoints e somente
parâmetros não sensíveis; senhas aparecem apenas como configuradas ou ausentes.
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

Para um diagnóstico somente leitura da instalação, catálogo, TUF local, runtime,
disco e permissões, sem alterar arquivos:

```sh
./dist/installer/bin/manager.py doctor
./dist/installer/bin/manager.py doctor --json
./dist/installer/bin/manager.py doctor --bundle
```

`--bundle` grava `x86qw-doctor.zip` fora da instalação, com `NOTICE.txt` e um
`doctor.json` sanitizado. Não há upload. Revise o zip antes de partilhar. Com
`--json --bundle`, o envelope JSON permanece inalterado no stdout e o caminho
do zip vai para o stderr.

O painel local `ui` grava um HTML somente leitura com o mesmo `doctor` e a
library, fora da instalação:

```sh
./dist/installer/bin/manager.py ui --output /tmp/x86qw-ui.html
```

A primeira instalação e o próprio `doctor` lembram o modo owner-only: um
usuário, Apple M3, instalação limpa permitida; Windows e Linux continuam
preview.

Configurações pessoais ficam na classe `profile` (`config.cfg`, `preset.cfg`,
`x86qw-user.cfg` e equivalentes). Cache e demos não entram no zip:

```sh
./dist/installer/bin/manager.py profile
./dist/installer/bin/manager.py profile --backup
./dist/installer/bin/manager.py profile --restore x86qw-profile.zip
```

Favoritos e recentes ficam em `qw/x86qw-library.json` (classe `profile`, entra
no backup). Cada entrada guarda endereço, origem (`user`/`hub`/`local`) e
freshness UTC:

```sh
./dist/installer/bin/manager.py library
./dist/installer/bin/manager.py library --add quake.example:27500
./dist/installer/bin/manager.py library --remove quake.example:27500
```

`hub` consulta o QuakeWorld Hub; se a rede falhar, usa favoritos e recentes
locais. Entrar em um servidor grava um recente com origem e freshness. Sem
dependência de QWLeague.

No Windows, substitua `./dist/installer/bin/manager.py` por `py -3 .\dist\installer\bin\manager.py` nos exemplos.

## Saída e diagnóstico

A saída padrão prioriza decisões, andamento e resultado. Downloads exibem uma barra de progresso quando o instalador roda em um terminal interativo. Em logs, redirecionamentos e automações, a barra é omitida para não poluir a saída. Downloads incompletos ou divergentes não são promovidos ao destino, e o modo detalhado nunca imprime o conteúdo remoto.

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
- estrutura, versão, integridade interna da assinatura e arquiteturas `arm64` e `x86_64` dos apps macOS; `codesign --verify` não comprova Developer ID, notarização ou autoria;
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

Os dois comandos atualizam primeiro a própria CLI por um bundle x86QW fixado
simultaneamente por versão, tamanho e SHA-256, então validado
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
hash, permissão AppImage e política do bundle macOS. Ele repara localmente
permissões, preparação nightly e estado reconstruível; um stable transformado
por uma versão anterior exige restaurar o payload upstream integral pela
reexecução do bootstrap no mesmo destino. Versão e canal registrados são
preservados sem downgrade. Recibo sem inventário, inventário sem recibo, runtime
sem metadata e estados ambíguos são diagnosticados sem exclusão ou inferência
destrutiva. Quando o plano exige payload, a CLI instalada orienta a reexecução
do bootstrap para obtê-lo pelo fluxo público validado. Arquivos pessoais e
arquivos gerenciados modificados são preservados.

Para converter os metadados de uma instalação legada para o contrato 1.0, use
`./x86qw.sh migrate --dry-run` para apenas visualizar o plano ou
`./x86qw.sh migrate` para executá-lo. A migração reorganiza recibos e estado
com hashes, journal e rollback; não baixa pacotes nem altera PAKs,
configurações pessoais, demos ou logs.

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

Feche qualquer ezQuake aberto antes de instalar ou atualizar. O stable oficial
preservado usa App Sandbox e uma autorização de diretório. A instalação inicial
remove uma seleção antiga para impedir que uma cópia nova continue lendo outro
diretório de jogo; update e repair preservam o bookmark já escolhido. O nightly
mantém sua preparação local preexistente e não é usado como modelo de confiança
do stable.

Abra `quake-world/ezQuake Stable.app` ou `quake-world/ezQuake Nightly.app`. Na janela que pede o diretório contendo `id1/pak0.pak`, escolha exatamente a própria pasta `quake-world` mostrada no resumo do instalador. Essa seleção é obrigatória para que o ezQuake encontre `qw/autoexec.cfg` e carregue a configuração inicial nQuake.

O menu principal pode manter a aparência clássica do Quake. Para verificar o estado real, execute `./dist/installer/bin/manager.py verify`: o resultado informa se as configurações nQuake estão aguardando a primeira abertura ou se já foram carregadas.

O stable upstream 3.6.9 usa assinatura ad hoc, hardened runtime e App Sandbox,
mas não apresenta Team ID nem ticket stapled e é rejeitado por `spctl`. Preservar
o bundle evita degradá-lo, mas não prova Developer ID, notarização ou autoria.
Se o Gatekeeper bloquear a abertura, use **Ajustes do Sistema > Privacidade e
Segurança > Abrir Mesmo Assim**. O instalador não remove a quarentena nem
contorna as proteções do macOS. O stable macOS é condicional e o nightly permanece preview
até os smokes nativos descritos no
[ADR 0004](../../../docs/adr/0004-preservar-bundle-upstream-ezquake-stable-macos.md).

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
