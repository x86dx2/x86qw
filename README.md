# x86QW

x86QW é uma distribuição moderna e reproduzível de QuakeWorld, mantida por
`x86dx2` e publicada em `x86qw.x86.com.br`.

Repositório principal: [GitHub](https://github.com/x86dx2/x86qw). Cópia de
contingência: [GitLab](https://gitlab.com/x86dx2/x86qw).

Este repositório é a fonte canônica da distribuição: tudo que o x86QW entrega
ao jogador fica em `dist/`, incluindo binários ezQuake, conteúdo nQuake, mods,
ajustes próprios, pacotes instaláveis e os PAKs registrados. O instalador usa
primeiro essa cópia local validada e consulta os mirrors somente quando o
artefato não acompanha o checkout.

No macOS, feche o ezQuake antes de instalar. O pacote oficial é preparado
localmente com assinatura ad-hoc sem o entitlement de sandbox que torna o
bookmark do diretório inválido entre aberturas; por isso `-basedir` acessa
diretamente `quake-world` e o usuário não precisa localizar os PAKs. Instalações
anteriores são reparadas e têm o recibo atualizado automaticamente ao usar
`play` ou `hub`. `./install-qw.py verify` confirma a integridade resultante.

Para abrir um mod local sem montar argumentos manualmente, use:

```sh
./install-qw.py play
```

O menu oferece apenas mods efetivamente instalados, valida o componente,
descobre os mapas presentes nos diretórios, PAKs e PK3s e permite escolher entre
KTX, Clan Arena, Pro-X, Team Fortress e Total Destruction 2. Para os quatro
mods QuakeC clássicos, a instalação dos componentes e `play` mantêm cópias
gerenciadas do gamecode com nomes exclusivos e pequenos `server.cfg`; isso
impede que o `qwprogs.qvm` do KTX seja carregado por engano. Esses arquivos
entram em `.install/play-support.*` e são
removidos normalmente por `uninstall`. Ao instalar ou atualizar o TD2, essa
mesma camada aplica o perfil x86QW de controles, HUD e servidor sem modificar o
pacote original. `td2/x86qw-td2-user.cfg` é carregado por último, fica fora do
inventário e nunca é sobrescrito. Configurações que o próprio cliente
reescreve, como `prox/configs/config.cfg`, são defaults preserváveis e não fazem
parte do inventário imutável do componente.

As fontes canônicas dessa camada estão na própria distribuição em
`dist/mods/td2/2.22/x86qw/`. O BOM `inventory/components.json` declara cada origem,
destino e modo de instalação; o instalador somente consome essa declaração.

## Princípios

- cada artefato é imutável e identificado por SHA-256;
- origem, versão e licença são registradas antes do espelhamento;
- o catálogo associa cada pacote ao seu caminho permanente dentro de `dist/`;
- GitHub Releases e GitLab Generic Packages são mirrors de entrega, não a fonte canônica;
- os binários grandes de `dist/` são versionados com Git LFS; R2 não é utilizado;
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
dist/                   distribuição QuakeWorld canônica e versionada
dist/ezquake/           clientes stable e nightly para os três sistemas
dist/nquake/            conteúdo nQuake efetivamente incorporado
dist/mods/              KTX, TD2 e customizações próprias
dist/packages/          pacotes seletivos consumidos pelo instalador
dist/id1/               PAKs registrados permanentes
recipes/                origens, checksums e estado da revisão por artefato
site/public/            site e catálogo publicados pelo Cloudflare Worker
tools/build_package.py  ingestão reproduzível em uma área temporária
tools/build_component_packages.py  gera os 18 pacotes reproduzíveis do mirror
tools/check_component_updates.py  compara versões fixadas com os upstreams
tools/publish_gitlab_packages.py  publica e verifica o segundo mirror
tools/add_package.py    registro atômico de artefatos revisados
tools/sync_distribution.py  atualiza somente os upstreams incorporados ao produto
tools/validate_components.py  valida catálogo e partição dos componentes
tools/validate_recipes.py
tools/validate_catalog.py
inventory/components.json  BOM, perfis e dependências de todos os componentes x86QW
inventory/component-releases.json  versão e estratégia de atualização por componente
tests/test_catalog.py
wrangler.jsonc          Worker estático em x86qw.x86.com.br
```

## Validar

```sh
python3 tools/validate_catalog.py
python3 tools/validate_recipes.py
python3 tools/validate_components.py
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

Os builds em `dist/` fazem parte da distribuição e são versionados. Arquivos
grandes usam Git LFS; depois de clonar, `git lfs pull` materializa seus corpos.
O envio para GitHub Releases ou GitLab Packages apenas replica os mesmos bytes
para instalação sem checkout completo.

Para gerar os pacotes dos componentes a partir do snapshot nQuake e dos
artefatos independentes validados:

```sh
python3 tools/build_component_packages.py --register
```

Os pacotes são gravados em `dist/packages/` e o catálogo registra também o
caminho local de cada um.

## Atualizar a distribuição

Para atualizar os upstreams incorporados à distribuição, execute:

```sh
python3 tools/sync_distribution.py
```

O comando atualiza diretamente `dist/` e baixa somente arquivos ligados a uma
ação real do instalador: binários ezQuake stable/nightly, o conteúdo nQuake e os
artefatos independentes KTX e TD2. Cada upstream recebe consumidor, pacote,
origem, tamanho e SHA-256 em `dist/manifest.json`; execuções posteriores
reaproveitam itens já confirmados.

A fronteira geral fica em `inventory/component-policy.json`; o BOM detalhado,
os perfis e as dependências ficam em `inventory/components.json`; versões,
upstreams e overlays ficam em `inventory/component-releases.json`. Um
componente novo sem consumidor, arquivos e destino declarados é recusado. Isso
impede que pesquisas, catálogos externos, fontes, dependências de build ou
coleções inteiras entrem na distribuição apenas porque estão disponíveis. Mapas e
LOCs externos ao nQuake só serão adicionados futuramente, um a um, quando forem
incorporados ao produto.

A distribuição é organizada pelo papel de cada conteúdo:

```text
dist/
├── ezquake/           binários stable e nightly
├── nquake/            conteúdo fixado da distribuição de referência
├── mods/              upstreams e ajustes dos mods incorporados
├── packages/          artefatos finais consumidos pelo instalador
├── id1/               PAKs registrados
└── manifest.json      proveniência e SHA-256 dos upstreams
```

Para aplicar a regra de incorporação sem acessar a rede:

```sh
python3 tools/sync_distribution.py --apply-policy
```

Essa política preserva somente ezQuake, os arquivos nQuake atribuídos pelo BOM
e os artefatos independentes KTX/TD2 declarados,
eliminando clientes futuros, overlays sobrescritos, GFX avulso, históricos Git,
código-fonte, dependências de compilação e índices sem consumidor.

Validação integral e offline:

```sh
python3 tools/sync_distribution.py --verify
```

`dist/` é permanente, versionado e constitui o produto. O resumo auditável da
captura atual fica em `inventory/upstream-current.json`.

`tools/check_component_updates.py` consulta somente upstreams explicitamente
associados a componentes. Coleções sem projeto versionado próprio acompanham o
commit atual de `nQuake/distfiles`; componentes com release oficial evoluem
independentemente. O primeiro é KTX 1.47, aplicado sobre os recursos curados do
nQuake sem modificar os outros 16 componentes.
