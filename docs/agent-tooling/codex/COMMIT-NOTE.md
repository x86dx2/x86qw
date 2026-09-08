# Continuação autorizada após o bootstrap

O usuário solicitou a coexistência dos dois harnesses neste worktree e, depois,
autorizou o commit local da entrega. Os 29 arquivos de `.grok/` foram copiados
fielmente do checkout principal; os hashes da cópia e da origem foram comparados.
Nenhum conteúdo Grok original foi alterado.

O commit inclui `.codex/`, `.grok/`, `AGENTS.md`, o marker `.ai-memory.toml` e
`docs/agent-tooling/codex/`. A autorização posterior permite este commit local;
não autoriza push, merge, publicação, migração ou aprovação de gate.

`BOOTSTRAP-REPORT.md`, `CAPABILITY-REPORT.json` e o manifesto preservam a fotografia
da entrega inicial sem commit e antes da cópia Grok. Este registro acrescenta os
dois passos posteriores, sem reescrever essa evidência. O manifesto inicial não
inclui a cópia Grok nem esta nota; o commit Git identifica a entrega ampliada.

Os seis testes estáticos passaram novamente antes do commit. A qualificação runtime
continua pendente, o HOLD externo da G0 permanece e G1 não está autorizada.
O baseline da migração continua nulo. Este commit não constitui aprovação do autor
sobre a qualificação dos agentes ou sobre qualquer candidata da migração.

## Correção posterior do roteamento de inicialização

Após o commit inicial `9a7a916`, o usuário pediu que `AGENTS.md` refletisse
explicitamente a tierização de cada cliente. A seleção por contexto nativo passou
ao início das regras do projeto: Grok segue `.grok/`; Codex segue `.codex/`.
A regra MCP foi separada por harness e a designação fixa de toda sessão como
Bootstrap Configurator foi substituída pelo papel do assignment atual.
Os modelos, efforts e configurações dos dois harnesses permanecem inalterados.
O manifesto inicial continua sendo a fotografia anterior a esta correção.

## Preparação para sincronização da main

O usuário autorizou integrar na main, sincronizar os remotos e remover worktrees
auxiliares. As candidatas G0 em HOLD não fazem parte desta integração.
Os patches do bootstrap foram encapsulados em arquivos `.patch.json`: o campo
`patch` contém o diff UTF-8 integral, com SHA-256 dos bytes originais. A conversão
foi verificada byte a byte e evita que espaços sintáticos dos patches sejam
interpretados como whitespace de código pela CI. A checagem CI permanece intacta.
Os nomes antigos nos registros históricos continuam identificando as fotografias
originais; o conteúdo pode ser recuperado do JSON ou do commit `7aff41e`.
