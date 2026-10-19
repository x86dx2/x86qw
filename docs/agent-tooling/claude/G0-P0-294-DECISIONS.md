# Decisões do Release Owner: P0 #294 e continuidade da G0

Registro de 2026-10-19 na sessão Claude Code 2c751897-7824-465b-8d83-3f6626cd801b.
A sessão rodou em modo bypass, fora do perfil `x86qw-orchestrator`; por isso fez só
análise e este registro. A execução cabe à sessão
`claude --agent x86qw-orchestrator --permission-mode default`.

## Fatos verificados antes da decisão (2026-10-19, cerca de 15:54Z)

- Issue #294 "TUF public lease requires attention" (P0, owner-only) aberta em
  2026-09-04T05:58:16Z e atualizada pelo monitor em 2026-10-19T10:50:22Z.
- `tuf-monitor.yml` falhou em todas as 200 execuções consultadas (a mais antiga, de
  2026-09-10, já falhava). Run 39240290782: `[ERRO] cadeia TUF pública não autenticou:
  falha de trust ao autenticar catálogo: timestamp.json is expired`.
- Metadata pública em `https://qw.x86.com.br/api/v1/trust/metadata/`:
  - timestamp v44, expirou em 2026-10-02T00:37:28Z;
  - `43.snapshot.json` e `43.targets.json` v43, válidos até 2026-12-01T00:37:27Z;
  - root v1 do checkout, válida até 2027-08-10T10:44:33Z (`2.root.json` responde 404).
- Pelo procedimento G0: `P0_BLOCKED` e `HOLD_TUF_LEASE_UNHEALTHY`. A recuperação exige
  publicação, portanto `PLAN_DEVIATION_TUF_PUBLICATION_REQUIRED`. Despachar o monitor
  não resolve: o problema é expiração, não cadência.

## Correção de registro

`G0-ORCHESTRATION-TRANSFER.md` (commit ffeefb8) e a página ai-memory
`status/go-migration-current.md` (2026-10-19) afirmaram "P0 aberto = 0". Isso está errado
desde 2026-09-04T05:58:16Z. As fotografias de 2026-09-03 (STATUS.json e packet da
candidata, páginas daquela data) eram válidas no instante em que foram tiradas, porque a
#293 fechou em 2026-09-03T16:47:13Z; elas não são reescritas. O packet Codex 6ac3fd6
afirma `p0_open=[]` e lease saudável e precisa ser refeito antes de qualquer checagem
externa.

## Pergunta e resposta

A sessão propôs, em ordem: recuperar o TUF (#294); corrigir os registros; qualificar os
papéis Claude necessários; merge do PR #307 depois dos checks verdes e da correção;
reconciliar a candidata com a `main` e refazer o packet; checagem externa por Codex ou
por PLAN_DEVIATION. Pediu ao Release Owner quatro decisões:

1. autorizar a recuperação do timestamp, o que significa publicar metadata TUF;
2. autorizar a correção do doc e da página de status no ai-memory;
3. autorizar a rodada de qualificação dos papéis Claude;
4. para a checagem externa da G0: esperar o Codex ou registrar PLAN_DEVIATION.

Resposta literal do Release Owner: "tudo aprovado".

## Alcance aprovado

1. Recuperação TUF (`PLAN_DEVIATION_TUF_PUBLICATION_REQUIRED` aprovado, escopo restrito).
   - Cobre: despachar workflows protegidos existentes e não modificados, somente para a
     role timestamp, de v44 para v45, conforme `docs/runbooks/trust-metadata-operations.md`
     e `docs/runbooks/release.md`. A v44 saiu em 2026-09-02 pela cadeia
     `tuf-metadata-handoff.yml` (run 33578257528) → `tuf-timestamp-renewal.yml`
     (33578350125) → `site-projection-repair.yml` (33578437072); o último
     `tuf-timestamp-publish.yml` bem-sucedido é de 2026-08-27 (33102330910). Antes de
     qualquer despacho, um Explorer confirma a cadeia exata e os inputs, e o Orchestrator
     apresenta cada workflow e seus inputs ao Release Owner. Workflow fora desses quatro:
     pare e peça ao Release Owner que o nomeie. Cada despacho e a aprovação do ambiente
     `release` são feitos pelo humano com `!`, porque o guard só permite `gh` de leitura.
   - Não cobre: alterar workflows, gates, runbooks ou ferramentas para um check passar;
     snapshot, targets ou root; chaves, secrets ou ambientes; "estender" a v44; reduzir
     threshold; reutilizar versão; fechar a #294 por narrativa.
   - Parada: se o caminho de renovação recusar partir de um timestamp já vencido, ou se
     ocorrer qualquer condição de parada do runbook, pare, preserve a evidência e peça
     nova decisão (novo PLAN_DEVIATION).
   - Fechamento da #294: um run agendado (`schedule`) do monitor verde depois da
     publicação e um Release Checker independente. Fechar a issue é ação humana.
   - Este arquivo é o registro no Git da aprovação do mantenedor único sob o ADR 0007,
     que também pede o registro em comentário (na #294 ou no PR). Não é revisão
     independente.
2. Correção deste registro e da página de status: feita nesta sessão, com commit local na
   `main`. Push pelo humano.
3. Qualificação Claude: Etapas A e B de `QUALIFICATION.md` na sessão Orchestrator nova,
   primeiro o Release Checker (necessário para fechar a #294) e depois o Maker
   (`worktree.baseRef: head` ainda não observado).
4. PR #307: merge depois dos checks verdes e desta correção, como ação humana.
5. Reconciliação da candidata com a `main` (conflito add/add em `AGENTS.md`, templates
   citados por `.grok/README.md`) e novo packet: assignments próprios, depois da #294.

## Pendente: "tudo aprovado" não decide

- Checagem externa da G0: esperar o Codex e registrar PLAN_DEVIATION para
  `x86qw-external-reviewer` são alternativas. Exige escolha explícita; até lá o Checker
  externo continua Codex.
- `lease_hours` da nova lease: o workflow usa 168 por padrão e o runbook limita o
  owner-only a 30 dias. A nova expiração precisa ficar antes da do snapshot
  (2026-12-01T00:37:27Z). Escolha no despacho.
- Próximos prazos TUF, fora desta aprovação: targets antes de 30 dias restantes (até
  2026-11-01T00:37:27Z) e snapshot antes de 14 dias restantes (até 2026-11-17T00:37:27Z).
  Sem uma cadência de renovação, a lease volta a vencer.

## O que não muda

`GATE=G0`, `STATE=IN_PROGRESS`, `MIGRATION_BASELINE_SHA=null`, `G1_AUTHORIZED=false`,
audiência owner-only, runtime Python. `AWAIT_EXTERNAL_CODEX_CHECK` só volta a ser o
próximo passo depois da #294 e de um packet novo. Nenhum push, merge, release, deploy,
mudança de audiência, chave, credencial ou supported foi autorizado além do listado acima.
