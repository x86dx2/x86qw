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

Estado operacional rebaselineado: o escopo corrente é um único usuário no Apple M3. `owner-only` é válido somente para esse escopo; `external-public = NO-GO`. A lease TUF técnica continua necessária para instalar e atualizar o ambiente do mantenedor, mas custódia independente, backup e RTO são requisitos condicionais de uma abertura para terceiros.

## Autoridade viva e última fotografia local

O estado corrente de deployment é exclusivamente o endpoint
`https://qw.x86.com.br/api/v1/release-truth.json`, conferido também contra o
alias pelo comando:

```sh
python3 maintenance/tools/verify_live_release_truth.py
```

[`release-truth-current.json`](post-1.0/release-truth-current.json) é apenas o
ponteiro machine-readable para essa autoridade. A fotografia versionada
[`release-truth-projection-seed.json`](post-1.0/release-truth-projection-seed.json)
é uma semente offline para montagem e testes; não declara o estado vivo atual.

### Última fotografia registrada em 2026-08-28T02:32:21Z

`MAIN=GREEN`; `TUF=HEALTHY`; `external-public=NO-GO`; a release
`1.0.0 owner-only` continua `VALID_FOR_SINGLE_USER_M3`. A projeção pública
convergente foi verificada no run [33136179763](https://github.com/x86dx2/x86qw/actions/runs/33136179763),
com o catálogo, product, bootstraps, site e release-truth servindo os bytes do
candidato exato. O Validate observado da linha `main` foi o run
[33135951867](https://github.com/x86dx2/x86qw/actions/runs/33135951867).

Nessa fotografia, o TUF público era root v1, timestamp v30 e snapshot/targets v29, com
75 pacotes e timestamp válido até `2026-09-27T02:15:12Z`. A renovação técnica
foi registrada no run `33135314707`/artifact `9671800710`; custódia
independente, backup humano e RTO continuam condicionais a `external-public`.
Os detalhes históricos dessa observação permanecem na semente offline e a
regra de consulta está em
[`RELEASE-TRUTH-CURRENT.md`](post-1.0/RELEASE-TRUTH-CURRENT.md).


## Baseline real

A linha canônica é `origin/main`. A revisão exata de um snapshot deve ser
obtida com `git rev-parse origin/main` no momento da auditoria; este documento
não repete um SHA da própria linha que o contém, porque o merge de qualquer
atualização documental mudaria esse valor. A identidade do candidato oficial —
commit, SHA do `candidate.json` e digest do artifact — é sempre a registrada
pelo próprio workflow e pelo checkpoint do PR, nunca por uma cópia manual neste
documento.

## Versões públicas

A versão-fonte current em `dist/installer/VERSION` é `1.0.4`. A release pública
`0.7.13` é a baseline histórica preservada para a futura audiência externa;
`1.0.0` é a release owner-only publicada e verificada.

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

A root Ed25519 incorporada autentica o catálogo público final. Na observação
viva de `2026-08-28T02:32:21Z`, o endpoint serviu root v1, timestamp v30 e
snapshot/targets v29; timestamp expira em `2026-09-27T02:15:12Z`, com 75
pacotes e catálogo SHA-256
`a03a8b0e3dcd97a66d338891dacd6ca80befdbee907ed9b83007a538bb97646a`.
TUF, bootstraps, product e release-truth passaram a verificação independente
nos domínios canônico e alias.

O run de renovação timestamp-only dessa fotografia foi `33135314707`, artifact
`9671800710`; o run de publicação/verificação da projeção é `33136179763`,
artifact `9672118367`. A custódia independente, backup humano e RTO continuam
pendência condicional de `external-public`; a lease técnica do owner-only está
saudável.

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
4. nenhuma falha P0 de integridade — concluído; P1 de verdade de deployment foi resolvido por Gate 0C;
5. Linux, Windows e macOS Intel continuam `preview`; stable macOS continua
   `conditional` enquanto Gatekeeper, notarização e primeira abertura do bundle
   upstream original não forem comprovados.

### Pendências estacionadas até usuários externos

1. migração real de `0.7.13` e preservação de instalações antigas;
2. soak protegido de sete dias;
3. drill operacional TUF com custódia e renovação observadas;
4. lease TUF sustentável durante uso externo; #152 foi encerrada após a publicação v20;
5. aceitação `external-users` pelos endpoints públicos.

Essas pendências não autorizam alegar compatibilidade externa. Elas não
bloqueiam o uso da release já publicada no modo `owner-only`.

## Registro remoto de governança

Na rechecagem remota de `2026-08-27`, os workflows de aceitação, renovação
timestamp-only, monitoramento e projeção estavam presentes no remoto. A
projeção foi reparada e verificada no run `33136179763`; o Validate da linha
`main` passou no run `33135951867`. A promoção owner-only permanece imutável;
`external-public` continua `NO-GO` sem nova autorização de audiência.

As issues históricas [#143–#152](https://github.com/x86dx2/x86qw/issues/143)
estão fechadas no GitHub. O encerramento registra a capacidade ou o gate
owner-only correspondente; não converte migração externa, soak, custódia
independente ou RTO em autorização para abrir a audiência. A avaliação de
imutabilidade host-level (#151) e a limpeza de refs (#150) também permanecem
preservadas como decisões históricas, sem alterar os bytes publicados.

Os runs anteriores `31791717871`, `31798419309`, `31845099782` e
`31849932133` continuam arquivados como fotografias históricas em
[`1.0.0-rc.1-remote-gates-2026-08-14.json`](releases/1.0.0-rc.1-remote-gates-2026-08-14.json);
eles não são a autoridade do deployment corrente.

## Veredito

`1.0.0 owner-only`: válido para o único usuário no M3 após a verificação protegida da projeção; `MAIN=GREEN`; `TUF=HEALTHY`; `external-public=NO-GO`. A funcionalidade pode avançar depois de S0-M3. EP-0..EP-5 ficam estacionados até existir decisão de audiência externa.

## Próxima ação

Fonte current `1.0.4`; `1.0.3`, `1.0.2`, `1.0.1`, `1.0.0` e `0.7.13`
históricas conforme seus papéis no catálogo. F1–F3 e o conserto de `host`
ficam em 1.0.x. Manter a lease TUF fora da janela de 6 h. Migração histórica,
outras plataformas e QWLeague não bloqueiam esse fluxo owner-only.
