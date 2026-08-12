# Estado atual do projeto

## Baseline real

A `main` pública observada neste checkpoint é
`origin/main@d4a92c0fe29786fdc6ec5c7d978813cb634be62c`. A implementação em
execução fica na branch `codex/stabilize-1.0`, no commit
`4256fc4ea8a75f4ca9b717088d88a2e67a4ab32f`; o PR aberto é o
[#95](https://github.com/x86dx2/x86qw/pull/95). A identidade do candidato
oficial é sempre a do artifact imutável produzido pelo workflow.

## Versão pública

O último release público confirmado no GitHub é `0.7.13`: instalador de
581883 bytes, SHA-256
`114604400e1fd18c4180624314d4bc8ca9b6d4559ed26cfe8d0a767287f2aa32`.
A versão-fonte de `dist/installer/VERSION` acompanha essa geração pública.
O candidato local `1.0.0-rc.1` é uma geração separada e não promove a versão
pública por si só.

## Estado de confiança

A root Ed25519 incorporada é validada localmente. A lease pública foi renovada
para TUF v12 e implantada no Cloudflare (deployment
`2625d035-6db0-4754-a02e-df054b5ee7ec`); o timestamp v12 expira em
`2026-08-13T05:20:06Z`. A validação local autentica a cadeia e a implantação foi
reportada pelo Wrangler, mas o HTTPS direto deste host para o edge expirou; a
convergência pública independente ainda precisa ser confirmada por um caminho
externo. Isso não constitui evidência de custódia humana independente nem
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

O rehearsal oficial `31570858468` terminou com 20/20 jobs verdes. O artifact
imutável é `9131490688`, com 648110874 bytes e digest
`sha256:a206bc54610a9641999669650f89c55da5162be0f6a80e9ac2bf651959931aa2`.
Seu `candidate.json` tem SHA-256
`54708415bae4384c644db3dedc8fbcc9f7b49b06a543cbed0f7eacb1b4e0a763`, versão
`1.0.0-rc.1`, commit `4256fc4ea8a75f4ca9b717088d88a2e67a4ab32f` e 73 artefatos.
Ubuntu, Windows e macOS verificaram esse artifact sem rebuild.

O smoke M3 local executou os 18 casos contra os mesmos bytes e passou em Apple
M3 Pro/arm64; a
evidência redigida está vinculada ao candidato, mas permanece
`signed=false`/`promotable=false`. O workflow oficial `native-m3.yml` ainda não
pôde ser dispatchado porque está somente nesta branch; após a integração do PR,
ele deve ser executado contra o artifact `9131490688`.

## Bloqueios atuais

1. a evidência M3 exata precisa da cerimônia externa de assinatura; o agregado
   local pending não é promotable;
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

Integrar o PR, executar o workflow M3 oficial contra o artifact fixado, obter a
assinatura externa e iniciar o fluxo protegido de promoção somente depois que
os secrets operacionais e o handoff TUF assinado estiverem disponíveis.
