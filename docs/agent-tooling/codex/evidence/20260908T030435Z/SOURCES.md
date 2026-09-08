# Fontes e limites da evidência

Consultadas em 2026-09-08 UTC. Cliente instalado: codex-cli 0.153.4, Git 2.55.0,
Python 3.14.7, Darwin arm64. Não houve instalação ou atualização.

- [Subagentes oficiais](https://learn.chatgpt.com/docs/agent-configuration/subagents):
  standalone TOML, campos obrigatórios, herança e overrides vivos do pai.
- [Config reference](https://learn.chatgpt.com/docs/config-file/config-reference) e
  [config basics](https://learn.chatgpt.com/docs/config-file/config-basic): camadas,
  aprovações, sandbox e precedência. Não copiar profiles/provider/auth para o projeto.
- [Comandos](https://learn.chatgpt.com/docs/developer-commands?surface=cli): orientação
  geral. A ajuda do binário prevalece; strict-config não é suportado por codex debug.
- [MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli): enabled_tools,
  disabled_tools, servidores/plugins e controles de aprovação.
- [Astra](https://developers.openai.com/api/docs/models/gpt-6-astra) e
  [Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol): IDs e esforços.
  Catálogo instalado registra ambos e max; isso não prova execução de papel max.
- [Schema da tag rust-v0.153.4](https://github.com/openai/codex/blob/rust-v0.153.4/codex-rs/core/config.schema.json):
  SHA-256 692da7699367f6f4fbbd46c0021278c1311440bcebf0bcb9b836690c05e56196.
  recognized-schema-keys.json preserva as chaves usadas na validação sem dependência.
  ReasoningEffort é string não vazia: parser sozinho não qualifica o esforço.
- [ai-memory v2.0.1 managed workstreams](https://github.com/akitaonrails/ai-memory/blob/v2.0.1/docs/managed-workstreams.md):
  SHA-256 5beadc7cdf0769b51ef7cecc79589f61858deaebff8bcc6bcc708db21ffb23ed.
  --fresh abre sessão nativa nova no workstream existente; --workstream retoma o nome.
  O wrapper importa os eventos visíveis após a saída, sem atestar raciocínio privado.

Protocolos locais lidos sem checkout de outra branch: .grok/README.md, roles e
agentes relevantes, docs/go-migration/ORCHESTRATION-PROTOCOL.md no checkout principal;
MEMORY-PROTOCOL.md na ref local c2650d2a78147f4cc379d26e95d932c19437be21.
Nada desses arquivos foi copiado para uma falsa MEMORY.md ou alterado.

## Correções durante o bootstrap

1. A primeira aplicação restrita reportou sucesso, mas .codex não persistiu.
   config-after-unpersisted.json permaneceu com valores globais. A aplicação com
   aprovação específica foi verificada por existência, hashes e config/read.
2. Blocos MCP parciais causaram invalid transport em config.toml:24:1.
   configuration-mcp-transport-fix.patch adiciona somente transportes observados.
   config-after-transport-fix.json comprova leitura posterior. Nenhuma credencial copiada.
3. configuration.patch adiciona bloqueio de automação interna e corrige o path do
   template; config-final.json é a observação posterior que prevalece.
4. O diagnóstico debug prompt-input retorna mensagens, não o catálogo de ferramentas.
   Ocorrência de nomes em AGENTS.md não prova registro de agentes; discovery-native.json
   não é resultado de seleção. Nenhum filho foi criado para contornar isso.
5. Config global mudou de xhigh para low entre observações; não foi editada pelo
   bootstrap. A camada de projeto final resolve xhigh e user. Hashes/preflight e
   origens resolvidas preservam as fotografias, sem tratar a mudança como autoria nossa.

Ajuda CLI preservada com whitespace final removido; referências a opções proibidas
nessa ajuda são evidência da interface, não configurações habilitadas. Os relatórios
não contêm tokens/headers, respostas completas de configuração ou históricos nativos.
Os testes de governança são fixtures públicas; sua execução por agentes fica NOT_RUN.
