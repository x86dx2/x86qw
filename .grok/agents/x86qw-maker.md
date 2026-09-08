---
name: x86qw-maker
description: Implementa uma única issue aprovada em worktree isolado e produz Evidence Pack.
prompt_mode: extend
permission_mode: default
agents_md: true
mcpInheritance:
  named:
    - ai-memory
---

Siga `.grok/prompts/maker.md` e o role `.grok/roles/x86qw-maker.toml`.
Não crie subagentes. Não altere `main`. Isolamento `worktree`.
