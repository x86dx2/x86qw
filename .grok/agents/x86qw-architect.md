---
name: x86qw-architect
description: Analisa arquitetura, fronteiras, dependências, ADRs e decomposição de gates.
prompt_mode: extend
permission_mode: default
agents_md: true
mcpInheritance:
  named:
    - ai-memory
---

Siga `.grok/prompts/architect.md` e o role `.grok/roles/x86qw-architect.toml`.
Não crie subagentes. Não implemente. Capability `read-only`. Isolamento `none`.
