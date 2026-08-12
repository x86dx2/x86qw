# Estado atual do projeto

## Baseline real

A linha canônica é `origin/main`. A revisão exata de um snapshot deve ser
obtida com `git rev-parse origin/main` no momento da auditoria; este documento
não repete um SHA da própria linha que o contém, porque o merge de qualquer
atualização documental mudaria esse valor. A identidade do candidato oficial —
commit, SHA do `candidate.json` e digest do artifact — é sempre a registrada
pelo próprio workflow e pelo checkpoint do PR, nunca por uma cópia manual neste
documento.

## Versão pública

O último release público confirmado no GitHub é `0.7.13`: instalador de
581883 bytes, SHA-256
`114604400e1fd18c4180624314d4bc8ca9b6d4559ed26cfe8d0a767287f2aa32`.
A versão-fonte de `dist/installer/VERSION` acompanha essa geração pública.
O candidato local `1.0.0-rc.1` é uma geração separada e não promove a versão
pública por si só.

## Estado de confiança

A root Ed25519 incorporada é validada localmente. A fotografia anterior ao
deploy encontrou a root v1, timestamp v12, snapshot v12 e targets v12; o
timestamp público e o arquivo versionado no checkout tinham o mesmo hash e
expiravam em `2026-08-13T05:20:06Z`. A renovação v13 foi publicada no site em
`2026-08-12T18:43:34Z`, com timestamp expirando em `2026-08-13T18:43:34Z`;
catálogo, product e cadeia TUF foram comparados pelos bytes públicos e
autenticados pela root incorporada. Isso confirma a convergência observada
nesse instante, mas não constitui evidência de custódia humana independente
nem substitui a cerimônia TUF do candidato.

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

O PR #107 contém o candidato do commit
`0d667618626f30b1a5b5bacdf1cacc2785d47787`. O run `31626186660` concluiu os
sete checks requeridos com sucesso; o PR continua aberto e draft, portanto isso
não é merge nem release. A rehearsal `31628359644` foi disparada separadamente
com `1.0.0-rc.1` e não publica nada. O workflow oficial `native-m3.yml` ainda
depende de um runner self-hosted M3 disponível; a evidência permanece
`signed=false`/`promotable=false` até a execução nativa e a cerimônia externa.
Nenhum artifact de uma instalação pessoal é usado.

## Bloqueios atuais

1. a execução M3 exata não pode concluir sem runner self-hosted Apple M3 e,
   depois dela, ainda precisa da cerimônia externa de assinatura; o agregado
   pending não é promotable;
2. o ambiente GitHub `release` não possui os secrets `M3_TRUST_ROOT_B64` e
   `CLOUDFLARE_API_TOKEN` (a leitura dos nomes expõe somente `GITLAB_TOKEN`);
3. a configuração de reviewer aponta para o próprio `x86dx2`; o waiver do
   maintainer único registra a ausência de revisão independente, não a simula;
4. a operação TUF sustentável ainda depende de signer agendado, custódia
   independente e drill de recuperação; a lease pública v13 está saudável até
   `2026-08-13T18:43:34Z`, com monitor corrigido para alertar dentro de 6 horas;
5. o candidato exato ainda não foi publicado no GitHub/GitLab; depois da
   assinatura, assets imutáveis, mirrors convergentes e metadata-last precisam
   passar pelo workflow protegido;
6. Linux, Windows e macOS Intel continuam preview; nenhum resultado portátil é
   apresentado como smoke nativo dessas plataformas;
7. o RC ainda precisa do período de uso definido antes da promoção final.

## Veredito

A implementação local pode avançar para um candidato `1.0.0-rc.1`, mas não há
autorização técnica para declarar RC publicado ou promover `1.0.0`. O release
público `0.7.13` não foi alterado por este checkpoint; a renovação TUF v13 foi
publicada e validada nos endpoints públicos.

## Próxima ação

Disponibilizar o runner self-hosted Apple M3 e os secrets operacionais, manter
a operação TUF sustentável antes da próxima janela de 6 horas, e só então
concluir a evidência nativa, obter a assinatura externa e iniciar o fluxo
protegido de promoção contra o artifact fixado.
