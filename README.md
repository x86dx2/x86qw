# x86QW

x86QW e uma distribuicao moderna, reproduzivel e autocontida de QuakeWorld.
Este repositorio e a fonte canonica: os clientes ezQuake, o conteudo selecionado
do nQuake, os mods incorporados, as customizacoes x86QW e os PAKs registrados
ficam permanentemente em `dist/`. GitHub Releases e GitLab Generic Packages sao
apenas mirrors derivados para instalacoes sem um checkout completo.

Repositores: [GitHub](https://github.com/x86dx2/x86qw) e
[GitLab](https://gitlab.com/x86dx2/x86qw). Site:
[x86qw.x86.com.br](https://x86qw.x86.com.br/).

## Instalar e jogar

Jogadores não precisam clonar este repositório. No macOS ou Linux:

```sh
/bin/bash -c "$(curl -fsSL https://x86qw.x86.com.br/install.sh)"
```

No Windows PowerShell:

```powershell
irm https://x86qw.x86.com.br/install.ps1 | iex
```

O bootstrap valida seu bundle por SHA-256, consulta somente o catálogo público
e pergunta onde instalar, oferecendo `~/Games/x86qw` apenas como sugestão. O
cliente do sistema atual é detectado automaticamente, sem perguntar o SO. Para
preparar outro cliente a partir de macOS ou Linux, use, por exemplo:

```sh
/bin/bash -c "$(curl -fsSL https://x86qw.x86.com.br/install.sh)" -- --platform windows
```

Os valores aceitos são `macos`, `linux` e `windows`. Ao
concluir, a CLI permanente fica na raiz escolhida e oferece `play`, `verify`,
`hub`, `update`, `upgrade`, `cleanup`, `uninstall` e `uninstall --purge`. Executar `x86qw`
sem argumentos mostra esse guia de uso; a ação principal é `./x86qw play`.

Depois da instalação, a CLI não oferece instalação arbitrária de clientes,
canais, mods ou presets. Esse papel pertence exclusivamente ao `install.sh` (ou
ao `install.ps1` no Windows). `x86qw update` atualiza a própria CLI e somente o
que já está instalado. `x86qw upgrade` também incorpora componentes novos que
passaram a integrar o perfil `essential`, `recommended`, `complete` ou `custom`
registrado naquela instalação. Ambos mostram o plano completo e exigem que o
jogador digite `yes` antes de alterar arquivos; `--yes` confirma o plano em
automações e `--dry-run` encerra depois de apresentá-lo.

Quem clonou o repositório está no fluxo de desenvolvimento e pode usar as
fontes canônicas locais:

```sh
./install-qw.py
./install-qw.py verify
./play-qw.py
```

Stable e nightly coexistem. No macOS, o instalador remove o entitlement de
sandbox do bundle com a ferramenta nativa `codesign`, limpa bookmarks antigos
e preserva recibos reversiveis. Os mods QuakeC usam nomes de gamecode exclusivos
e os parametros `gamedir` corretos; arquivos pessoais e configuracoes que o
cliente reescreve nao sao tratados como payload imutavel.
Os launchers isolam a colecao com `-nohome`, e `qw/pak.lst` fixa a prioridade
dos PK3 para que texturas e addons sejam carregados sempre na mesma ordem.

O manual completo esta em [installer/docs/installer.md](installer/docs/installer.md).

## Estrutura

```text
dist/                    produto final canonico e versionado
maintenance/            manutencao, inventarios, receitas, testes e builds
installer/               documentacao e testes das ferramentas da raiz
site/                    site inteiro: produto, design, deploy, assets e testes
docs/                    arquitetura global da plataforma
install-qw.py             motor interno de instalação, atualização e remoção
play-qw.py                seleciona e abre mods locais no ezQuake
ROADMAP.md                roteiro global do produto
```

Cada dominio guarda tudo que lhe pertence:

- `maintenance/inventory/` define componentes, dependencias, versoes, origens
  e a fronteira de arquivos aceitos;
- `maintenance/recipes/` registra artefatos stable byte a byte;
- `maintenance/tools/` contem apenas modulos internos usados pelo gerenciador;
- `maintenance/tests/`, `installer/tests/` e `site/tests/` testam seus proprios
  contextos;
- `maintenance/build/` recebe ZIPs derivados e e ignorado pelo Git;
- `site/wrangler.jsonc`, `site/PRODUCT.md`, `site/DESIGN.md` e `site/docs/`
  pertencem ao site, sem arquivos de site soltos na raiz;
- `quake-world/`, caches e `__pycache__` sao estado local ignorado, nunca fonte
  da distribuicao.

`dist/` tem somente material usado pelo produto:

```text
dist/
├── ezquake/              stable e nightly para os tres sistemas
├── nquake/               snapshot fixado e particionado pelo BOM
├── mods/                 KTX, Final Arena, Pro-X, Team Fortress, TD2 e perfis x86QW
├── id1/                  pak0.pak e pak1.pak registrados
├── installer/            bundle versionado usado pelo bootstrap público
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
./install-qw.py --help
./play-qw.py --help
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
- `id1` e tratado como material registrado, validado por SHA-256 e incorporado ao bundle público.
