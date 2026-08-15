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

## Publicação final owner-only

Em 2026-08-15, a primeira release final foi publicada para o escopo
`owner-only`: [`x86qw-installer-1.0.0`](https://github.com/x86dx2/x86qw/releases/tag/x86qw-installer-1.0.0).
Ela aponta para o commit de produto
`e12ed081b968f820f47200e4be954a4f444056a1`, foi promovida no run
`31849932133` e teve o `metadata-last` concluído na tentativa 2 do mesmo run.
O registro detalhado, incluindo os hashes e a aceitação pública no M3, está em
[`1.0.0-owner-only-publication-2026-08-15.md`](releases/1.0.0-owner-only-publication-2026-08-15.md).

Estado atual: `1.0.0 owner-only = AT-RISK`; `external-public = NO-GO` até que o
mantenedor declare usuários externos e reative os gates condicionais. A lease
TUF pública está saudável, mas custódia independente, backup, RTO e a
convergência do deployment ainda permanecem gates abertos.

## Baseline real

A linha canônica é `origin/main`. A revisão exata de um snapshot deve ser
obtida com `git rev-parse origin/main` no momento da auditoria; este documento
não repete um SHA da própria linha que o contém, porque o merge de qualquer
atualização documental mudaria esse valor. A identidade do candidato oficial —
commit, SHA do `candidate.json` e digest do artifact — é sempre a registrada
pelo próprio workflow e pelo checkpoint do PR, nunca por uma cópia manual neste
documento.

## Versões públicas

A versão estável-fonte continua em `dist/installer/VERSION = 0.7.13`; ela é a
baseline histórica preservada para a futura audiência externa.

A release final atual é `x86qw-installer-1.0.0`, owner-only, com instalador de
600825 bytes e SHA-256
`d3274e6aa2f1e3078ac5000ffae8b97c9efd329f3c2a87499bf1c57e5f388cb8`.
O `candidate.json` público tem 17405 bytes e SHA-256
`0bde0550895cab24abf8a3ee974da011e031fea11279148a41635e173cbdcc21`.

O `1.0.0-rc.1` permanece publicado como prerelease histórica, deliberadamente
separada da linha estável-fonte e da release final. Sua identidade e seus
gates estão preservados em [`1.0.0-rc.1.md`](releases/1.0.0-rc.1.md).

A imutabilidade host-level da release GitHub continua `immutable=false`; o
publisher mantém imutabilidade lógica recusando overwrite, divergência de
digest e assets extras. A avaliação está em
[`1.0.0-github-immutable-release-evaluation.md`](releases/1.0.0-github-immutable-release-evaluation.md).

## Estado de confiança

A root Ed25519 incorporada autenticou o catálogo público final. Na verificação
de 2026-08-15, o endpoint serviu timestamp, snapshot e targets na versão 18;
timestamp expirava em `2026-08-15T21:09:01Z` e snapshot em
`2026-08-21T21:09:01Z`. TUF, bootstraps e product passaram tanto no job
`metadata-last` retomado quanto na verificação independente local.

O handoff usado pela release foi o run `31849830873`, artifact `9237154449`.
A custódia independente, backup humano, RTO de produção e sucessão continuam pendência P1 em #148; a lease técnica imediata foi renovada e #152 foi encerrada.

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
- o preflight privado histórico de `1.0.0`, construído na HEAD
  `f2cadeff3261ce07f7c9490313db1aa69e417fa2`, passou os mesmos `25/25` casos
  no Apple M3 Pro. Seus bytes foram posteriormente promovidos como o candidato
  oficial `e12ed081b968f820f47200e4be954a4f444056a1` após as verificações
  protegidas; o registro final está em
  [`1.0.0-owner-only-publication-2026-08-15.md`](releases/1.0.0-owner-only-publication-2026-08-15.md);
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
  lifecycle e purge. O workflow protegido `31845951477` também passou no M3,
  gerou o artifact `9235987853` e produziu o recibo SHA-256
  `492fdc4995ceb187ed738ca44a129192c3a9743607567ffdef4821f5299c2bdc`. O
  recibo histórico de migração continua arquivado como evidência opcional, não
  como bloqueio do owner-only;
- a aceitação pública final independente foi executada no M3 usando o endpoint
  público e o instalador `1.0.0`; seu recibo durável está em
  [`1.0.0-owner-only-public-acceptance-m3-2026-08-15.json`](releases/1.0.0-owner-only-public-acceptance-m3-2026-08-15.json);
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
  prova antes da publicação. Esse caminho foi executado e publicado no run
  `31839143732`, e o deploy timestamp-only foi verificado no run
  `31845099782`. A operação contínua, a custódia independente e a recuperação
  fora do fluxo pontual continuam pendência `external-public`; os workflows
  devem falhar fechado até que a custódia seja configurada ou o modo manual B
  seja comprovado. Isso não bloqueia o modo `owner-only`;
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

O candidato final `1.0.0` foi construído uma vez no commit
`e12ed081b968f820f47200e4be954a4f444056a1`, validado pelos contratos portáveis,
executado no Apple M3, aprovado no limite protegido e promovido sem
reconstrução. A cadeia final confirmou:

1. candidato exato e `candidate.json` por digest;
2. evidência M3 assinada e vinculada ao candidato;
3. aceitação pública RC e handoff TUF autenticados;
4. publicação GitHub e GitLab com mirrors convergentes;
5. `release-evidence.json`, `evidence-root.json` e `release-receipt.json` como
   assets duráveis;
6. metadata TUF e site implantados por último;
7. verificação pública pós-deploy, repetida independentemente no M3.

O registro completo está em
[`1.0.0-owner-only-publication-2026-08-15.md`](releases/1.0.0-owner-only-publication-2026-08-15.md).
O run `31849932133` começou com falha transitória no `metadata-last`; a
tentativa 2 repetiu somente esse job e encerrou o workflow com sucesso.

## Gaps e gates restantes

### Gates do modo owner-only

1. evidência M3 assinada, vinculada e publicada de forma durável — concluído;
2. aceitação `single-user` pública do candidato exato, com instalação limpa,
   lifecycle, uninstall e purge — concluído no M3 local em
   [`1.0.0-owner-only-public-acceptance-m3-2026-08-15.json`](releases/1.0.0-owner-only-public-acceptance-m3-2026-08-15.json);
3. branch dos workflows remota e validação protegida — concluído;
4. nenhuma falha P0 de integridade — concluído; P1 de verdade de deployment permanece aberto em Gate 0C;
5. Linux, Windows e macOS Intel continuam `preview`; stable macOS continua
   `conditional` enquanto Gatekeeper, notarização e primeira abertura do bundle
   upstream original não forem comprovados.

### Pendências estacionadas até usuários externos

1. migração real de `0.7.13` e preservação de instalações antigas;
2. soak protegido de sete dias;
3. drill operacional TUF com custódia e renovação observadas;
4. lease TUF sustentável durante uso externo e resolução da issue #152;
5. aceitação `external-users` pelos endpoints públicos.

Essas pendências não autorizam alegar compatibilidade externa. Elas não
bloqueiam o uso da release já publicada no modo `owner-only`.

## Registro remoto de governança

Uma rechecagem remota posterior confirmou que os workflows de aceitação,
renovação timestamp-only e publicação estão presentes no remoto. O workflow
protegido de aceitação `31845951477` passou no M3 e o runner efêmero foi
removido. O timestamp público foi renovado e o deploy `31845099782` passou a
verificação pós-deploy. A release RC continua sem imutabilidade host-level;
isso é uma pendência P2, não uma alteração a ser feita no RC. O snapshot
histórico em
[`1.0.0-rc.1-remote-gates-2026-08-14.json`](releases/1.0.0-rc.1-remote-gates-2026-08-14.json)
deve ser lido como fotografia anterior, não como estado atual.

- [#143 — RC soak histórico](https://github.com/x86dx2/x86qw/issues/143) (superseded/closed);
- [#144 — durable signed release evidence](https://github.com/x86dx2/x86qw/issues/144) (P2, capacidade concluída para owner-only; externo pendente);
- [#145 — public RC acceptance](https://github.com/x86dx2/x86qw/issues/145) (fechada após o run protegido);
- [#146 — real 0.7.13 migration](https://github.com/x86dx2/x86qw/issues/146);
- [#147 — remaining M3 functional coverage](https://github.com/x86dx2/x86qw/issues/147) (fechada após 25/25);
- [#148 — sustainable TUF operation](https://github.com/x86dx2/x86qw/issues/148) (custody/RTO/backup ainda abertos);
- [#149 — final 1.0.0 promotion](https://github.com/x86dx2/x86qw/issues/149) (fechada após o run `31849932133`, tentativa 2);
- [#150 — remote branch cleanup](https://github.com/x86dx2/x86qw/issues/150);
- [#151 — GitHub immutable release evaluation](https://github.com/x86dx2/x86qw/issues/151);
- [#152 — TUF public lease attention](https://github.com/x86dx2/x86qw/issues/152) (closed after v19 publication).

O último monitor TUF incluído nessa leitura foi o run
[31791717871](https://github.com/x86dx2/x86qw/actions/runs/31791717871), com
falha por lease dentro da janela de alerta. Depois desse snapshot histórico,
o run [31798419309](https://github.com/x86dx2/x86qw/actions/runs/31798419309)
falhou pela mesma causa. O snapshot completo, incluindo os limites da leitura,
está em
[`1.0.0-rc.1-remote-gates-2026-08-14.json`](releases/1.0.0-rc.1-remote-gates-2026-08-14.json).

## Veredito

`1.0.0 owner-only`: `AT-RISK` até o deployment live refletir a projeção
owner-only atual. `MAIN=GREEN` e `TUF=HEALTHY`; `external-public=NO-GO` até
EP-0..EP-5, custódia TUF sustentável e aceitação externa. Nenhuma feature 1.1+
começa antes de Gate 0C/0D/0E.

## Próxima ação

Concluir Gate 0C com uma geração site única e verificável, incluindo
`/api/v1/release-truth.json`, audiência owner-only em `product.json`, e root
sem claim público 0.7.13. Depois reconciliar Gate 0D e iniciar a observação
owner-only; não publicar uma nova release de produto por correção exclusivamente
documental.
