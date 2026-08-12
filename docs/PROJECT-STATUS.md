# Estado atual do projeto

## Baseline real

A `main` canônica observada neste checkpoint é
`origin/main@99fcad83b8bacd951e2eeb56842f854e14ec10a8`. O avanço mais recente
está integrado no PR
[#100](https://github.com/x86dx2/x86qw/pull/100). A identidade do candidato
oficial — commit, SHA do `candidate.json` e digest do artifact — é sempre a
registrada pelo próprio workflow e pelo checkpoint do PR, nunca por uma cópia
manual neste documento.

## Versão pública

O último release público confirmado no GitHub é `0.7.13`: instalador de
581883 bytes, SHA-256
`114604400e1fd18c4180624314d4bc8ca9b6d4559ed26cfe8d0a767287f2aa32`.
A versão-fonte de `dist/installer/VERSION` acompanha essa geração pública.
O candidato local `1.0.0-rc.1` é uma geração separada e não promove a versão
pública por si só.

## Estado de confiança

A root Ed25519 incorporada é validada localmente. Em 2026-08-12, a leitura
pública encontrou a root v1, timestamp v12, snapshot v12 e targets v12; o
timestamp público e o arquivo versionado no repositório têm o mesmo hash e
expiram em `2026-08-13T05:20:06Z`. Isso confirma a convergência observada nesse
instante, mas não constitui evidência de custódia humana independente nem
substitui a cerimônia TUF do candidato.

## Estado local

- downloader, archives, SemVer, launchers, `changes` e `migrate` compartilham
  contratos de runtime;
- fixtures de migração cobrem os instaladores públicos `0.7.0`–`0.7.13`;
- o publisher é build-once e falha fechado para bytes ausentes, mirrors
  divergentes e metadata TUF fora de ordem;
- o candidato carrega o site renderizado e os binários de `dist`, sem depender
  de uma instalação pessoal em `quake-world/`;
- o harness Mac M3 executa plano candidato-owned e registra handoff, smoke
  normalizado e agregado unsigned pendente;
- o catálogo separa `supported`, `conditional` e `preview`: stable macOS
  permanece condicional, nightly e Linux/Windows/macOS Intel permanecem preview
  quando não há evidência nativa do candidato exato;
- a instalação pessoal temporária não é usada pelos testes de release.

## Candidato oficial

O workflow `Immutable candidate rehearsal` produziu uma única vez o candidato
do commit `99fcad83b8bacd951e2eeb56842f854e14ec10a8` no run
`31600754928`, com validação portátil concluída. O workflow oficial
`native-m3.yml` foi despachado para o mesmo candidato no run `31604145989`, mas
permanece `queued` porque não há runner self-hosted M3 disponível. A evidência
permanece `signed=false`/`promotable=false` até a execução nativa e a cerimônia
externa; nenhum artifact de uma instalação pessoal é usado.

## Bloqueios atuais

1. a execução M3 exata está na fila sem runner self-hosted Apple M3 e, depois
   dela, ainda precisa da cerimônia externa de assinatura; o agregado pending
   não é promotable;
2. o ambiente GitHub `release` não possui os secrets `M3_TRUST_ROOT_B64` e
   `CLOUDFLARE_API_TOKEN` (o único nome de secret visível no ambiente é
   `GITLAB_TOKEN`);
3. a configuração de reviewer aponta para o próprio `x86dx2`; o waiver do
   maintainer único registra a ausência de revisão independente, não a simula;
4. o candidato exato ainda não foi publicado no GitHub/GitLab; depois da
   assinatura, assets imutáveis, mirrors convergentes e metadata-last precisam
   passar pelo workflow protegido;
5. Linux, Windows e macOS Intel continuam preview; nenhum resultado portátil é
   apresentado como smoke nativo dessas plataformas;
6. o RC ainda precisa do período de uso definido antes da promoção final.

## Veredito

A implementação local pode avançar para um candidato `1.0.0-rc.1`, mas não há
autorização técnica para declarar RC publicado ou promover `1.0.0`. O release
público `0.7.13` não foi alterado por este checkpoint; TUF v12 somente reparou
a lease dessa versão pública.

## Próxima ação

Disponibilizar o runner self-hosted Apple M3 e os secrets operacionais, então
deixar o run `31604145989` concluir contra o artifact fixado, obter a assinatura
externa e iniciar o fluxo protegido de promoção somente depois que o handoff
TUF assinado estiver disponível.
