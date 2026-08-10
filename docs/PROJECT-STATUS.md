## Baseline atual

O estado remoto de `main` inclui o hotfix 0.7.6 da PR #78 no commit
`a9680e9cc5d0eb728d3d84203d0966f0d1167592`. A raiz de trabalho
`/Users/x86/git-projects/x86qw` permanece separada e dirty; este status não
autoriza limpar nem sobrescrever mudanças locais não relacionadas.

## Versão pública

`0.7.6` é a release pública corrente. O bundle tem 577554 bytes e SHA-256
`ee3ce227fd1e6b604d56cf1c7559b57fe843fd5377fa592c075ba7210ed740c0`.
GitHub e GitLab servem esses mesmos bytes, e o domínio
`https://x86qw.x86.com.br` publica os bootstraps, o catálogo autenticado e a
metadata TUF v2. As releases 0.7.5, 0.7.4 e 0.7.3 permanecem históricas e
imutáveis. Nenhuma release `1.0.0` foi publicada.

O 0.7.6 corrige o tratamento do HTTP 404 consultado durante a busca pela
próxima versão de `root.json`. No 0.7.5, o downloader encapsulava esse status
antes de o cliente TUF reconhecê-lo como fim normal da rotação.

## Estado da jornada

- PRs A-G e o hotfix #78 estão mesclados.
- Trust de produção e a publicação operacional E2 estão concluídos para a
  linha 0.7: root Ed25519, thresholds, catálogo, targets, snapshot e timestamp
  foram publicados e verificados sem chaves privadas no Git, CI ou bundle.
- Um candidato local `1.0.0` foi preparado anteriormente em área isolada, mas
  ele antecede o hotfix TUF e continua sendo somente evidência histórica de
  preparação. Não é release, RC, promoção nem autorização de suporte.
- A evidência nativa M3 e o período de uso de um RC exato ainda são gates da
  promoção 1.0.

## Evidência atual

- PR #78: run `31383957653` verde no Quality Gate e nas seis combinações
  Ubuntu, macOS e Windows com Python 3.10 e 3.13.
- Gate local: 1.405 testes de manutenção, 37 skips explícitos e 5 testes do
  site aprovados; `git lfs fsck` e `git diff --check` também aprovados.
- GitHub e GitLab entregam o ZIP 0.7.6 com 577554 bytes e SHA-256
  `ee3ce227fd1e6b604d56cf1c7559b57fe843fd5377fa592c075ba7210ed740c0`.
- Cloudflare deploy `4533c45e-6999-44a5-b893-e0fbe2cab421`: bootstraps,
  catálogo `21fb1f6e8bbd7d4da4861bf2cefc798b95ffaaf06d604ca67d3cad37a74acc4b`
  e metadata TUF v2 conferidos byte a byte no domínio público.
- Um diretório temporário limpo completou instalação pública do ezQuake 3.6.9,
  `update --dry-run`, `update --yes` e `verify` com a CLI 0.7.6.
- A instalação usada para reproduzir o problema foi migrada de 0.7.4 para
  0.7.6; uma segunda atualização ficou idempotente e a verificação terminou
  sem problemas.

## Gates pendentes

| Gate | Estado atual |
|---|---|
| trust de produção 0.7 | concluído e verificado publicamente no 0.7.6 |
| E2 operacional | concluído; GitHub, GitLab e Cloudflare convergentes |
| evidência nativa M3 do candidato exato | pendente |
| `1.0.0-rc.1` | não criada/publicada; período de uso não iniciado |
| H / promoção `1.0.0` | não aberta; bloqueada por RC, uso e evidência M3 |

O ADR 0007 registra o waiver solo-maintainer e a aprovação do proprietário; ele
não é revisão independente e não substitui evidência M3, RC, período de uso ou
mirrors convergentes para os bytes exatos promovidos a 1.0.

## Próxima ação

Preparar um novo candidato e `1.0.0-rc.1` a partir do baseline que contém o
0.7.6, congelar seus hashes, executar novamente o lifecycle H no host M3 e
observar o período de uso. A promoção `1.0.0` só pode ser aberta depois que
essas evidências e os mirrors do RC convergirem.
