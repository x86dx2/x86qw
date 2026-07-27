# x86QW

x86QW é uma distribuição moderna e reproduzível de QuakeWorld, mantida por
`x86dx2` e publicada em `x86qw.x86.com.br`.

Repositório principal: [GitHub](https://github.com/x86dx2/x86qw). Cópia de
contingência: [GitLab](https://gitlab.com/x86dx2/x86qw).

Este repositório concentra o instalador multiplataforma, o catálogo público, as
regras de proveniência, as ferramentas de validação e o site do projeto. O
instalador e a página pública leem o mesmo catálogo canônico, sem uma camada de
sincronização intermediária.

## Princípios

- cada artefato é imutável e identificado por SHA-256;
- origem, versão e licença são registradas antes do espelhamento;
- o instalador consulta `https://x86qw.x86.com.br/api/v1/catalog.json`;
- GitHub, GitLab e futuramente R2 são mirrors, não contratos do instalador;
- os PAKs comerciais de `id1` nunca entram no repositório ou no mirror;
- o instalador continua multiplataforma e usa somente a biblioteca padrão do
  Python.

## Estrutura inicial

```text
docs/architecture.md    serviços, repositórios e fluxo de publicação
docs/cloudflare.md      deploy, domínio e redirecionamento do portal
docs/provenance.md      política e inventário das fontes
docs/diagrams/          arquitetura interativa e fonte Archify
docs/installer.md       manual completo do instalador migrado
PRODUCT.md              propósito, público e princípios da marca
DESIGN.md               tokens e sistema visual do site
install-qw.py           instalador macOS, Linux e Windows
recipes/                origens, checksums e estado da revisão por artefato
site/public/            site e catálogo publicados pelo Cloudflare Worker
tools/build_package.py  ingestão reproduzível em uma área temporária
tools/add_package.py    registro atômico de artefatos revisados
tools/snapshot_upstreams.py  captura somente os arquivos usados pelo produto
tools/validate_recipes.py
tools/validate_catalog.py
tests/test_catalog.py
wrangler.jsonc          Worker estático em x86qw.x86.com.br
```

## Validar

```sh
python3 tools/validate_catalog.py
python3 tools/validate_recipes.py
python3 -m unittest discover -s tests -v
./install-qw.py --help
python3 tools/build_package.py --help
python3 tools/add_package.py --help
npx --yes wrangler@4.114.0 deploy --dry-run
```

Para abrir o site localmente com o mesmo runtime de produção:

```sh
npx --yes wrangler@4.114.0 dev --ip 127.0.0.1 --port 8787
```

As fontes do site são servidas localmente sob SIL Open Font License; os textos
das licenças ficam em `site/public/legal/fonts`. Nenhum binário do jogo ou
conteúdo de terceiros foi publicado no catálogo nesta fase.

## Montar um pacote do mirror

Cada arquivo em `recipes/` fixa origem, tamanho, SHA-256, conteúdo mínimo
esperado, fonte correspondente e estado da revisão. Uma receita `blocked` é
documentação auditável, mas não pode gerar nem publicar um pacote.

Depois que a revisão mudar explicitamente para `ready`, a ingestão baixa em um
diretório temporário, valida o arquivo e grava uma cópia byte a byte em `dist/`:

```sh
python3 tools/build_package.py recipes/ezquake/3.6.9/macos-universal.json
```

Para validar um download já obtido sem acessar a rede:

```sh
python3 tools/build_package.py recipes/ezquake/3.6.9/macos-universal.json \
  --artifact /caminho/ezQuake-macOS-universal.zip
```

`dist/` é local e ignorado pelo Git. O envio para um GitHub Release ou outro
mirror ocorre antes do registro público. Somente então use `--register`; a ação
é explícita para impedir que um build local altere o catálogo por acidente.

## Acervo upstream local

Para preservar o estado atual das fontes sem publicar os arquivos, execute:

```sh
python3 tools/snapshot_upstreams.py
```

O comando cria `archive/` e baixa somente arquivos ligados a uma ação real do
instalador: binários estáveis e nightly, clientes opcionais, os caminhos do
nQuake efetivamente sobrepostos, mapas e LOCs. Cada arquivo recebe consumidor,
origem, tamanho e SHA-256 em `archive/manifest.json`; execuções posteriores
reaproveitam itens já confirmados.

A lista canônica fica em `inventory/component-policy.json`. Um componente novo
sem consumidor e prefixo de acervo declarados é recusado pelos validadores. Isso
impede que pesquisas, catálogos externos, fontes, dependências de build ou
coleções inteiras entrem no acervo apenas porque estão disponíveis.

O acervo é organizado primeiro pelo contexto do conteúdo:

```text
archive/
├── components/
│   ├── ezquake/       binários de releases e nightlies
│   ├── classicq/      binários das releases
│   ├── unezquake/     binários das releases
│   └── nquake/        snapshot dos caminhos usados, fixado por commit
├── content/
│   ├── maps/          arquivos instaláveis da coleção all
│   └── locs/          nomes de regiões instaláveis
└── manifest.json      inventário com consumidor, origem, tamanho e SHA-256
```

Para aplicar a regra ao acervo anterior sem acessar a rede:

```sh
python3 tools/snapshot_upstreams.py --apply-policy
```

Essa migração extrai do antigo mirror nQuake somente os arquivos indicados pela
política e depois elimina GFX, históricos Git, código-fonte, dependências de
compilação, checksums auxiliares e páginas de índice sem consumidor.

Validação integral e offline:

```sh
python3 tools/snapshot_upstreams.py --verify
```

`archive/` é permanente, mas local e ignorado pelo Git devido ao tamanho. O
resumo auditável da captura atual fica em `inventory/upstream-current.json`.
