# Grok Build — orquestração local do x86QW

Configuração versionável para iniciar a sessão principal como Orchestrator e delegar somente aos papéis `x86qw-*`.

Não use modo irrestrito (`--yolo`, `--always-approve`, bypass).

## Como iniciar o Orchestrator

```text
agent: x86qw-orchestrator
model: grok-4.6
reasoning effort: xhigh
permission mode: default
```

Na TUI: `/config-agents` e escolha `x86qw-orchestrator`, ou:

```sh
grok --agent x86qw-orchestrator --model grok-4.6
```

Confirme o reasoning effort `xhigh` na sessão. Checkout: o diretório principal do repositório, não um worktree de Maker.

Se o Grok Build não listar os papéis recém-adicionados, reinicie a sessão (`RESTART_REQUIRED`).

## Matriz de papéis

| Papel | Modelo | Effort | Capability | Isolamento |
|---|---|---|---|---|
| Orchestrator | Grok 4.6 | xhigh na sessão | confirmação padrão | checkout principal |
| Explorer | Grok 4.6 | medium | read-only | none |
| Architect | Grok 4.6 | xhigh | read-only | none |
| Maker | Grok 4.6 | high | all | worktree |
| Critical Maker | Grok 4.6 | xhigh | all | worktree |
| Test Runner | Grok 4.6 | high | execute | cwd do Maker |
| Contract Checker | Grok 4.6 | xhigh | read-only | cwd do Maker |
| Security Checker | Grok 4.6 | xhigh | read-only | cwd do Maker |
| Release Checker | Grok 4.6 | xhigh | read-only | cwd do Maker |
| Finalizer | Grok 4.6 | xhigh | read-only | cwd do Maker |

Autoridade de modelo, effort, capability e isolamento: `.grok/roles/*.toml`.
Prompts: `.grok/prompts/*.md`.
Agente da sessão raiz: `.grok/agents/x86qw-orchestrator.md`.
Agentes filhos (tipos de spawn + herança MCP): `.grok/agents/x86qw-*.md`.

## Fluxo

```text
Orchestrator
  → Explorer / Architect (read-only)
  → Maker ou Critical Maker (worktree)
  → Test Runner (cwd do Maker)
  → Contract / Security / Release Checker (cwd do Maker)
  → Finalizer
```

Maker, Checker e Finalizer são sessões distintas. Não use `resume_from` entre Maker e Checker. Não use `general-purpose` na migração.

Gates críticos exigem Checker Codex externo quando o plano vigente pedir. Outro Grok não substitui isso.

## MCP

Servidor de memória real: `ai-memory`.
Filhos herdam somente `ai-memory` (`mcpInheritance.named`). Não herdam `chrome-devtools`, `stitch` nem `tasks`. Capability mode não torna MCP mutável em read-only.

## ai-memory

Continuidade e registro, não scheduler. Marker: `.ai-memory.toml`. Política humana: `AGENTS.md` e `docs/go-migration/ORCHESTRATION-PROTOCOL.md`.

## Templates

- `docs/go-migration/templates/ASSIGNMENT.md`
- `docs/go-migration/templates/SUBAGENT-REPORT.md`
- `docs/go-migration/templates/EVIDENCE-PACK.md`
