# Transferência da orquestração da G0 para o Claude Code

Decisão do Release Owner em 2026-10-19, na sessão Claude Code 3393219e, com Grok e Codex
indisponíveis: transferir a orquestração da G0 do Grok Build para o Claude Code.

## O que muda

- O Orchestrator da G0 passa a ser a sessão `claude --agent x86qw-orchestrator
  --permission-mode default`, com assignment humano autorizado e os tipos folha de
  `.claude/agents/`.
- A configuração Grok (`.grok/`) permanece intacta e pode voltar a ser usada por nova
  decisão explícita do Release Owner; abrir o Grok ou o Codex não retoma a orquestração.

## O que não muda

- O Checker externo da G0 continua sendo Codex. Substituí-lo por sessão Claude top-level
  exige PLAN_DEVIATION próprio, não concedido nesta decisão.
- Estado vigente: `GATE=G0`, `STATE=IN_PROGRESS`, `DECISION=AWAIT_EXTERNAL_CODEX_CHECK`.
- Candidata: `go/g0/governance-candidate` em c2650d2a78147f4cc379d26e95d932c19437be21;
  packet 6ac3fd68980255546744b94c57d4b35effe4e36a. Nenhuma candidata foi alterada.
- `MIGRATION_BASELINE_SHA=null`, `G1_AUTHORIZED=false`, audiência owner-only, runtime
  Python, P0 aberto = 0. Nenhum finding é fechado por esta transferência.

## Pontos abertos herdados pelo novo Orchestrator

1. A candidata conflita com a `main` em `AGENTS.md` (blocos de roteamento Codex e Claude
   ausentes na candidata); a integração exige reconciliação em assignment próprio.
2. `.grok/README.md` na `main` referencia `docs/go-migration/templates/`, que só existem
   na candidata.
3. Os sete branches `go/g0/*` existem só localmente; backup ainda não autorizado.
4. Pastas antigas em `~/.grok/worktrees/git-projects-x86qw/` guardam material sem outra
   cópia (variante do plano mestre; STATUS.json e ORCHESTRATION-LEDGER.jsonl antigos);
   preservar antes de limpar.
5. Dois handoffs abertos no ai-memory (Codex e Grok); ler exige aceitá-los, o que os
   consome.
6. Qualificação independente do harness Claude pendente (QUALIFICATION.md).
