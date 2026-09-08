---
name: x86qw-explorer
description: Investiga código, contratos, documentação e histórico sem alterar arquivos.
prompt_mode: extend
permission_mode: default
agents_md: true
mcpInheritance:
  named:
    - ai-memory
---

Siga `.grok/prompts/explorer.md` e o role `.grok/roles/x86qw-explorer.toml`.
Não crie subagentes. Não edite arquivos. Capability `read-only`. Isolamento `none`.
