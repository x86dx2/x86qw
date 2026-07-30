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
sem argumentos mostra esse guia de uso; a ação principal é `./x86qw.sh play`.

Depois da instalação, a CLI não oferece instalação arbitrária de clientes,
canais, mods ou presets. Esse papel pertence exclusivamente ao `install.sh` (ou
ao `install.ps1` no Windows). `./x86qw.sh update` atualiza a própria CLI e somente o
que já está instalado. `./x86qw.sh upgrade` também incorpora componentes novos que
passaram a integrar o perfil `essential`, `recommended`, `complete` ou `custom`
registrado naquela instalação. Ambos mostram o plano completo e exigem que o
jogador digite `yes` antes de alterar arquivos; `--yes` confirma o plano em
automações e `--dry-run` encerra depois de apresentá-lo. O plano é uma tabela
com somente as mudanças reais — tipo, item, versão instalada, versão disponível
e ação. Quando não há mudanças, o comando informa isso e termina sem confirmação,
aplicação ou verificação integral.

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
│   ├── docs/
│   │   └── installer.md  manual completo
│   └── packages/         histórico de bundles públicos imutáveis
│       ├── latest → <versão atual>
│       └── <versão>/     pacote daquela release do instalador
└── manifest.json         origem, consumidor, tamanho e SHA-256 dos upstreams
```

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
