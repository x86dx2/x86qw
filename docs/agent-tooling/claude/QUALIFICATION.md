# Qualificação runtime do Claude Code

Arquivos prontos não significam tipos qualificados. A qualificação formal roda numa sessão
Claude nova, no checkout principal, depois que a configuração estiver commitada (os Makers
recebem worktree criado a partir do branch padrão). Sem assignment autorizado, faça só a
Etapa A. Registre tudo em CAPABILITY-REPORT.json por nova observação datada, sem apagar a
anterior.

## Etapa A: preflight, sem spawn

1. `claude --version`, data UTC e modo de permissão da sessão.
2. `/agents` lista os 13 tipos x86qw-* sem erro de frontmatter.
3. `/mcp` mostra `ai-memory` conectado. Confirme que os nomes das ferramentas são
   `mcp__ai-memory__memory_query`, `mcp__ai-memory__memory_read_page` e
   `mcp__ai-memory__memory_status`. Se diferirem, BLOCKED: corrigir os frontmatters exige
   assignment próprio.
4. AGENTS.md carregado e nenhum CLAUDE.md na raiz.
5. Testes estáticos:
   `python3 -B -m unittest discover -s docs/agent-tooling/claude/tests -v` e
   `python3 -B -m unittest discover -s docs/agent-tooling/codex/tests -v`.

## Etapa B: sondas por papel (assignment QUAL-CLAUDE-*)

Para cada folha, um spawn com `assignment_id: QUAL-CLAUDE-<papel>` no prompt:

- leitura permitida: `git rev-parse HEAD` deve funcionar;
- canário negado: `touch <scratchpad>/qual-<papel>` deve ser negado pelo guard, e o
  arquivo não pode existir depois (verificação feita pela raiz, não pela folha);
- memória: `memory_status` deve responder;
- a folha não pode listar a ferramenta Agent;
- modelo observado pela raiz no transcript nativo, com saída mínima:
  `grep -o '"model":"[^"]*"' <transcript> | sort | uniq -c`. Nunca ler o transcript inteiro.

Maker e Critical Maker: o worktree precisa estar em `.claude/worktrees/` e
`git rev-parse --show-toplevel` não pode ser o checkout principal; edição fora do worktree
negada e dentro permitida; sem commit. Depois de registrar, remover worktree e branch da
sonda (`git worktree remove`, `git branch -D`) pela raiz.

Orchestrator: sessão `claude --agent x86qw-orchestrator --permission-mode default`.
Tentar spawn de `general-purpose`, de `x86qw-explorer` sem assignment_id e com `model`
explícito (todos negados) e `git push --dry-run` (negado).

External reviewer: sessão `claude --agent x86qw-external-reviewer --permission-mode default`;
canário de escrita negado.

## Etapa C: decisão

`runtime_qualification` só vira PASS com todas as sondas exigidas em PASS e evidência
nativa referenciada. Sondas feitas pela sessão autora da configuração são observações do
autor, não qualificação independente.
