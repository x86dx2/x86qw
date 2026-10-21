# Arquitetura

O mapa interativo da plataforma está em
[`diagrams/x86qw-platform.html`](diagrams/x86qw-platform.html). Sua fonte
versionada fica ao lado, em `x86qw-platform.architecture.json`.

## Contratos públicos

```text
https://qw.x86.com.br/                          site do projeto
https://qw.x86.com.br/install.sh                bootstrap macOS/Linux
https://qw.x86.com.br/install.ps1               bootstrap Windows
https://qw.x86.com.br/api/v1/catalog.json       catálogo de pacotes
https://qw.x86.com.br/api/v1/product.json       fatos públicos do produto
https://qw.x86.com.br/api/v1/trust/             repositório TUF (metadata/ e targets/)
https://github.com/x86dx2/x86qw/releases        CLI (x86qw-cli-*) e pacotes de conteúdo
https://gitlab.com/x86dx2/x86qw/-/packages      mirror de contingência dos pacotes
```

## Código

Todo o código vive em `go/`, num único módulo:

- `cmd/x86qw`: a CLI e o app. Cada comando registra grupo, sinopse e opções;
  `internal/cliargs` é o único parser.
- `internal/setup` e `internal/setup/tui`: o instalador e o app (Bubble Tea).
  O app fala com um `setup.Backend`; `setup.Real` usa a instalação de verdade e
  `setup.Demo` serve à demonstração e aos testes.
- `internal/install`, `internal/plan`, `internal/maint`: instalação,
  planos de update/upgrade/repair, desinstalação, limpeza, perfil, biblioteca
  e migração.
- `internal/play`, `internal/services`, `internal/hub`: partidas locais e
  links, MVDSV/QTV/QWFWD com journal e lock, e o QuakeWorld Hub.
- `internal/trust`, `internal/download`, `internal/archive`,
  `internal/atomicfile`: catálogo autenticado por TUF, downloads limitados,
  extração segura e escrita atômica.
- `cmd/x86qw-content` e `internal/content`: verificação, atualização, build e
  publicação do `dist/`.
- `cmd/x86qw-release` e `internal/tufsign`: release da CLI, assinatura TUF,
  montagem do site e monitor da cadeia pública.

## A instalação

A CLI publicada é um binário estático por plataforma. O bootstrap baixa o da
release `x86qw-cli-*` fixado por tamanho e SHA-256 e executa `x86qw setup`.
Numa instalação:

```text
x86qw.sh / x86qw.cmd     lançador que executa a CLI instalada
.x86qw/cli/x86qw         a CLI e seu recibo (receipt)
.x86qw/state.json        estado: perfil, componentes e versões
.x86qw/components/       recibo e inventário de cada componente
.x86qw/clients/          recibos do ezQuake por plataforma e canal
.x86qw/setup/            escolhas do instalador e senha de rcon
.x86qw/sessions/         locks e journals das sessões
ezQuake Stable.app,      cliente stable e nightly (app no macOS, AppImage no
ezquake-*.AppImage/.exe  Linux, executável no Windows)
ezquake/configs/         configurações do cliente
id1/                     PAKs registrados
qw/ arena/ prox/         conteúdo dos jogos (KTX, Final Arena, Pro-X,
fortress/ td2/           Team Fortress e TD2)
mvdsv  qtv/  qwfwd/      servidor e serviços, quando a instalação hospeda
docs/licenses/           licenças dos componentes
```

`.x86qw/` é o plano de controle. Os pacotes carregam variantes por plataforma
numa área de staging; a CLI seleciona exatamente uma antes de copiar. Depois de
qualquer alteração de componentes, ela gera `qw/pak.lst` em ordem
determinística, registra o arquivo em recibo próprio e rejeita divergências na
verificação. O ezQuake sempre recebe `-nohome`, evitando que um `~/.ezquake`
externo sobreponha a instalação.

Uma CLI mais nova que abre o app de uma instalação passa a ser a CLI dela; uma
versão mais antiga ou de desenvolvimento nunca substitui a instalada.

## Repositório

- `x86dx2/x86qw` guarda a distribuição completa em `dist/`, os inventários, o
  catálogo, o código, o site e a documentação. Arquivos grandes ficam no Git
  LFS. Os pacotes de componentes são builds reproduzíveis das fontes canônicas
  e não são versionados de novo na árvore.
- Os dois PAKs registrados ficam em `dist/game-data/id1`; o builder produz o
  pacote de dados-base `x86qw-core-id1`, com tamanho e SHA-256 no catálogo.
- Customizações próprias fazem parte da distribuição: para o TD2, ficam em
  `dist/mods/td2/2.22/x86qw/` e são declaradas como `project_sources` no BOM; o
  bootstrap de conteúdo segue o mesmo contrato em `dist/mods/x86qw/core/`.

O catálogo canônico é `site/public/api/v1/catalog.json`, exatamente o arquivo
servido. Capacidades, runtimes, jogos e compatibilidade são declarados em
`maintenance/inventory/`; a projeção pública `site/public/api/v1/product.json`
combina esses contratos com o catálogo, e `x86qw-content verify` rejeita uma
projeção divergente. A lista de comandos de `capabilities.json` é a mesma de
`x86qw help`, e um teste compara as duas.

`x86qw-content publish` envia os pacotes de conteúdo ao GitHub Releases e ao
GitLab Generic Packages, baixa cada cópia pública, confere tamanho e SHA-256 e
só então registra a segunda URL. Pacotes de conteúdo nunca recebem o selo
**Latest**; a release da CLI é publicada como prerelease e vira Latest quando o
site passa a servi-la.

## Regra de entrada de componentes

`maintenance/inventory/component-policy.json` é a fronteira geral da
distribuição. `maintenance/inventory/components.json` decompõe o conteúdo em
BOM, perfis, dependências, origens e destinos. O coletor rejeita caminhos fora
dessas declarações e a validação rejeita arquivos sem consumidor ou componente.
Pesquisar ou avaliar um recurso não o torna parte do x86QW.

`maintenance/inventory/component-releases.json` mantém a camada de atualização
separada: versão atual, estratégia, upstream, artefatos consumidos e hashes.
Assim uma release como KTX pode avançar sem renomear ou reconstruir componentes
alheios.

Todo conteúdo canônico fica sob `dist/`: `clients/ezquake/stable/` contém as
releases oficiais, `clients/ezquake/nightly/` contém os snapshots de
desenvolvimento, `distributions/nquake/` contém somente o conteúdo incorporado
da distribuição de referência, `mods/` mantém upstreams e ajustes próprios e
`game-data/id1/` mantém os PAKs registrados. ZIPs derivados ficam
temporariamente em `maintenance/build/packages/`, fora do Git.

Quando um componente for proposto, a ordem é: implementar a ação consumidora,
declarar o componente na política, adicionar testes e só então habilitar seu
download. Remover a ação consumidora exige remover também seus arquivos do
acervo.

Atualizações são detectadas por `x86qw-content check`. `update` aplica apenas
atualizações mecanicamente seguras; releases independentes exigem uma definição
revisada em `add`, e `publish` continua explícito. Cada mod escolhe uma
composição verificável: merge de arquivos para KTX, snapshot integral para
Final Arena, substituição completa para Pro-X, assets de referência com
gamecode atualizado para Team Fortress e pacote independente para TD2. As
harmonizações x86QW são sempre aplicadas por último. No KTX, toda divergência
compartilhada precisa constar, com hashes e resolução, em
`dist/mods/ktx/1.47/x86qw/policy/merge-policy.json`; em Team Fortress, o
builder retira do PAK montado o gamecode 2.8 e a mídia estritamente duplicada
antes de adicionar o `qwprogs.dat` 2.9 extraído do ZIP original. Uma mudança
não declarada interrompe o build.

## Fluxo de publicação

```text
fonte fixada -> dist/ -> Git LFS -> catálogo -> x86qw-release sign -> TUF
                     -> maintenance/build/packages/ -> GitHub + GitLab
go/cmd/x86qw -> go-release.yml -> x86qw-cli-X.Y.Z -> x86qw-release prepare -> bootstraps + catálogo
merge em main -> site-deploy.yml -> qw.x86.com.br

metadado HTTPS -> limite + deadline -> memória
pacote HTTPS   -> tamanho + SHA-256 -> staging privado -> promoção atômica
```

O passo a passo de release, renovação TUF e recuperação está no
[runbook de release](runbooks/release.md).

## Fronteira de bytes remotos

`internal/download` é a única entrada de bytes HTTP persistentes, na CLI e nas
ferramentas. Artefatos persistentes são fixados por tamanho e SHA-256;
metadados dinâmicos usam leitura limitada e nunca viram conteúdo gerenciado.
Stable, nightly e nQuake descobertos só herdam uma identidade quando caminho,
URL e tamanho coincidem com o manifesto revisado; candidatos novos exigem
definição revisada antes do download persistente. Pacotes ezQuake também
precisam coincidir exatamente com
`clients/ezquake/<canal>/<versão>/<plataforma>-<arquitetura>/<arquivo>`, e os
caminhos que vêm do upstream são validados como relativos e portáveis antes de
qualquer pasta ser criada.

Os limites são 512 MiB para artefatos, 2 MiB para o catálogo e 1 MiB para o
Hub. Toda URL inicial, redirecionada e final usa HTTPS; um deadline cobre
tentativas, leituras e backoff, e o retry fica restrito a falhas transitórias.
Temporários recebem modo `0600` no POSIX e são sincronizados antes de uma
substituição atômica, preservando o destino anterior até a promoção. URLs
persistentes em catálogo, manifesto e inventários passam pelo mesmo validador
HTTPS e não aceitam credenciais, query, fragmento, espaços ou controles.

## Confiança

O catálogo é autenticado por TUF com chaves Ed25519. A root, embutida na CLI e
versionada em `maintenance/trust/root.json`, define quatro papéis: root e
targets com threshold 2 de 3, snapshot e timestamp com threshold 1 de 2. A CLI
guarda os metadados confiáveis no cache local e rejeita rollback, metadados
vencidos e um timestamp que reutilize uma versão com outros bytes. Os detalhes
estão no [threat model](THREAT-MODEL.md) e no
[runbook de chaves](runbooks/key-management.md).

## Segurança e recuperação

- nenhum artefato persistente é aceito sem tamanho e SHA-256 prévios;
- cada resposta possui limite explícito e deadline total;
- bytes só chegam ao destino final após validação e promoção atômica;
- versões publicadas não são substituídas, apenas deixam de ser a atual no
  catálogo;
- senhas de serviço vêm de prompt sem eco ou arquivo privado e nunca entram na
  linha de comando dos filhos;
- no Windows, objetos privados usam DACL protegida com acesso somente ao
  usuário atual e a `LOCAL SYSTEM`;
- o pré-voo de portas ocorre antes do primeiro processo; readiness e rollback
  encerram startups parciais;
- `.x86qw/sessions/` registra journals privados e, após crash, remove somente
  arquivos criados pela sessão cujo hash ainda coincide.
