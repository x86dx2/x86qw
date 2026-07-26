# x86QW

x86QW é uma distribuição moderna e reproduzível de QuakeWorld, mantida por
`x86dx2` e publicada em `x86qw.x86.com.br`.

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
site/public/            site e catálogo publicados pelo Cloudflare Worker
tools/add_package.py    registro atômico de artefatos revisados
tools/validate_catalog.py
tests/test_catalog.py
wrangler.jsonc          Worker estático em x86qw.x86.com.br
```

## Validar

```sh
python3 tools/validate_catalog.py
python3 -m unittest discover -s tests -v
./install-qw.py --help
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
