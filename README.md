# x86QW

x86QW é uma distribuição moderna e reproduzível de QuakeWorld, mantida por
`x86dx2` e publicada em `x86qw.x86.com.br`.

Repositório principal: [GitHub](https://github.com/x86dx2/x86qw). Cópia de
contingência: [GitLab](https://gitlab.com/x86dx2/x86qw).

Este repositório concentra o instalador multiplataforma, o catálogo público, as
regras de proveniência, as ferramentas de validação e o site do projeto. O
instalador e a página pública leem o mesmo catálogo canônico, sem uma camada de
sincronização intermediária.

No macOS, feche o ezQuake antes de instalar. Na primeira abertura, selecione a
própria pasta `quake-world` quando o aplicativo pedir o diretório do jogo; o
instalador limpa autorizações antigas e `./install-qw.py verify` confirma se a
configuração nQuake já foi carregada.

## Princípios

- cada artefato é imutável e identificado por SHA-256;
- origem, versão e licença são registradas antes do espelhamento;
- o instalador consulta `https://x86qw.x86.com.br/api/v1/catalog.json`;
- GitHub Releases e GitLab Generic Packages mantêm duas cópias verificadas; R2 não é utilizado;
- os PAKs registrados de `id1` ficam versionados em `dist/id1` e são validados
  por SHA-256 antes de cada cópia para uma instalação nova;
- o instalador continua multiplataforma e usa somente a biblioteca padrão do
  Python.

## Estrutura inicial

```text
docs/architecture.md    serviços, repositórios e fluxo de publicação
docs/components.md      matriz de versões e estratégia dos 18 componentes
docs/cloudflare.md      deploy, domínio e redirecionamento do portal
docs/provenance.md      política e inventário das fontes
docs/diagrams/          arquitetura interativa e fonte Archify
docs/installer.md       manual completo do instalador migrado
PRODUCT.md              propósito, público e princípios da marca
DESIGN.md               tokens e sistema visual do site
install-qw.py           instalador macOS, Linux e Windows
dist/id1/               PAKs registrados permanentes usados pela instalação
recipes/                origens, checksums e estado da revisão por artefato
site/public/            site e catálogo publicados pelo Cloudflare Worker
tools/build_package.py  ingestão reproduzível em uma área temporária
tools/build_nquake_packages.py  gera os 18 pacotes reproduzíveis do mirror
tools/check_component_updates.py  compara versões fixadas com os upstreams
tools/publish_gitlab_packages.py  publica e verifica o segundo mirror
tools/add_package.py    registro atômico de artefatos revisados
tools/snapshot_upstreams.py  captura somente os arquivos usados pelo produto
tools/validate_nquake_components.py  valida catálogo e partição dos componentes
tools/validate_recipes.py
tools/validate_catalog.py
inventory/nquake-components.json  BOM, perfis e dependências do conteúdo nQuake
inventory/nquake-releases.json  versão e estratégia de atualização por componente
tests/test_catalog.py
wrangler.jsonc          Worker estático em x86qw.x86.com.br
```

## Validar

```sh
python3 tools/validate_catalog.py
python3 tools/validate_recipes.py
python3 tools/validate_nquake_components.py
python3 tools/check_component_updates.py
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
das licenças ficam em `site/public/legal/fonts`. O catálogo publica os 18
pacotes de componentes, os três binários ezQuake 3.6.9 e os três binários da nightly
fixada atualmente.

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

Os builds gerados em `dist/` são locais e ignorados pelo Git. A única exceção é
`dist/id1/pak0.pak` e `dist/id1/pak1.pak`, fontes permanentes usadas diretamente
pelo instalador. O envio dos demais artefatos para um GitHub Release ou outro
mirror ocorre antes do registro público. Somente então use `--register`; a ação
é explícita para impedir que um build local altere o catálogo por acidente.

Para gerar os pacotes nQuake diretamente do snapshot e dos overlays de release
validados:

```sh
python3 tools/build_nquake_packages.py
```

Depois de publicar os mesmos arquivos no release indicado pelo manifesto, use
`--register` para registrar seus hashes no catálogo público.

## Acervo upstream local

Para preservar o estado atual das fontes sem publicar os arquivos, execute:

```sh
python3 tools/snapshot_upstreams.py
```

O comando cria `archive/` e baixa somente arquivos ligados a uma ação real do
instalador: binários ezQuake stable/nightly e os arquivos atribuídos a um dos
componentes nQuake. Cada arquivo recebe consumidor, pacote,
origem, tamanho e SHA-256 em `archive/manifest.json`; execuções posteriores
reaproveitam itens já confirmados.

A fronteira geral fica em `inventory/component-policy.json`; o BOM detalhado,
os perfis e as dependências ficam em `inventory/nquake-components.json`; versões,
upstreams e overlays ficam em `inventory/nquake-releases.json`. Um
componente novo sem consumidor, arquivos e destino declarados é recusado. Isso
impede que pesquisas, catálogos externos, fontes, dependências de build ou
coleções inteiras entrem no acervo apenas porque estão disponíveis. Mapas e
LOCs externos ao nQuake só serão adicionados futuramente, um a um, quando forem
incorporados ao produto.

O acervo é organizado primeiro pelo contexto do conteúdo:

```text
archive/
├── components/
│   ├── ezquake/       binários de releases e nightlies
│   └── nquake/        snapshot fixado e releases externas realmente consumidas
└── manifest.json      inventário com consumidor, origem, tamanho e SHA-256
```

Para aplicar a regra ao acervo anterior sem acessar a rede:

```sh
python3 tools/snapshot_upstreams.py --apply-policy
```

Essa migração preserva somente ezQuake e os arquivos nQuake atribuídos pelo BOM,
eliminando clientes futuros, overlays sobrescritos, GFX avulso, históricos Git,
código-fonte, dependências de compilação e índices sem consumidor.

Validação integral e offline:

```sh
python3 tools/snapshot_upstreams.py --verify
```

`archive/` é permanente, mas local e ignorado pelo Git devido ao tamanho. O
resumo auditável da captura atual fica em `inventory/upstream-current.json`.

`tools/check_component_updates.py` consulta somente upstreams explicitamente
associados a componentes. Coleções sem projeto versionado próprio acompanham o
commit atual de `nQuake/distfiles`; componentes com release oficial evoluem
independentemente. O primeiro é KTX 1.47, aplicado sobre os recursos curados do
nQuake sem modificar os outros 16 componentes.
