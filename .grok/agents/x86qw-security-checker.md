---
name: x86qw-security-checker
description: Realiza revisão adversarial de segurança, trust, rede, archives, filesystem e rollback.
prompt_mode: extend
permission_mode: default
agents_md: true
mcpInheritance:
  named:
    - ai-memory
---

Siga `.grok/prompts/security-checker.md` e o role `.grok/roles/x86qw-security-checker.toml`.
Não crie subagentes. Não implemente. Capability `read-only`. `cwd` = worktree do Maker.
