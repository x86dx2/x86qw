# Bootstrap Claude Code do x86QW (2026-10-19)

## Autorização

O usuário (Release Owner) pediu em 2026-10-19: "analise todos os pontos e permita que o
claude participe do projeto. Neste momento não tenho codex e nem grok disponível."
Antes, no mesmo dia, autorizou habilitar o ai-memory no Claude Code de forma global
(hooks via `ai-memory install-hooks --apply` e MCP `ai-memory` em escopo user).

A autorização cobre a configuração do terceiro harness. Não cobre commit, push, merge,
escrita de memória durável, remediação da G0, G1, transferência da orquestração da G0,
substituição do Checker Codex, release, deploy, TUF, audiência, supported ou credenciais.
Não é permissão geral para sessões futuras.

## Entrega

- `.claude/agents/`: 11 tipos folha x86qw-* e 2 perfis top-level (orchestrator,
  external-reviewer), com frontmatter uniforme validado por teste.
- `.claude/hooks/x86qw_guard.py`: guard PreToolUse fail-closed (readonly, test, maker,
  orchestrator).
- `.claude/settings.json`: somente `worktree.baseRef: "head"`.
- `.claude/README.md` e `docs/agent-tooling/claude/` (POLICY, MODEL-ROUTING,
  QUALIFICATION, CAPABILITY-REPORT, templates e testes estáticos).
- `AGENTS.md`: linha Claude Code na tabela de harnesses e regras Claude nos blocos de
  roteamento, orquestração, MCP e encerramento. O bloco gerenciado do ai-memory ficou
  idêntico (verificado pelo teste Codex).
- `.gitignore`: `/.claude/worktrees/`.

Nada em `.grok/`, `.codex/`, `docs/agent-tooling/codex/` ou `.ai-memory.toml` foi alterado.

## Decisões de desenho

- Tierização proposta: Claude Opus 5.5 nos papéis complexos e Claude Sonnet 5.5 em
  Explorer e Test Runner, espelhando a divisão Codex. Pendente de confirmação.
- Enforcement por allowlist de ferramentas e hook, porque o `permissionMode` dos filhos é
  ignorado quando a sessão pai roda em bypass, acceptEdits ou auto.
- Nenhum CLAUDE.md na raiz, para o Claude Code continuar lendo AGENTS.md.
- Checker externo Claude existe só como sessão top-level e, para a G0, só com
  PLAN_DEVIATION registrado pelo Release Owner.

## Verificação

- Testes estáticos Claude e Codex: PASS (`python3 -B -m unittest discover -s <dir>/tests`).
- Sondas do autor após reinício (Explorer, Test Runner, Maker): ver CAPABILITY-REPORT.json.
  O Maker ficou BLOCKED porque o worktree saiu de `origin/HEAD` e a configuração não está
  commitada; `worktree.baseRef: "head"` foi adicionado depois e ainda não foi observado.
- Worktree e branch da sonda do Maker foram removidos. Nenhum canário ficou fora do worktree.

## Decisões posteriores do Release Owner (2026-10-19)

- Tierização Claude confirmada.
- Commit local na `main` e push para os remotos autorizados.
- Orquestração da G0 transferida ao Claude Code (G0-ORCHESTRATION-TRANSFER.md); o
  Checker externo continua Codex.
- Atualização do ai-memory autorizada (escopo x86/x86qw).
- Backup dos branches G0 e das pastas Grok não autorizado nesta rodada.
- Test Runner trocado de Sonnet 5.5 para Haiku 4.5 (sem effort), por custo; sonda de
  aderência ao guard aprovada pelo autor após reinício (modelo observado
  claude-haiku-4-5-20251001; negações respeitadas; qualificação independente pendente).

## Pendências

1. Qualificação independente em sessão nova (QUALIFICATION.md), incluindo Orchestrator
   e External reviewer.
