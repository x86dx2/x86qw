# QuakeWorld moderno e multiplataforma

Este projeto monta uma instalação autocontida em `quake-world`. O mesmo instalador Python executa no macOS, Linux ou Windows, pode preparar binários para qualquer um dos três sistemas e acrescenta recursos modernos sem substituir arquivos pessoais. Ele não instala pacotes nem arquivos globais.

Requisitos:

- Python 3.10 ou mais recente;
- Git, utilizado somente na Fase 2 para obter os arquivos nQuake sem baixar componentes de servidor desnecessários.

O instalador usa apenas a biblioteca padrão do Python.

## Instalar

Mantenha os PAKs registrados originais em:

```text
quake-world/id1/pak0.pak
quake-world/id1/pak1.pak
```

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
├── client-classicq-macos.receipt
├── client-unezquake-macos.receipt
├── nquake.receipt
├── nquake.inventory
├── maps.{receipt,inventory}
├── presets.{receipt,inventory}
└── classicq.{receipt,inventory}
```

O bootstrap trabalha somente com essa estrutura. O par `nquake.{inventory,receipt}` é atualizado em conjunto, sem publicar versões incompatíveis entre si.

### Fases

A execução continua dividida em duas fases:

1. **ezQuake:** seleciona, baixa, valida e instala o artefato do SO escolhido.
2. **nQuake:** após confirmação explícita, instala os dados, texturas e addons compartilhados por todos os binários.

Ao terminar a primeira fase, o instalador pergunta:

```text
Deseja instalar/atualizar também os dados nQuake? [s/N]
```

O padrão é `N`. A Fase 2 precisa ser instalada somente uma vez no mesmo `quake-world`; executá-la novamente atualiza seus arquivos gerenciados sem substituir configurações pessoais. O executável Windows antigo presente nos distfiles não faz parte do overlay.

- `stable`: releases estáveis aprovadas e espelhadas pelo x86QW;
- `nightly`: snapshots de desenvolvimento aprovados e espelhados pelo x86QW.

As duas listas vêm de `https://x86qw.x86.com.br/api/v1/catalog.json`.
Cada entrada registra origem, licença revisada, tamanho, SHA-256 e uma lista
ordenada de mirrors. Se uma cópia estiver indisponível ou entregar um hash
incorreto, o instalador tenta a próxima automaticamente.

O instalador grava o commit exato de `nQuake/distfiles` usado. Servidores e shareware ficam de fora. Em uma instalação nova, também cria `ezquake/configs/preset.cfg` com o ajuste mínimo de volume esperado pelo primeiro start; um preset existente nunca é substituído.

## Recursos modernos opcionais

Cada recurso tem uma ação explícita. Nada abaixo é instalado silenciosamente pela ação `install`:

```sh
./install-qw.py clients
./install-qw.py maps
./install-qw.py presets
./install-qw.py hub
```

### Clientes alternativos

`clients` instala, atualiza ou remove um cliente alternativo sem substituir ezQuake stable ou nightly:

- [classicQ](https://github.com/classicq/classicq): cliente de aparência clássica com SDL3 e renderizador Metal nativo no Apple Silicon. No macOS é instalado como `classicQ.app`; Linux e Windows recebem `classicq-x86_64` e `classicq.exe`;
- [unezQuake](https://github.com/dusty-qw/unezquake): fork experimental com antilag, predição, HUD e crosshair vetorial. É instalado como `unezQuake.app`, `unezquake-x86_64.AppImage` ou `unezquake.exe`.

As duas famílias coexistem com todos os ezQuake instalados. O catálogo mostra
somente pacotes cuja licença e redistribuição foram revisadas pelo x86QW e cujo
SHA-256 foi registrado. Como os três pacotes do classicQ compartilham
`classicq/classicq.pak`, eles precisam permanecer na mesma versão dentro de um
`quake-world` transportável.

### Mapas e LOCs

`maps` consulta ao vivo o arquivo comunitário [maps.quakeworld.nu](https://maps.quakeworld.nu/), com quatro escolhas:

- `base`: conjunto base recomendado;
- `core`: coleção comunitária ampliada;
- `individual`: um mapa informado pelo nome;
- `all`: arquivo completo, protegido por uma confirmação adicional por poder ocupar bastante espaço.

Para todo BSP selecionado, todo mapa original dos PAKs e todo mapa já detectado em BSP, PK3 ou PAK local, o LOC de mesmo nome é incluído automaticamente quando existir. Um LOC é apenas o arquivo de nomes das regiões do mapa usado por mensagens de equipe e HUD; ele é pequeno, mas só é útil junto ao BSP correspondente. Os arquivos ficam em `qw/maps`. O servidor não publica checksums próprios, portanto cada download é feito novamente por HTTPS, validado como BSP v29 quando aplicável e registrado com SHA-256 local no inventário.

Arquivos já existentes que não pertencem ao componente `maps` são preservados. A remoção apaga somente os hashes ainda idênticos aos que o instalador gravou.

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

### Navegador de servidores

`hub` consulta a API pública do [QuakeWorld Hub](https://hub.quakeworld.nu/), mostra servidores ativos com humanos, bots, modo e mapa, e abre um cliente já instalado para:

- jogar: informe o número do servidor;
- observar diretamente: informe `o` seguido do número, como `o3`;
- assistir via QTV: informe `q` seguido do número, como `q3`.

Se houver mais de um cliente compatível com o sistema atual, o instalador pergunta qual abrir. A execução recebe `-basedir quake-world`, portanto todos compartilham os mesmos PAKs e dados. Não há registro global de protocolo nem alteração no navegador ou no sistema operacional.

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

O instalador consulta somente o catálogo público x86QW para ezQuake e clientes
alternativos. Para desenvolvimento, `X86_QW_CATALOG_URL` permite apontar para
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
- clientes classicQ e unezQuake instalados e seus recibos por plataforma;
- hashes dos mapas, LOCs, presets e dados compartilhados do classicQ gerenciados.

## Desinstalar e limpar o cache

```sh
./install-qw.py uninstall
./install-qw.py purge
./install-qw.py cleanup
```

`uninstall` remove todos os binários macOS, Linux e Windows comprovadamente gerenciados, clientes opcionais, mapas, LOCs, presets próprios do instalador, seus recibos e os arquivos cujo hash ainda corresponde ao inventário. Arquivos modificados são preservados. Os PAKs, `config.cfg`, demos, screenshots, logs, presets pessoais e outros arquivos pessoais permanecem em `quake-world`.

O recibo é a autoridade para a remoção: `uninstall` também conclui quando um app ou executável registrado já está ausente ou incompleto. Use `verify` quando quiser exigir e diagnosticar a integridade dos runtimes instalados.

`purge` é a remoção total: apaga tudo dentro de `quake-world`, incluindo arquivos pessoais e metadados desconhecidos, preservando somente a árvore `id1`. Também remove o cache nativo criado pelo instalador. A ação recusa alvos sem um diretório `id1` real.

`cleanup` remove somente o cache criado pelo próprio instalador, incluindo downloads dos três sistemas, clientes opcionais, mapas e LOCs. A remoção só ocorre se o marcador de propriedade criado pelo instalador estiver presente. O diretório é resolvido conforme o host:

- macOS: `$(getconf DARWIN_USER_CACHE_DIR)/x86-qw`;
- Linux: `$XDG_CACHE_HOME/x86-qw` ou `~/.cache/x86-qw`;
- Windows: `%LOCALAPPDATA%\x86-qw`.

Para consultar o caminho neste Mac:

```sh
printf '%s/x86-qw\n' "$(getconf DARWIN_USER_CACHE_DIR | sed 's#/$##')"
```

## Atualizar ou trocar de canal

Execute a ação correspondente novamente: `install` para ezQuake/nQuake, `clients` para clientes alternativos, `maps` para mapas+LOCs ou `presets` para configurações. Somente o componente escolhido é substituído; os demais binários e arquivos pessoais permanecem preservados.

Não há uso silencioso do alias `latest`: uma nightly é sempre baixada pelo nome exato, com data, hora e commit.

## Primeira execução no macOS

Abra `quake-world/ezQuake Stable.app` ou `quake-world/ezQuake Nightly.app`. Se o ezQuake pedir o diretório que contém `id1/pak0.pak`, escolha a própria pasta `quake-world`.

Os builds oficiais atuais usam assinatura ad-hoc e podem não estar notarizados. Se o Gatekeeper bloquear a abertura, use **Ajustes do Sistema > Privacidade e Segurança > Abrir Mesmo Assim**. O instalador não remove a quarentena nem contorna as proteções do macOS.

## O que permanece no projeto

O repositório guarda somente o instalador, seus testes e a documentação. Em `quake-world`, permanecem apenas os PAKs fornecidos pelo usuário e configurações pessoais quando o runtime for removido. Apps, executáveis, addons, texturas, fontes upstream e cache são reconstruíveis.

## Testar o instalador

```sh
python3 -m unittest discover -s tests -v
```

Os testes usam somente diretórios temporários e não alteram `quake-world` nem o cache real.
