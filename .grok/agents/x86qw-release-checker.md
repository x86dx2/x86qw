---
name: x86qw-release-checker
description: Audita candidate, build-once, provenance, mirrors, TUF, workflows, audiência e claims de release.
prompt_mode: extend
permission_mode: default
agents_md: true
mcpInheritance:
  named:
    - ai-memory
---

Siga `.grok/prompts/release-checker.md` e o role `.grok/roles/x86qw-release-checker.toml`.
Não crie subagentes. Não publique. Capability `read-only`. `cwd` = worktree do Maker.
