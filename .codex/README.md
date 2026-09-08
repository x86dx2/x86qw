# Agentes Codex do x86QW

Infraestrutura local para Codex CLI 0.153.4, ai-memory 2.0.1 e escopo x86/x86qw.
Consulte [POLICY](../docs/agent-tooling/codex/POLICY.md), a matriz
[MODEL-ROUTING](../docs/agent-tooling/codex/MODEL-ROUTING.json) e o relatório observado
[CAPABILITY-REPORT](../docs/agent-tooling/codex/CAPABILITY-REPORT.json).

A raiz recebe configuração de .codex/config.toml e um prompt explícito.
Os TOMLs em agents/ são tipos de spawn para filhos. Não existe um perfil raiz
criado pelo nome de uma thread. /agent inspeciona threads; não prova seleção de tipo.

## Qualificação antes do uso

Leia [QUALIFICATION](../docs/agent-tooling/codex/QUALIFICATION.md).
Arquivos prontos não significam filhos qualificados. A sessão que escreveu estes
arquivos mantém overrides e ferramentas anteriores; não deve fazer spawn privilegiado.
Use uma nova sessão, após saída normal do run gerenciado, sem flags de sandbox escritor:

```sh
cd /Users/x86/git-projects/x86qw-codex-agents
ai-memory run --workspace x86 --project x86qw --workstream x86qw-codex-agents-bootstrap --fresh codex --strict-config "Atue somente como Bootstrap Qualifier. Leia docs/agent-tooling/codex/QUALIFICATION.md e execute a Etapa B no escopo ali descrito. Não regenere arquivos, não execute G0 e não transfira a orquestração. Primeiro verifique a configuração efetiva e a superfície de ferramentas."
```

--fresh abre outra sessão nativa no MESMO workstream. Para retomar uma sessão
incompleta, use o mesmo comando sem --fresh e sem --new. Confirme primeiro
`ai-memory workstreams --workspace x86 --project x86qw --json`.
Não lance um segundo writer enquanto o run anterior estiver ativo. Encerre o CLI
normalmente (/quit na interface que o reconhecer, ou Ctrl-D conforme a ajuda)
e aguarde o wrapper devolver o terminal e importar o transcript. Não mate o wrapper
para fabricar encerramento. A busca pós-saída está em QUALIFICATION.md.

## Raiz Orchestrator futura

Somente depois da qualificação e de autorização humana do assignment:

```sh
codex --strict-config -C /Users/x86/git-projects/x86qw-codex-agents "Atue como Orchestrator x86QW. Leia .codex/instructions/orchestrator.md e o assignment humano autorizado. Confira configuração efetiva, identidade e qualificação antes de qualquer spawn. Sem assignment autorizado, faça somente preflight."
```

Sem --agent inventado e sem trocar modelo/effort no spawn. A matriz normativa é:

| Papel | Modelo | Effort | Sandbox alvo |
|---|---|---|---|
| Raiz | gpt-6-astra | xhigh | workspace-write |
| Explorer | gpt-5.6-sol | medium | read-only |
| Architect | gpt-6-astra | xhigh | read-only |
| Inventory Auditor | gpt-6-astra | xhigh | read-only |
| Maker | gpt-6-astra | high | workspace-write exclusivo |
| Critical Maker | gpt-6-astra | xhigh | workspace-write exclusivo |
| Test Designer | gpt-6-astra | high | read-only |
| Test Runner | gpt-5.6-sol | low | read-only no código |
| Contract / Security / Release Checker | gpt-6-astra | xhigh | read-only |
| Finalizer | gpt-6-astra | xhigh | read-only |
| Gate Auditor (inativo) | gpt-6-astra | max | read-only |

Gate Auditor fica em optional/*.example até parser, catálogo e execução qualificarem
max. xhigh não é max. Disponibilidade no catálogo não atesta o backend.

## Revisão top-level independente

Um revisor externo usa uma sessão nova, sem resume/fork/conversa do autor.
Para revisar estes bytes locais no worktree real:

```sh
codex --strict-config -C /Users/x86/git-projects/x86qw-codex-agents -s read-only -a never -c 'approvals_reviewer="user"' -c 'agents.enabled=false' -c 'features.multi_agent_v2=false' "Atue como Checker externo independente. Leia .codex/instructions/external-reviewer.md. Faça somente preflight de revisão até receber o assignment com manifesto e escopo; não aprove a G0."
```

Para outra candidata, indique seu worktree real e provisione os mesmos filtros de
ferramentas por configuração reconhecida, sob autorização própria. Não basta trocar cwd.
O comando acima é uma rota proposta; sandbox negativo e catálogo precisam de prova
antes de qualificar a revisão. Nunca chame filho do autor de auditor externo.

## Inspeção

Use `codex --version`, `codex --strict-config doctor --json` (redigido),
`codex debug models --bundled`, e a API nativa config/read com includeLayers=true
e cwd correto. Compare origens e disabledReason; exigências administradas vêm de
configRequirements/read. Não imprima respostas completas com endpoints de autenticação,
headers ou tokens. /status e /mcp são auxiliares; o catálogo resolvido de ferramentas
e metadata nativa dos filhos são a evidência necessária.

A configuração usa sandbox_mode; não misture default_permissions/permissions.
workspace-write permite escrita dentro do workspace sem aprovação por arquivo.
.config não é allowlist do assignment. .codex/, .agents/ e Git podem ser protegidos.
Raiz/Makers usam on-request + user; never nos leitores significa falhar sem elevação.
Flags e escolhas vivas do pai podem sobrepor defaults do filho.

## Autorizações futuras

Bootstrap não autoriza G0, G1, takeover, commit, push, PR, merge, workflow, release,
deploy, TUF, mudança de audiência/supported, credenciais ou instalação pessoal.
Nova ferramenta mutável, nova root ou ampliação de assignment exige autorização.
Não altere configuração global, hooks, store ou versões para concluir a qualificação.
