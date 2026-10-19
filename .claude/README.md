# Claude Code: agentes do x86QW

Terceiro harness do x86QW, ao lado de Grok Build (`.grok/`) e Codex (`.codex/`), sem
alterar a configuração deles. Política: [POLICY](../docs/agent-tooling/claude/POLICY.md).
Matriz: [MODEL-ROUTING](../docs/agent-tooling/claude/MODEL-ROUTING.json).
Observações: [CAPABILITY-REPORT](../docs/agent-tooling/claude/CAPABILITY-REPORT.json).
Qualificação: [QUALIFICATION](../docs/agent-tooling/claude/QUALIFICATION.md).

## Como iniciar

Orchestrator, somente com assignment humano autorizado, no checkout principal:

```sh
claude --agent x86qw-orchestrator --permission-mode default
```

Checker externo top-level, em sessão nova sem herdar conversa do autor:

```sh
claude --agent x86qw-external-reviewer --permission-mode default
```

Não use `--dangerously-skip-permissions`, bypass ou auto-aprovação para trabalho do projeto.
Sem assignment, a sessão faz apenas preflight. Desde 2026-10-19 o Claude Code orquestra
a G0 (../docs/agent-tooling/claude/G0-ORCHESTRATION-TRANSFER.md); o Checker externo
continua Codex.

## Matriz de papéis

| Papel | Modelo | Effort | Acesso | Guard | Isolamento |
|---|---|---|---|---|---|
| Orchestrator (top-level) | Claude Opus 5.5 | xhigh | raiz, default | orchestrator | checkout principal |
| External reviewer (top-level) | Claude Opus 5.5 | xhigh | leitura | readonly | sessão nova |
| Explorer | Claude Sonnet 5.5 | medium | leitura | readonly | none |
| Architect | Claude Opus 5.5 | xhigh | leitura | readonly | none |
| Inventory Auditor | Claude Opus 5.5 | xhigh | leitura | readonly | none |
| Maker | Claude Opus 5.5 | high | escrita no worktree | maker | worktree |
| Critical Maker | Claude Opus 5.5 | xhigh | escrita no worktree | maker | worktree |
| Test Designer | Claude Opus 5.5 | high | leitura | readonly | none |
| Test Runner | Claude Sonnet 5.5 | low | execução | test | worktree do Maker |
| Contract / Security Checker | Claude Opus 5.5 | xhigh | leitura | readonly | worktree do Maker |
| Release Checker | Claude Opus 5.5 | xhigh | leitura | readonly | none |
| Finalizer | Claude Opus 5.5 | xhigh | leitura | readonly | none |

Tierização confirmada pelo Release Owner em 2026-10-19.

## Enforcement

Quando a sessão pai roda em bypass, acceptEdits ou auto, o `permissionMode` dos filhos é
ignorado. Valem a allowlist `tools` de cada agente e o hook PreToolUse
`hooks/x86qw_guard.py` (fail-closed). Detalhes e limites em POLICY.md.

## Fluxo

```text
Orchestrator
  → Explorer / Architect / Inventory Auditor / Test Designer (leitura)
  → Maker ou Critical Maker (worktree isolado, commit local na branch do assignment)
  → Test Runner (worktree do Maker, git status antes e depois)
  → Contract / Security / Release Checker (worktree do Maker, leitura)
  → Finalizer
```

Maker, Checker e Finalizer são sessões distintas. Subagente Claude não é Checker externo.

## Cuidados

- Não crie `CLAUDE.md` na raiz: com ele, o Claude Code deixa de ler AGENTS.md.
- `.claude/worktrees/` é ignorado pelo Git; preserve evidências antes de limpar.
- `settings.local.json` é pessoal e fica fora do Git.
