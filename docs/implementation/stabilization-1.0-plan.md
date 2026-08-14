# Plano de estabilização `0.7.13 → 1.0`

| Campo | Valor |
|---|---|
| Estado | `1.0.0-rc.1` público; aceitação pública v2 e drill técnico TUF locais verdes; soak e gates protegidos finais pendentes |
| Baseline pública | release imutável `x86qw-installer-0.7.13`, preparada no commit `04a55aed8711ec5466dc70f0e33a591d92e07ccb` |
| Base canônica | `origin/main` |
| Checkpoint auditável | `main@335d9a062f8ce33b226a9892de82979828a0fd1b`; RC `a8758ee27bebd7c72c24a31dc19335652e260c0a`; promoção `31752738047` |
| Versão alvo | `1.0.0` |
| Data da auditoria | 2026-08-13, verificações finais em 2026-08-14 UTC |
| Escopo desta entrega | documentação, evidência durável, aceitação pública, cobertura M3, operação TUF e gates de promoção |

Este documento registra a sequência aprovada para transformar a baseline pública
`0.7.13` em uma `1.0` verificável. O estado corrente fica em
[PROJECT-STATUS.md](../PROJECT-STATUS.md), o índice estratégico em
[ROADMAP.md](../ROADMAP.md) e o registro de baseline em
[stabilization-baseline-2026-07-31.md](stabilization-baseline-2026-07-31.md). Este plano não promove o
checkpoint, não autoriza a publicação e não substitui aprovação humana.

## 1. Fronteira, autoridade e invariantes

`origin/main` é a única linha canônica. A release pública `0.7.13` e todos
os bundles públicos anteriores permanecem imutáveis. O checkpoint é somente uma
fonte de consulta para extração seletiva: **checkpoint para extração, nunca
merge**. Nenhum commit de `codex/stabilize-1.0` será incorporado como unidade.

O checkpoint histórico que originou este plano não autorizava publicação. A
fotografia atual confirma que `1.0.0-rc.1` já é uma prerelease pública; este
plano continua sem autorizar a promoção final, alteração dos bytes publicados
ou publicação de metadata TUF fora do workflow protegido. A implementação local
do fechamento dos gates está na branch `agent/rc-1.0-completion`, no `HEAD`
verificável dessa branch. O preflight privado `1.0.0`
da revisão de produto `f2cadeff3261ce07f7c9490313db1aa69e417fa2` passou `25/25`
casos no Apple M3 Pro, com `candidate.json` SHA-256
`c7357159df806b29d8c9eb715152ec6186c5d9edefd3bb5587dbf6c98a0a94c7`; isso
ainda precisa passar pelo fluxo remoto, handoff assinado e endpoint público
antes de ser considerado evidência de release. Os commits posteriores desta
sequência alteraram apenas tooling de release e documentação, não os arquivos
de produto cobertos por aquele preflight.

As invariantes para todas as fases são:

- uma frente estrutural por vez, vinculada a uma issue e a uma branch;
- implementação e release são PRs diferentes;
- bytes upstream, customizações x86QW, metadata de trust e evidência nativa
  mantêm fronteiras explícitas;
- compatibilidade declarada nunca é apresentada como execução nativa;
- artefatos publicados são imutáveis e uma promoção sem evidência falha
  fechada;
- TDD RED → GREEN → REFACTOR será exigido nas PRs A–G quando houver mudança
  testável; esta entrega documental dispensa TDD.

## 2. Auditoria das linhas e dos PRs históricos

A auditoria foi feita sobre as refs locais disponíveis em 2026-08-06. Antes de
qualquer ação futura de limpeza, a ancestralidade deve ser revalidada contra
`origin/main`; a lista abaixo não é uma autorização para apagar refs.

| Ref | Estado observado | Decisão do plano |
|---|---|---|
| `origin/main` | resolver com `git rev-parse origin/main` no snapshot | única linha canônica |
| `codex/stabilize-1.0` | `30e9d5b`, checkpoint com trabalho de preparação | congelada; extrair símbolos/hunks, nunca fazer merge/cherry-pick |
| `origin/codex/control-maps` | `362f705`, quatro commits à frente e 105 atrás, quatro commits exclusivos | manter até uma decisão própria; não apagar com a limpeza do checkpoint |
| `agent/bounded-downloader` | `a60a625`; 0 à frente/86 atrás, integrado pela PR #58 (`b833ba4`) | apagar apenas depois de revalidar ancestralidade e com autorização |
| `agent/archive-boundary` | `3228865`; 0 à frente/74 atrás, integrado pela PR #59 (`b746d57`) | apagar apenas depois de revalidar ancestralidade e com autorização |
| `agent/runtime-boundaries` | `e5a90a6`; 0 à frente/10 atrás, extração histórica integrada pela PR #62 e publicada pela PR #64 | apagar apenas depois de revalidar ancestralidade e com autorização |
| `origin/x86dx2/audit-ezquake-tournament-gameplay` | `6d659d4`; 0 à frente/5 atrás, ancestral de `origin/main` | apagar apenas depois de revalidar ancestralidade e com autorização |
| `gitlab/main` | `afb4f66` (`0.7.1`) | não sincronizar; função de espelho ainda precisa de decisão |
| `fix/macos-stable-no-entitlements` | ref remota gone; branch local histórica | não recriar nem tratar como linha canônica |

As seis frentes históricas de estabilização estão encerradas como entregas de
código/publicação anteriores à jornada 1.0:

| PR histórica | Resultado encerrado | Evidência de referência |
|---|---|---|
| PR 1 — bootstrap Python | publicada na `0.7.1` | PR #56, merge `8ba1f90`; [contrato Python](python-runtime-contract-0.7.1.md) |
| PR 2 — downloader limitado | publicada na `0.7.3` | PR #58, merge `b833ba4`; [fronteira de download](bounded-download-boundary.md) |
| PR 3 — ZIP/PK3/PYZ único | publicada na `0.7.3` | PR #59, merge `b746d57`; [fronteira de arquivos](safe-archive-boundary.md) |
| PR 4 — DACL privada Windows | publicada na `0.7.3` | PR #60, merge `206adc4`; [implementação Windows](windows-private-acl.md) |
| PR 5 — stable macOS upstream | publicada na `0.7.3` | PR #61, merge `0009833`, correção pública PR #65 `3bbc7a0`; [ADR 0004](../adr/0004-preservar-bundle-upstream-ezquake-stable-macos.md) |
| PR 6 — fronteiras de runtime | integrada na `0.7.3`; trilha auditável encerrada como entrega, sem promover o checkpoint | HEAD histórico `29d76a4`, integração `daf5d0c`, publicação PR #64 `15eef0b`; [evidência da PR 6](runtime-boundaries-pr6.md) |

O encerramento histórico não transforma os artefatos do checkpoint em código
aprovado para `1.0`. Qualquer símbolo restante precisa de uma issue da sequência
A–G, revisão própria e evidência do candidato exato.

As matrizes históricas relevantes permanecem como evidência datada, não como
gate futuro: PR 3 (`30856293818`), PR 4 (`30866754435`) e PR 5
(`30871046055`). Elas provam contratos dos seus commits, não smokes nativos do
candidato `1.0`.

## 3. Regra de extração do checkpoint

A extração é seletiva e feita por símbolo ou hunk, sempre comparando o resultado
com a baseline `origin/main`. Os arquivos compartilhados entre fases —
`manager.py`, `maintenance/manage.py`, `build_installer_bundle.py`,
`release_candidate.py`, `test_ci.py` e
`maintenance/inventory/installer-runtime-members.json` — não podem ser
copiados integralmente. Cada hunk deve declarar consumidor, contrato, teste e
issue de destino.

### Extrair quando houver contrato e issue

- governança, avisos, ownership, Dependabot, lockfile do site, threat model e
  runbooks na PR B;
- SemVer, schemas, envelopes JSON, redaction e códigos estáveis na PR C;
- migração unilateral de fixtures reais `0.7.0–0.7.13` na PR D;
- ADR, biblioteca e cerimônia de trust aprovados na E1/E2;
- preparação/verificação de candidato, ownership, SBOM, provenance e mirrors
  na PR F;
- executor nativo e evidência M3 na PR G;
- promoção sem correção funcional na PR H.

### Rejeitar nesta jornada

- gameplay, KTX, downloader, host, supervisor ou correções incidentais sem uma
  issue própria;
- workflows com etapas reservadas, rebuild após aprovação ou claims nativos
  sem evidência;
- fixtures prospectivas `0.8.x`/`0.9.x` tratadas como releases publicadas;
- chaves privadas, metadata de produção, chaves/roles de fixture ou o trust que
  expira em `2026-08-08`;
- evidência nativa sintética, promoção local como substituta de publicação
  protegida ou qualquer alteração em bundles já publicados.

## 4. Política de plataformas e CI

Artefato publicado, suporte e validação são três dimensões diferentes. Um
registro de plataforma só pode usar os estados abaixo:

| Estado | Regra |
|---|---|
| `supported` | exige evidência nativa do candidato exato |
| `conditional` | mantém o artefato, mas documenta o contrato que ainda falta |
| `preview` | há disponibilidade/contrato, sem smoke nativo do candidato |
| `deprecated` | o artefato permanece enquanto a remoção é conduzida |
| `unavailable` | deriva da ausência de artefato; não é um sinônimo de “não testado” |

Linux, Windows, macOS Intel e nightly começam como `preview`. O artefato
macOS universal físico é preservado, mas o suporte é projetado separadamente
para `macos-arm64` e `macos-x64`. A evidência M3 só muda o estado após a PR G:
serviços podem tornar-se `supported`; o cliente stable continua `conditional`
se assinatura/notarização ainda for uma limitação. A matriz portátil não é
smoke nativo.

Os workflows agora existem como gates executáveis: a validação portável é
separada do runner self-hosted M3, e nenhum job portável afirma execução
gráfica, de rede ou de Gatekeeper. A promoção exige approval, mirrors,
evidência durável, aceitação pública para a final e metadata-last; ausência de
configuração externa falha fechada.

## 5. Sequência A–H

Cada fase começa com uma issue, cria sua branch somente depois da issue, fecha
seu gate de contrato e só então desbloqueia a fase seguinte. Os alvos de versão
são sugestões de publicação, não versões preparadas nesta entrega.

### PR A — verdade de plataforma

Criar uma issue própria antes da branch. Separar artefato publicado, suporte e
validação; conservar o macOS universal físico; classificar Linux, Windows,
macOS Intel e nightly como `preview`; preservar a CI com os nomes
`portable-contract / ...`; e registrar a regra de evidência do candidato exato.
macOS M3 não é promovido antes de G. A baseline estável pública `0.7.13` não é
reescrita; qualquer correção futura deve ser uma release nova, separada da PR
de implementação e justificada por um gap P0/P1.

**Gate:** catálogo e documentação usam os cinco estados sem misturar presença
com execução; CI portável permanece intacta; a issue de release da `0.7.4` é
separada da implementação.

### PR B — governança (`issue #55`)

Extrair licença, avisos, ownership, Dependabot, lockfile do site, threat model
e runbooks. Não remover workflows e não misturar trust, promoção ou publicação.

**Gate:** cada novo arquivo tem owner e licença; avisos carregam para a
distribuição moderna; runbooks apontam para uma autoridade única.

### PR C — contratos (`issue #53`, alvo `0.8.0`)

Extrair SemVer, schemas de estado/receipt, envelopes JSON, redaction e códigos
por símbolo/hunk. Os writers reais devem emitir as versões congeladas. Antes de
declarar um RC publicável, builders, archives, catálogo, bootstraps e ordenação
devem aceitar `1.0.0-rc.1`.

**Gate:** schemas, writers e consumidores concordam; fixtures negativas cobrem
redaction e códigos; nenhum bootstrap 0.7.x é quebrado.

### PR D — migração (`issue #46`, alvo `0.8.1` ou `0.8.2`)

Atualizar a issue para suportar somente versões realmente publicadas. Usar
fixtures reais `0.7.0–0.7.13`; remover claims sintéticos
`0.8.x`/`0.9.x`; garantir
que o estado migrado satisfaz os schemas congelados em C e que rollback e
ownership continuam diagnosticáveis.

**Gate:** migração unilateral, preservação de arquivos pessoais, recibos
coerentes e ausência de versão futura inventada.

### PRs E1/E2 — trust (`issue #48`, alvo `0.9.0`)

Manter a issue aberta nas duas PRs. E1 aprova ADR, biblioteca, algoritmo,
thresholds, custódia, endpoints, expiração, rotação e cerimônia. E2 só
implementa depois de aprovação e revisão criptográfica externa.

Nunca promover RSA-PSS própria, chaves/roles de fixture ou metadata do
checkpoint que expira em `2026-08-08`.

**Gate:** a chave de produção foi criada em cerimônia aprovada, a revisão
independente foi registrada e o verificador falha fechado para rollback,
freeze, equivocation, expiração e root não ancorado.

### PR F — candidato imutável (`issue #51`)

Preservar e redesenhar `validate.yml` e `release.yml`. Desacoplar preparação de
candidato de trust/evidência por imports opcionais: rehearsal pode existir sem
evidência, mas promoção/publicação 1.0 falha fechada sem evidência M3. Construir
uma vez, fixar Actions por SHA, conferir ownership/SBOM/provenance/mirrors e
publicar metadata por último. Corrigir ownership e fontes/licenças do protótipo;
todo runtime usado pelo smoke deve pertencer ao candidato ou estar vinculado
por digest imutável.

**Gate:** bytes, checksums, ownership, SBOM e provenance são idênticos entre
preparação e promoção; nenhum destino é sobrescrito; trust não é importado
implicitamente.

O gate final também exige um handoff de operação TUF registrado por
`.github/workflows/tuf-operation-drill.yml`. O relatório deve estar vinculado ao
catálogo do candidato, conter contexto de operador/host/SLA e comprovar
renovação, expiração simulada e recuperação; suas coordenadas entram no recibo
durável de `1.0.0`.

O workflow final também consulta a lease pública atual com
`monitor_public_tuf.py` e janela de seis horas. O drill histórico não substitui
essa verificação: enquanto a issue de alerta #152 estiver aberta ou a lease
estiver dentro da janela, a promoção permanece `NO-GO`.

O gate final também exige o período de uso do RC por
`.github/workflows/rc-soak.yml`, despachado na ref do commit exato do candidato.
O relatório precisa comprovar sete dias completos, observações diárias verdes,
as cinco condições operacionais e a issue canônica encerrada. `verify-soak`
confere a procedência do run e o artifact imutável; suas coordenadas entram na
seção `soak` do recibo durável de `1.0.0`.

### PR G — Mac M3/arm64 e RC (`issue #54`; fechamento operacional `#147`)

O executor `native_macos_harness.py` e o workflow
`.github/workflows/native-m3.yml` exigem um plano fechado fornecido para o
candidato exato. O checkpoint não contém evidência assinada de release; no
candidato exato, executar o
conjunto completo de clientes, jogos, serviços e lifecycle. Linux, Windows e
macOS Intel permanecem `not-run`/`preview`, com harnesses não bloqueantes.

O RC público foi produzido e promovido no run `31752738047`. A implementação
do fechamento desta frente adiciona os casos M3 restantes, a aceitação pública
e a evidência durável. A rechecagem local v2 baixou os mesmos bytes pelos
endpoints públicos, completou o lifecycle e comprovou a migração real
`0.7.13 → 1.0.0-rc.1`; o recibo está em
[`1.0.0-rc.1-public-acceptance-migration-local-2026-08-14.json`](../releases/1.0.0-rc.1-public-acceptance-migration-local-2026-08-14.json).
O drill técnico TUF local formato 2 também passou renovação, expiração simulada e
recuperação, com contexto de operador/host/SLA, versões por role e chaves efêmeras, em
[`1.0.0-tuf-drill-local-2026-08-14.json`](../releases/1.0.0-tuf-drill-local-2026-08-14.json).
O candidato local `1.0.0-rc.2`, distinto dos bytes do RC público, também passou
os 25 casos M3 reais no Apple M3 Pro; o recibo unsigned/pending está em
[`1.0.0-rc.2-native-preflight-local-2026-08-14.json`](../releases/1.0.0-rc.2-native-preflight-local-2026-08-14.json).
O handoff/artifact do workflow protegido, a custódia produtiva e a abertura do
soak continuam pendentes.

**Gate:** implementação fechada localmente; evidência M3 autenticada para um
novo candidato, smokes registrados e período de uso concluído sem promover
`1.0.0`.

### PR H — promoção `1.0.0` (`issue #149`)

Criar a issue própria somente depois de o RC cumprir o período de uso e todos os
gates. A PR faz apenas promoção/release, sem correção funcional. Exigir trust
válido, evidência M3, bytes idênticos, mirrors convergentes e documentação
coerente.

**Gate final:** aprovação humana explícita, soak protegido concluído, metadata
publicada por último, reversão documentada e nenhum claim de plataforma além da
evidência existente.

## 6. Releases intermediárias e dependências

| Marco | Dependência mínima | Tipo de release |
|---|---|---|
| nova `0.7.x` | somente P0 confirmado | corretiva, PR de release separada |
| `0.8.x`/`0.9.x` | não são requisito deste ciclo | não criar por inércia documental |
| `1.0.0-rc.1` | F e G executadas; aceitação pública v2 e evidência operacional registradas | RC público disponível; soak ainda não formalmente aberto |
| `1.0.0` | H e todos os gates | promoção sem correção funcional |

O RC `1.0.0-rc.1` já foi criado, tagueado e publicado; a materialização desta
implementação não altera seus bytes. Qualquer alteração de produto exige novo
RC e reinício do soak. O mirror `gitlab/main` não é tratado como espelho
confirmado fora da verificação registrada do RC.

## 7. Governança atual e limpeza futura

As instruções históricas de controle abaixo foram substituídas pelo estado
operacional do RC. As ações de limpeza continuam exigindo nova verificação e
não podem remover evidência:

1. não criar a tag histórica `audit/stabilize-1.0-2026-08-06` nem tratá-la como
   autoridade;
2. revalidar ancestralidade e apagar somente refs já integradas, depois de
   registrar os SHAs e preservar refs ligadas ao RC, à evidência e ao TUF;
3. manter o backlog operacional nas issues [#143–#152](https://github.com/x86dx2/x86qw/issues/143),
   com a limpeza de branches em [#150](https://github.com/x86dx2/x86qw/issues/150)
   e a avaliação de imutabilidade host-level em
   [#151](https://github.com/x86dx2/x86qw/issues/151);
4. manter `codex/control-maps` e os checkpoints históricos fora da linha de
   release até decisão própria.

As issues históricas #68 e #70 foram encerradas por já estarem concluídas; não
devem voltar a ser usadas como backlog de 1.0.



## 8. Riscos, rollback e fronteiras de aprovação

| Risco | Controle | Evidência exigida |
|---|---|---|
| checkpoint ser tratado como merge | comparação hunk a hunk e base `origin/main` | diff de cada PR e ancestry revalidada |
| compatibilidade virar suporte | estados separados e evidência do candidato | catálogo, status e smoke correspondentes |
| trust de fixture chegar à produção | custódia e revisão externa obrigatórias | cerimônia, fingerprints e expiração |
| rebuild alterar bytes | build once, hashes e promoção sem overwrite | checksums, SBOM, provenance e mirrors |
| docs divergir do código | status/roadmap/plano têm papéis distintos | `verify`, links e revisão humana |
| release misturar implementação | PR de release separada | issue, aprovação e changelog |

O rollback de uma fase documental é apenas reverter sua PR antes do merge; não
se reescrevem bundles nem se apagam evidências. Uma promoção de artefato já
publicada é imutável e exige procedimento de incidente, não `git revert` de
bytes. Qualquer ação remota, criptográfica ou destrutiva para fora das fases
descritas para até obter autorização explícita.

## 9. Definição de concluído

A jornada só pode ser declarada concluída quando:

- A–G têm issues, branches, contratos, evidência e gates aprovados;
- `1.0.0-rc.1` passou pelo período de uso com trust e evidência M3 válidos;
- H promove somente bytes idênticos, mirrors convergentes e metadata coerente;
- suporte por plataforma corresponde à evidência nativa do candidato;
- `PROJECT-STATUS.md`, `ROADMAP.md`, README e notas de release apontam para o
  mesmo estado;
- existe um artifact protegido de soak, com issue encerrada e coordenadas
  conferidas no recibo durável final;
- nenhuma alteração funcional é escondida em uma PR de promoção.

Para este checkpoint, a conclusão é mais estreita: os contratos locais passam,
o publisher não recompila, a fixture pública 0.7.13 existe, os workflows não
possuem placeholders deliberados, a aceitação local v2 percorreu os endpoints
públicos e a migração real preservou os dados, e o drill técnico TUF local
passou renovação, expiração simulada e recuperação. O harness M3 local passou
`25/25` no preflight privado `1.0.0` da revisão de produto
`f2cadeff3261ce07f7c9490313db1aa69e417fa2`. O RC público já foi promovido,
mas a implementação local ainda não é aprovação de `1.0.0`: faltam artifact e
handoff protegido, cobertura pública/protegida dos casos M3 restantes,
operação TUF de produção, soak e novo candidato final após esses gates.

## 10. Validação desta materialização

O gate da implementação local é executado no worktree; ele não substitui os
gates remotos e nativos:

1. confirmar que a base canônica ainda resolve para
   `origin/main`; registrar o SHA retornado no relatório da auditoria; se avançar, repetir a
   auditoria antes de editar;
2. executar `git diff --check` e revisar os links Markdown locais, resolvendo
   cada destino a partir do diretório do documento e ignorando somente URLs
   externas e âncoras;
3. confirmar a distinção entre baseline pública, checkout local, TUF técnico e
   operação TUF;
4. executar `git diff --check`, `manage.py verify --no-tests`, os testes
   portáveis e os testes nativos com as permissões apropriadas;
5. revisar o `HEAD` local contra `origin/main`, mantendo separado o
   trabalho histórico já publicado do candidato atual; registrar os recibos
   locais de aceitação pública e drill TUF sem tratá-los como handoffs
   protegidos.

Esse gate não promove `1.0.0` nem altera a release RC existente. A execução
nativa M3, a custódia TUF, o catálogo público e o soak continuam dependentes de
entradas protegidas e não são inferidos localmente.
