# Estado atual do projeto

## Baseline real

A linha canônica é `origin/main`. A revisão exata de um snapshot deve ser
obtida com `git rev-parse origin/main` no momento da auditoria; este documento
não repete um SHA da própria linha que o contém, porque o merge de qualquer
atualização documental mudaria esse valor. A identidade do candidato oficial —
commit, SHA do `candidate.json` e digest do artifact — é sempre a registrada
pelo próprio workflow e pelo checkpoint do PR, nunca por uma cópia manual neste
documento.

## Versões públicas

A versão estável-fonte continua em `dist/installer/VERSION = 0.7.13` e o
último release estável continua sendo `x86qw-installer-0.7.13`: instalador de
581883 bytes, SHA-256
`114604400e1fd18c4180624314d4bc8ca9b6d4559ed26cfe8d0a767287f2aa32`.

O Release Candidate público é `x86qw-installer-1.0.0-rc.1`, uma prerelease
deliberadamente separada da versão-fonte estável. Ele aponta para o commit de
produto `a8758ee27bebd7c72c24a31dc19335652e260c0a` e foi promovido pelo run
`31752738047`, a partir da linha canônica `main@335d9a062f8ce33b226a9892de82979828a0fd1b`.

Identidade pública do RC:

- instalador: 600431 bytes,
  SHA-256 `9600be7eb2ed14e23b2eeb079bd6aa0e4611f996be0c89741fda12587eb7fed8`;
- `candidate.json`: 14474 bytes,
  SHA-256 `1552a896a0076dd2e347ed5b732b6dd31ba892292e1f9fb8c97fe9111f755bcb`;
- release GitHub: [x86qw-installer-1.0.0-rc.1](https://github.com/x86dx2/x86qw/releases/tag/x86qw-installer-1.0.0-rc.1).

O RC é público e não é GitHub Latest. A imutabilidade host-level da release
GitHub ainda aparece como indisponível (`immutable=false`); o publisher mantém
imutabilidade lógica recusando overwrite, divergência de digest e assets extras.

## Estado de confiança

A root Ed25519 incorporada é validada localmente. O monitor público encontrou
root v1, timestamp v16, snapshot v16 e targets v16; autenticou o catálogo com
75 pacotes e timestamp válido até `2026-08-14T15:42:54Z`. O recibo está em
[`docs/releases/1.0.0-rc.1-tuf-monitor-2026-08-14.json`](releases/1.0.0-rc.1-tuf-monitor-2026-08-14.json).
Esta é uma fotografia do endpoint público em 2026-08-14 05:04 UTC; não
constitui evidência de custódia humana independente nem substitui a cerimônia
TUF do candidato.

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
- o checkpoint local histórico `1.0.0-rc.4`, construído no commit
  `76a73f9b50919010fc13730514d2c73ceced2fde`, passou o harness Apple M3 Pro
  com `25/25` casos; seu `candidate.json` tem SHA-256
  `487d844aa4de66667bf26a375011b1c8b1c5baede5450cca9fee3bfd0c329d2e` e o
  instalador local tem 600931 bytes, SHA-256
  `94de20fa7f1efe61b1d5f93aeee936362e3ea8f2abcb1c83c62658d81cbd0b03`;
- o preflight privado mais recente de `1.0.0`, construído na HEAD
  `f2cadeff3261ce07f7c9490313db1aa69e417fa2`, passou os mesmos `25/25` casos
  no Apple M3 Pro. Seu `candidate.json` tem SHA-256
  `c7357159df806b29d8c9eb715152ec6186c5d9edefd3bb5587dbf6c98a0a94c7` e o
  instalador tem 600825 bytes, SHA-256
  `d3274e6aa2f1e3078ac5000ffae8b97c9efd329f3c2a87499bf1c57e5f388cb8`.
  Handoff, corpo unsigned e agregado pending foram preparados; continua sendo
  preflight privado não promotable, sem assinatura autorizada, publicação ou
  aceitação pública;
- um novo candidato local `1.0.0-rc.2`, construído na HEAD documental
  `12df4557fbc2d5b0efb3eb445ad922e4c2cf414a`, passou `25/25` casos M3 reais
  em cache limpo no Apple M3 Pro. Seu `candidate.json` tem SHA-256
  `87eb3b3ca39d5ad2de08f2e74a662b0f5d3935a84a53ae16cb484cff9de10699` e o
  instalador tem SHA-256
  `01315b9571f6b7752b7a305842b6853e4df59e6f61c9e71b90ad1d1f774aed33`.
  O registro compacto está em
  `docs/releases/1.0.0-rc.2-native-preflight-local-2026-08-14.json`; o corpo
  e o agregado continuam unsigned/pending e não são evidência protegida;
- o catálogo separa `supported`, `conditional` e `preview`: stable macOS
  permanece condicional, nightly e Linux/Windows/macOS Intel permanecem preview
  quando não há evidência nativa do candidato exato;
- a instalação pessoal temporária não é usada pelos testes de release.
- a aceitação pública completa está implementada em
  `maintenance/tools/public_install_smoke.py --full-lifecycle` e no workflow
  M3 manual; a execução protegida histórica de 2026-08-14 falhou na etapa
  macOS `directory-preferences`, enquanto a rechecagem local posterior dos
  mesmos bytes públicos passou catálogo/TUF, instalação completa e todo o
  lifecycle. A execução seguinte também comprovou a migração real pública de
  `0.7.13` para o RC, com preservação de configuração, demo e PAKs. Os recibos
  locais estão em:
  `docs/releases/1.0.0-rc.1-public-acceptance-local-2026-08-14.json` e
  `docs/releases/1.0.0-rc.1-public-acceptance-migration-local-2026-08-14.json`;
  nenhum substitui o artifact do workflow protegido nem inicia o soak;
- o harness M3 agora contém migração 0.7.13, Frogbot, lifecycle apply, reparo
  por corrupção e purge; os contratos, testes locais e o run nativo local do
  candidato `1.0.0-rc.2` estão verdes em `25/25`, mas isso não substitui o
  handoff M3 assinado pelo workflow protegido nem a aceitação pelos endpoints
  públicos;
- o drill TUF offline está implementado em
  `maintenance/tools/tuf_operation_drill.py` e foi executado com sucesso sobre
  uma visão corrente temporária; o recibo histórico está em
  `docs/releases/1.0.0-rc.1-tuf-drill.json` e a execução local com operador,
  host e SLA explícitos está em
  `docs/releases/1.0.0-tuf-drill-local-2026-08-14.json`. O drill não publicou
  metadata. Esta execução usou chaves efêmeras e o catálogo local do checkout:
  prova o contrato técnico e o contexto não secreto, mas não prova custódia
  humana independente nem operação contínua de produção; o workflow protegido
  ainda é obrigatório;

## Candidato oficial e promoção

O RC foi construído uma vez, validado por artifacts imutáveis, executado no
runner Apple M3 e promovido sem reconstrução. O fluxo final confirmou:

1. candidato exato e `candidate.json` por digest;
2. evidência M3 assinada e vinculada ao candidato;
3. aprovação protegida e ausência de blockers;
4. publicação GitHub e GitLab com mirrors convergentes;
5. metadata TUF e site implantados por último;
6. verificação pública pós-deploy.

A evidência assinada foi usada pela promoção, mas ainda precisa ser publicada
de forma durável como asset (`release-evidence.json`, `evidence-root.json` e
`release-receipt.json`) para que a prova não dependa da retenção de artifacts de
Actions.

## Gaps e gates restantes

1. o período de uso do RC está registrado em
   `docs/releases/1.0.0-rc.1-soak.md`, mas foi interrompido pela falha de
   aceitação pública do RC.1 e precisa reiniciar com um novo candidato;
2. a evidência M3 deste RC ainda depende da retenção de 90 dias dos artifacts até
   que os três assets duráveis sejam publicados;
3. a aceitação pública pós-deploy tem uma rechecagem local verde e workflow/
   verificador implementados, mas ainda precisa do artifact e handoff do run
   M3 protegido;
4. a migração real pública de `0.7.13` para `1.0.0-rc.1` está comprovada no
   M3 local pelo recibo v2; Frogbot, upgrade apply e reparo por corrupção agora
   também estão verdes no preflight local `1.0.0-rc.2`, mas ainda precisam de
   execução pública/protegida, e os casos completos precisam ser repetidos em
   um candidato final novo;
5. a operação TUF tem monitor público verde, drill offline implementado e agora
   um workflow protegido que vincula o relatório ao recibo final; ainda faltam
   custódia de produção sustentada, renovação observada, alerta, expiração
   simulada e recuperação registrados no ambiente público;
6. Linux, Windows e macOS Intel continuam `preview`; stable macOS continua
   `conditional` enquanto Gatekeeper, notarização e primeira abertura do bundle
   upstream original não forem comprovados.

## Veredito

O RC público é um marco legítimo e está em `GO` para uso operacional. A
promoção de `1.0.0` permanece `NO-GO` até que todos os gates acima tenham
evidência pública e o candidato final seja novo. Nenhum novo `0.7.x` deve ser
publicado salvo regressão crítica.

## Próxima ação

Manter o soak do RC, fechar a aceitação pública e as lacunas M3, publicar a
evidência durável e provar a operação TUF. Depois disso, congelar a linha,
gerar um novo candidato `1.0.0`, repetir todos os gates sobre seus bytes e só
então promover a versão final.
