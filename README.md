# x86QW

x86QW e uma distribuicao moderna, reproduzivel e autocontida de QuakeWorld.
Este repositorio e a fonte canonica: os clientes ezQuake, o conteudo selecionado
do nQuake, os mods incorporados, as customizacoes x86QW e os PAKs registrados
ficam permanentemente em `dist/`. GitHub Releases e GitLab Generic Packages sao
apenas mirrors derivados para instalacoes sem um checkout completo.

Repositores: [GitHub](https://github.com/x86dx2/x86qw) e
[GitLab](https://gitlab.com/x86dx2/x86qw). Site:
[x86qw.x86.com.br](https://x86qw.x86.com.br/). Releases atuais:
[GitHub Releases](https://github.com/x86dx2/x86qw/releases).

## Instalar e jogar

Jogadores não precisam clonar este repositório. No macOS ou Linux:

```sh
/bin/bash -c "$(curl -fsSL https://x86qw.x86.com.br/install.sh)"
```

No Windows PowerShell:

```powershell
irm https://x86qw.x86.com.br/install.ps1 | iex
```

O bootstrap valida o bundle enxuto do instalador por SHA-256, consulta somente
o catálogo público e pergunta onde instalar, oferecendo `~/Games/x86qw` apenas como sugestão. O
cliente do sistema atual é detectado automaticamente, sem perguntar o SO. Para
preparar outro cliente a partir de macOS ou Linux, use, por exemplo:

```sh
/bin/bash -c "$(curl -fsSL https://x86qw.x86.com.br/install.sh)" -- --platform windows
```

Os valores aceitos são `macos`, `linux` e `windows`. Ao
concluir, a CLI permanente fica na raiz escolhida e oferece `play`, `verify`,
`hub`, `update`, `upgrade`, `cleanup`, `uninstall` e `uninstall --purge`. Executar `./x86qw.sh`
sem argumentos mostra esse guia de uso e a versão instalada; `./x86qw.sh version`
e `./x86qw.sh --version` imprimem somente a versão. A ação principal é
`./x86qw.sh play`.

O KTX possui seleção própria de modo no menu. Também pode ser aberto
diretamente, com modo e mapa opcionais:

```sh
./x86qw.sh play ktx --mode duel
./x86qw.sh play ktx --mode duel --map dm6 --bots 2 --bot-skill 8
./x86qw.sh play ktx --mode ctf --ctf-hook smooth --ctf-runes off
./x86qw.sh play ktx --mode race --map slide1 --race-style match --race-scoring formula1
```

O catálogo cobre os 17 usermodes nativos — Duel, 2on2, 3on3, 4on4, 10on10,
FFA, CTF, HoonyMode, Blitz 2v2/4v4, 2on2on2, 3on3on3, 4on4on4, XonX,
Wipeout, Clan Arena e ThunderWalker ToT — e sete variações oficiais: Midair,
DMM4, Instagib, LGC, Rocket Arena, Race e Practice. `--help` lista as opções de
Frogbot (quantidade/fill, habilidade 1-20, equipe, arma e vida), os estilos de
CTF e os formatos, pontuações e pacemaker de Race. O launcher valida a rota do
mapa antes de ativar um bot, limita Race às 54 rotas oficiais e mantém bots
desligados em Race e CTF, conforme exigido pelo QVM 1.47.

O perfil completo também instala MVDSV, QTV e QWFWD como componentes
independentes e verificáveis. Eles iniciam apenas em primeiro plano e usam
loopback por padrão; exposição à LAN/Internet exige um `--bind` explícito:

```sh
./x86qw.sh host
./x86qw.sh host ktx --mode 4on4 --map dm3
./x86qw.sh host team-fortress --map 2fort5r
./x86qw.sh host td2 --map dm6 --bind 0.0.0.0 --with-qtv
./x86qw.sh proxy --bind 0.0.0.0
./x86qw.sh qtv --upstream 127.0.0.1:28501
```

`host` oferece os mesmos jogos instalados de `play`, mas executa somente o
MVDSV: KTX, Final Arena, Pro-X, Team Fortress ou Total Destruction 2. Para KTX,
modo, mapa, bots e regras de CTF/Race podem ser definidos sem abrir o ezQuake.
`--with-qtv` e `--with-proxy` continuam opcionais; `proxy` executa QWFWD e `qtv`
pode operar sozinho ou conectado a um MVDSV. `Ctrl+C`
encerra de forma coordenada todos os processos iniciados pelo comando. Senhas
são gravadas somente em configurações efêmeras privadas, nunca na linha de
comando nem na saída. Consulte [docs/HOSTING.md](docs/HOSTING.md).

Depois da instalação, a CLI não oferece instalação arbitrária de clientes,
canais, mods ou presets. Esse papel pertence exclusivamente ao `install.sh` (ou
ao `install.ps1` no Windows). `./x86qw.sh update` atualiza a própria CLI e somente o
que já está instalado. `./x86qw.sh upgrade` também incorpora componentes novos que
passaram a integrar o perfil `essential`, `recommended`, `complete` ou `custom`
registrado naquela instalação. Ambos mostram o plano completo e exigem que o
jogador confirme em um prompt `[y/n]` antes de alterar arquivos; `--yes` confirma
o plano em automações e `--dry-run` encerra depois de apresentá-lo. Perfis
históricos salvos incorretamente como `custom` são recuperados somente
quando os componentes presentes coincidem exatamente com um perfil conhecido;
seleções customizadas válidas são preservadas. A saída segue
o fluxo do Homebrew: baixa e valida o manifesto, mostra somente os pacotes
desatualizados em linhas tabuladas com versão instalada, versão disponível e
tamanho, pede confirmação e então informa o progresso de cada pacote. Quando não
há mudanças, o comando informa isso e termina sem confirmação, aplicação ou
verificação integral.

Quem clonou o repositório está no fluxo de desenvolvimento e pode usar as
fontes canônicas locais:

```sh
./dist/installer/bin/manager.py
./dist/installer/bin/manager.py verify
./dist/installer/bin/manager.py play
```

Stable e nightly coexistem. No macOS, o instalador remove o entitlement de
sandbox do bundle com a ferramenta nativa `codesign`, limpa bookmarks antigos
e preserva recibos reversiveis. Os mods QuakeC usam nomes de gamecode exclusivos
e os parametros `gamedir` corretos; arquivos pessoais e configuracoes que o
cliente reescreve nao sao tratados como payload imutavel.
Os launchers isolam a colecao com `-nohome`, e `qw/pak.lst` fixa a prioridade
dos PK3 para que texturas e addons sejam carregados sempre na mesma ordem.

O manual completo esta em [dist/installer/docs/installer.md](dist/installer/docs/installer.md).

## Estrutura

```text
dist/                    produto final canonico e versionado
maintenance/            manutencao, inventarios, receitas, testes e builds
site/                    site inteiro: produto, design, deploy, assets e testes
docs/                    arquitetura global da plataforma
ROADMAP.md                roteiro global do produto
```

Cada dominio guarda tudo que lhe pertence:

- `maintenance/inventory/` define componentes, dependencias, versoes, origens
  e a fronteira de arquivos aceitos;
- `maintenance/recipes/` registra artefatos stable byte a byte;
- `maintenance/tools/` contem apenas modulos internos usados pelo gerenciador;
- `maintenance/tests/` valida distribuição, manutenção e instalador;
- `site/tests/` valida o site e a projeção pública dos bootstraps;
- `maintenance/build/` recebe ZIPs derivados e e ignorado pelo Git;
- `site/wrangler.jsonc`, `site/PRODUCT.md`, `site/DESIGN.md` e `site/docs/`
  pertencem ao site, sem arquivos de site soltos na raiz;
- `quake-world/`, caches e `__pycache__` sao estado local ignorado, nunca fonte
  da distribuicao.

`dist/` tem somente material usado pelo produto:

```text
dist/
├── clients/
│   └── ezquake/
│       ├── stable/       releases oficiais, separadas por versao
│       └── nightly/      snapshots de desenvolvimento, separados por build
├── distributions/
│   └── nquake/           snapshot fixado e particionado pelo BOM
├── game-data/
│   └── id1/              fontes canônicas de pak0.pak e pak1.pak registrados
├── mods/                 KTX, Final Arena, Pro-X, Team Fortress, TD2 e perfis x86QW
├── installer/            bundle versionado usado pelo bootstrap público
│   ├── README.md         contrato e manutenção deste contexto
│   ├── bin/              executáveis e módulos distribuídos
│   │   ├── install.sh    bootstrap canônico para macOS e Linux
│   │   ├── install.ps1   bootstrap canônico para Windows
│   │   ├── x86qw.sh      launcher permanente macOS/Linux
│   │   ├── x86qw.cmd     launcher permanente Windows
│   │   ├── manager.py    gerenciador de instalação e manutenção
│   │   ├── gameplay.py   módulo interno especializado em gameplay local
│   │   ├── services.py   MVDSV, QTV e QWFWD em primeiro plano
│   ├── docs/
│   │   └── installer.md  manual completo
│   └── packages/         histórico de bundles públicos imutáveis
│       ├── latest → <versão atual>
│       └── <versão>/     pacote daquela release do instalador
└── manifest.json         origem, consumidor, tamanho e SHA-256 dos upstreams
```

`manager.py`, `gameplay.py` e os módulos auxiliares são fontes do repositório.
O builder os empacota em `x86qw.pyz`; na instalação do jogador, a aplicação e
seu recibo ficam juntos em `.install/cli/`.

## Manter a distribuicao

`maintenance/manage.py` e a unica interface oficial de manutencao:

```sh
./maintenance/manage.py check
./maintenance/manage.py update --dry-run
./maintenance/manage.py update
./maintenance/manage.py verify
./maintenance/manage.py build
./maintenance/manage.py publish --dry-run
./maintenance/manage.py commit
```

`update` descobre upstreams, prepara uma arvore temporaria, baixa somente
arquivos com consumidor declarado, remove versoes superadas e atualiza
`dist/`, inventarios, receitas, catalogo e pacotes de componentes como uma
unica transacao. KTX, TD2 ou outro componente independente nunca recebem uma
versao inventada: quando o upstream muda, o comando exige uma definicao
revisada via `add`.

Cada mod declara sua composicao real. KTX mescla a base curada do nQuake com a
release oficial; Final Arena preserva integralmente a unica versao comprovada;
Pro-X substitui a referencia 0.8b pelo pacote completo 1.1; Team Fortress mantem
os assets nQuake, elimina o gamecode 2.8 incorporado e aplica o 2.9 oficial; TD2
parte somente de sua distribuicao 2.22. Em todos os casos a harmonizacao x86QW
e aplicada por ultimo. Conteudo exclusivo e preservado; conflitos ou remocoes
precisam de decisao e hashes registrados antes de um novo pacote ser gerado.

O contrato completo, incluindo o formato de inclusao de pacotes e
configuracoes, esta em [maintenance/README.md](maintenance/README.md).

## Site

Tudo relacionado ao portal esta em `site/`. Para executar localmente:

```sh
cd site
npx --yes wrangler@4.114.0 dev --ip 127.0.0.1 --port 8787
```

Abra <http://127.0.0.1:8787>. Instrucoes de deploy ficam em
[site/docs/cloudflare.md](site/docs/cloudflare.md).

## Validacao integral

```sh
./maintenance/manage.py verify
./dist/installer/bin/manager.py --help
./dist/installer/bin/manager.py play --help
cd site && npx --yes wrangler@4.114.0 deploy --dry-run
```

O primeiro comando valida hashes do `dist/`, particao nQuake, inventarios,
receitas, catalogo, organizacao do repositorio e as tres suites de testes. Nao
instala dependencias adicionais.

## Principios

- somente arquivos com utilidade direta e consumidor declarado entram no Git;
- o `dist/` preserva a copia exata do upstream e separa customizacoes x86QW;
- versoes publicadas sao imutaveis e identificadas por tamanho e SHA-256;
- mapas e LOCs externos não são baixados em massa; entra apenas o acervo curado do nQuake;
- o modo de desenvolvimento materializa fontes locais; o modo público usa apenas mirrors do catálogo;
- binarios grandes usam Git LFS; R2 nao faz parte da arquitetura atual;
- `id1` e tratado como material registrado e validado por SHA-256; seu ZIP de
  dados-base é publicado separadamente e nunca incorporado ao bundle do instalador.
