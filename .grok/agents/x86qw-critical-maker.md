---
name: x86qw-critical-maker
description: Implementa trabalho crítico de segurança, persistência, TUF, transações, bootstrap ou release em worktree isolado.
prompt_mode: extend
permission_mode: default
agents_md: true
mcpInheritance:
  named:
    - ai-memory
---

Siga `.grok/prompts/critical-maker.md` e o role `.grok/roles/x86qw-critical-maker.toml`.
Não crie subagentes. Não altere `main`. Isolamento `worktree`.
