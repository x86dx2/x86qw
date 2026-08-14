# Estado atual do projeto

## Modo operacional vigente

Desde 2026-08-14, o projeto está formalmente em `owner-only`: um único
usuário/mantenedor e instalação descartável autorizada. A decisão está em
[`ADR 0008`](adr/0008-owner-only-release-gates.md).

Neste modo, instalação limpa, lifecycle descartável, evidência M3, contratos
portáveis, build-once e integridade dos bytes são gates. Migração histórica,
soak de usuários externos e operação TUF sustentável são capacidades
`post-public`; não bloqueiam a primeira instalação do mantenedor. O código e as
fixtures de migração permanecem preservados.

Quando o produto for declarado aberto a usuários externos, a promoção deverá
usar `release_audience=external-public`; esse valor reativa explicitamente os
gates de migração, soak e operação TUF externa.

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
A avaliação de compatibilidade e rollback está em
[`1.0.0-github-immutable-release-evaluation.md`](releases/1.0.0-github-immutable-release-evaluation.md);
a release RC não será alterada para experimentar essa configuração.

## Estado de confiança

A root Ed25519 incorporada é validada localmente. O monitor público encontrou
root v1, timestamp v16, snapshot v16 e targets v16; autenticou o catálogo com
75 pacotes e timestamp válido até `2026-08-14T15:42:54Z`. O recibo está em
[`docs/releases/1.0.0-rc.1-tuf-monitor-2026-08-14.json`](releases/1.0.0-rc.1-tuf-monitor-2026-08-14.json).
Esta é uma fotografia do endpoint público em 2026-08-14 05:04 UTC; não
constitui evidência de custódia humana independente nem substitui a cerimônia
TUF do candidato. As execuções seguintes do monitor, runs `31791717871` às
10:19 UTC e `31798419309` às 11:59 UTC, falharam porque a mesma lease de
`timestamp` entrou na janela de alerta de seis horas; a issue automática
[#152](https://github.com/x86dx2/x86qw/issues/152) permanece aberta. O registro
do primeiro alerta está em
[`1.0.0-rc.1-tuf-monitor-alert-2026-08-14.json`](releases/1.0.0-rc.1-tuf-monitor-alert-2026-08-14.json).
Enquanto a renovação/recuperação manual não for concluída e verificada, o gate
TUF de `external-public` está `NO-GO`; a validação da cadeia ainda é exigida
para qualquer instalação que use endpoints públicos.

## Estado local

- downloader, archives, SemVer, launchers, `changes` e `migrate` compartilham
  contratos de runtime;
- fixtures de migração continuam cobrindo os instaladores públicos
  `0.7.0`–`0.7.13`, mas são capacidade pós-publicação e não gate do modo atual;
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
- a aceitação M3 possui dois escopos explícitos em
  `maintenance/tools/public_install_smoke.py --acceptance-scope`: o modo
  `single-user` instala em destino vazio e executa lifecycle descartável sem
  baixar `0.7.13`; o modo `external-users` acrescenta a migração histórica.
  A rechecagem local dos bytes públicos passou catálogo/TUF, instalação limpa,
  lifecycle e purge. O recibo histórico de migração continua arquivado como
  evidência opcional, não como bloqueio do owner-only;
- o harness M3 agora contém migração 0.7.13, Frogbot, lifecycle apply, reparo
  por corrupção e purge; os contratos, testes locais e o run nativo local do
  candidato `1.0.0-rc.2` estão verdes em `25/25`, mas isso não substitui o
  handoff M3 assinado pelo workflow protegido nem a aceitação pelos endpoints
  públicos;
- o drill TUF offline formato 2 está implementado em
  `maintenance/tools/tuf_operation_drill.py` e foi executado com sucesso sobre
  uma visão corrente temporária; o recibo histórico está em
  `docs/releases/1.0.0-rc.1-tuf-drill.json` e a execução local com operador,
  host, SLA e versões individuais por role está em
  `docs/releases/1.0.0-tuf-drill-local-2026-08-14.json`. O drill não publicou
  metadata. Esta execução usou chaves efêmeras e o catálogo local do checkout:
  prova o contrato técnico e o contexto não secreto, mas não prova custódia
  humana independente nem operação contínua de produção; o workflow protegido
  ainda é obrigatório;
- a ferramenta `maintenance/tools/tuf_timestamp_renewal.py` implementa o
  caminho de signer limitado: aceita somente uma chave da role `timestamp`,
  autentica a saída e recusa qualquer mudança fora de
  `metadata/timestamp.json`; `verify_tuf_timestamp_renewal.py` repete essa
  prova antes da publicação. O workflow
  `.github/workflows/tuf-timestamp-publish.yml` então monta e implanta somente
  essa geração timestamp-only sob aprovação protegida e verifica TUF,
  bootstraps e product públicos; `verify_tuf_timestamp_publication.py` valida o
  recibo antes do upload. Não há ainda signer configurado nem renovação
  observada no endpoint público. A
  última leitura somente-leitura do ambiente protegido `release` confirmou que
  `TUF_TIMESTAMP_KEY_B64` ainda não existe; os workflows de operação externa
  devem falhar fechado até que a custódia seja configurada ou o modo manual B
  seja comprovado. Isso é pendência `external-public`, não bloqueio do modo
  owner-only;
- o período de uso agora possui um workflow protegido em
  `.github/workflows/rc-soak.yml`: ele exige a ref do commit exato do RC,
  confere a issue canônica fechada, valida sete dias de observações verdes com
  hardware `macos-arm64`/M3 e uma referência HTTPS por dia, e publica um
  artifact imutável. O job `verify-soak` de `release.yml` exige esse handoff e
  o inclui no recibo final quando `release_audience=external-public`. Nenhum run
  protegido concluído está registrado ainda; o soak fica estacionado até a
  declaração de usuários externos;
- a promoção final também executa `monitor_public_tuf.py` imediatamente antes
  da evidência M3. O gate foi incluído porque o drill operacional, sozinho, não
  prova que a lease pública atual ainda está saudável;

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
Actions. Os três artifacts do run final ainda estavam retidos na última
verificação somente-leitura; suas IDs, digests e tamanhos estão registrados em
[`1.0.0-rc.1-promotion-artifacts-2026-08-14.json`](releases/1.0.0-rc.1-promotion-artifacts-2026-08-14.json).

## Gaps e gates restantes

### Gates do modo owner-only

1. a evidência M3 do candidato final precisa ser assinada, vinculada e
   publicada de forma durável; artifacts de Actions com retenção de 90 dias não
   bastam como único registro;
2. a aceitação `single-user` do candidato exato precisa ser executada no runner
   M3 protegido, com instalação limpa, lifecycle, uninstall e purge;
3. a branch que contém os workflows precisa estar no GitHub remoto e passar a
   validação protegida; o checkout local não executa Actions;
4. nenhum blocker P0/P1 pode permanecer aberto;
5. Linux, Windows e macOS Intel continuam `preview`; stable macOS continua
   `conditional` enquanto Gatekeeper, notarização e primeira abertura do bundle
   upstream original não forem comprovados.

### Pendências estacionadas até usuários externos

1. migração real de `0.7.13` e preservação de instalações antigas;
2. soak protegido de sete dias;
3. drill operacional TUF com custódia e renovação observadas;
4. lease TUF sustentável durante uso externo e resolução da issue #152;
5. aceitação `external-users` pelos endpoints públicos.

Essas pendências não autorizam alegar compatibilidade externa, mas não impedem
o uso e a promoção no modo `owner-only` quando os gates da seção anterior
estiverem verdes.

## Registro remoto de governança

Na verificação somente-leitura de 2026-08-14 11:24 UTC, o GitHub confirmou a
release RC pública como prerelease não draft, mas ainda sem imutabilidade
host-level. As issues canônicas abertas eram:

Uma rechecagem somente-leitura às 13:35 UTC confirmou que os workflows locais
de aceitação pública, soak, drill operacional TUF, renovação limitada de
timestamp e publicação timestamp-only ainda não estão presentes no remoto. O
ambiente protegido `release`
continua sem o secret `TUF_TIMESTAMP_KEY_B64`; o monitor público mais recente
continua sendo o run `31798419309`, concluído com falha por lease dentro da
janela de alerta. Esse snapshot está registrado em
[`1.0.0-rc.1-remote-gates-2026-08-14.json`](releases/1.0.0-rc.1-remote-gates-2026-08-14.json);
nenhuma mutação remota foi executada nesta rechecagem.

- [#143 — RC soak](https://github.com/x86dx2/x86qw/issues/143);
- [#144 — durable signed release evidence](https://github.com/x86dx2/x86qw/issues/144);
- [#145 — public RC acceptance](https://github.com/x86dx2/x86qw/issues/145);
- [#146 — real 0.7.13 migration](https://github.com/x86dx2/x86qw/issues/146);
- [#147 — remaining M3 functional coverage](https://github.com/x86dx2/x86qw/issues/147);
- [#148 — sustainable TUF operation](https://github.com/x86dx2/x86qw/issues/148);
- [#149 — final 1.0.0 promotion](https://github.com/x86dx2/x86qw/issues/149);
- [#150 — remote branch cleanup](https://github.com/x86dx2/x86qw/issues/150);
- [#151 — GitHub immutable release evaluation](https://github.com/x86dx2/x86qw/issues/151);
- [#152 — TUF public lease attention](https://github.com/x86dx2/x86qw/issues/152).

O último monitor TUF incluído nessa leitura foi o run
[31791717871](https://github.com/x86dx2/x86qw/actions/runs/31791717871), com
falha por lease dentro da janela de alerta. Depois desse snapshot histórico,
o run [31798419309](https://github.com/x86dx2/x86qw/actions/runs/31798419309)
falhou pela mesma causa. O snapshot completo, incluindo os limites da leitura,
está em
[`1.0.0-rc.1-remote-gates-2026-08-14.json`](releases/1.0.0-rc.1-remote-gates-2026-08-14.json).

## Veredito

O RC público é um marco legítimo e está em `GO` para uso do mantenedor. A
promoção de `1.0.0` em modo `owner-only` fica bloqueada somente pelos gates
owner-only acima; migração, soak externo e operação TUF sustentável não são
pré-requisitos desta fase. A promoção `external-public` continua `NO-GO` até
que todas as pendências externas sejam comprovadas. Nenhum novo `0.7.x` deve
ser publicado salvo regressão crítica.

## Próxima ação

Publicar a alteração de política, executar a aceitação `single-user` no M3,
publicar a evidência durável e repetir o candidato final sobre os bytes exatos.
Quando o mantenedor declarar usuários externos, reabrir o plano de migração,
soak e operação TUF usando `release_audience=external-public`.
