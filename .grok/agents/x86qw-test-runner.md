---
name: x86qw-test-runner
description: Executa testes independentes no worktree indicado, sem corrigir a implementação.
prompt_mode: extend
permission_mode: default
agents_md: true
mcpInheritance:
  named:
    - ai-memory
---

Siga `.grok/prompts/test-runner.md` e o role `.grok/roles/x86qw-test-runner.toml`.
Não crie subagentes. Não edite o código revisado. Capability `execute`. `cwd` = worktree do Maker.
