# ADR 0005 — Fronteiras incrementais em `x86qw_runtime`

- **Estado:** aceita parcialmente no código da PR 6; issue aberta; não publicada
- **Data:** 2026-08-04
- **Issue:** [#52](https://github.com/x86dx2/x86qw/issues/52)
- **Baseline:** `00098330e5833ba2c83c7121272d644c2a204a7b`
- **Recorte documentado:** `23194fd..49594cd`

## Contexto

As fronteiras de arquivos seguros, filesystem privado e DACL Windows já
existiam em `x86qw_runtime`, mas a CLI instalada ainda concentrava regras de
download, catálogo, persistência, gameplay, plataforma, supervisão e UI nos
entrypoints. Em alguns caminhos, o runtime dependia de `maintenance.tools`, de
modo que o artefato público precisava incorporar código de manutenção para
executar.

Essa inversão de dependências aumentava a superfície do zipapp, favorecia
ciclos de importação e permitia que contratos sensíveis — HTTP, estado,
recibos, readiness e rollback — voltassem a divergir entre manutenção e
runtime instalado.

## Decisão

O x86QW adota uma migração incremental, sem rewrite, para a seguinte direção de
dependências:

```text
entrypoints instalados ─┐
maintenance             ├──> x86qw_runtime
builders                ┘

x86qw_runtime -X-> maintenance
x86qw_runtime -X-> manager/gameplay/services/menu
```

As regras são:

1. `x86qw_runtime` possui os contratos reutilizáveis do produto e não importa
   `maintenance`, `dist` nem os entrypoints instalados;
2. `manager.py`, `gameplay.py`, `services.py` e `menu.py` podem permanecer como
   fachadas de compatibilidade enquanto delegam para o runtime;
3. manutenção consome o runtime; um módulo de manutenção pode ser fachada
   temporária, mas não uma segunda implementação;
4. catálogos são carregados somente pelo primeiro comando que realmente os
   exige; `--help` e `--version` não dependem deles;
5. mutações cobertas usam um plano imutável, preflight, revalidação,
   aplicação ordenada e rollback inverso;
6. o zipapp inclui somente o runtime e as projeções declarativas necessárias,
   sem incorporar `maintenance/`;
7. a extração continua pertencendo à fronteira única
   `x86qw_runtime.io.archive`, aceita anteriormente no
   [ADR 0002](0002-fronteira-unica-de-arquivos.md); esta PR não cria um scanner
   paralelo nem altera seus limites;
8. códigos, textos, formatos persistidos e comportamento público permanecem
   estáveis durante esta refatoração. Mudanças públicas de SemVer, schemas,
   códigos de saída e JSON pertencem à PR 7.

## Ownership implementado no recorte

| Área | Fronteira canônica | Estado em `49594cd` |
|---|---|---|
| Versão e erros | `x86qw_runtime.versioning`, `x86qw_runtime.errors` | consumidores compartilham tipos e parser atuais |
| Download | `x86qw_runtime.io.downloader` | manutenção é fachada; zipapp leva somente o runtime |
| Arquivos | `x86qw_runtime.io.archive` | contrato seguro anterior preservado como fronteira única |
| Persistência atômica | `x86qw_runtime.io.atomic`, `io.metadata`, `io.paths` | gravação durável e primitivas de caminho centralizadas |
| Catálogos | `x86qw_runtime.catalogs` | modelos/loaders canônicos e carregamento tardio |
| Estado persistido | `x86qw_runtime.state`, `receipts`, `migrations` | parsing, serialização e migração atuais extraídos sem mudar formato |
| Transações | `x86qw_runtime.transaction` | instalação e remoção de componentes, clientes e composição com o estado pai cobertas pelos incrementos já integrados |
| UI | `x86qw_runtime.ui` | menu canônico; console e argumentos compartilhados pelos serviços, com migração dos demais consumidores ainda parcial |
| Gameplay | `x86qw_runtime.gameplay` | modelos, catálogos e planejamento extraídos; ciclo com manager removido |
| Plataforma | `x86qw_runtime.platform.processes` | identidade nativa e proteção contra PID reutilizado centralizadas |
| Supervisor | `x86qw_runtime.supervisor` | modelos, readiness e lifecycle de processos centralizados |

## Contratos preservados

- comandos, argumentos, textos e códigos de saída públicos existentes;
- comandos equivalentes e planejamento dos jogos atuais;
- formatos atuais de catálogo, `state.json`, recibos e migrações;
- stable e nightly coexistentes, perfis e componentes instalados;
- biblioteca padrão e Python 3.10 ou superior;
- compatibilidade temporária dos imports usados pelos entrypoints e testes;
- bundles, tag, catálogo e bootstraps públicos da `0.7.1`, imutáveis;
- nenhuma mudança de gameplay, runtime distribuído ou conteúdo dos PAKs.

## Contratos alterados

- ownership de regras reutilizáveis passa dos entrypoints e de
  `maintenance.tools` para `x86qw_runtime`;
- manutenção e builders passam a importar a implementação runtime;
- o zipapp deixa de incorporar módulos de manutenção;
- leitura de catálogo deixa de ocorrer durante comandos que não o consomem;
- componentes e clientes cobertos passam a expor transações compostas, que
  retêm staging até o commit do estado pai;
- falha posterior a uma barreira já promovida é distinguida de falha
  reversível, evitando rollback enganoso de bytes cuja durabilidade ficou
  inconclusiva.

## Consequências

Uma única implementação passa a definir cada contrato migrado. Os testes podem
comparar identidade de símbolos entre fachadas e runtime, inspecionar o zipapp,
provar lazy loading e rejeitar ciclos ou imports invertidos por AST.

A transação comum melhora a composição de mudanças: o estado pai pode reverter
uma subtransação concluída enquanto seu staging ainda existe. Ao mesmo tempo,
uma falha depois de `replace` que não permite comprovar `fsync` é relatada como
efeito comprometido e preservado para recuperação, não como rollback completo.

## Riscos residuais e condição de fechamento

Esta decisão **não** declara a PR 6 concluída. No recorte documentado ainda
faltam, no mínimo:

- aplicar o contrato transacional à instalação/atualização da própria CLI;
- cobrir defaults e artefatos derivados ainda gravados fora da transação;
- converter os chamadores residuais de remoção e as migrações mutáveis sem
  simular reversibilidade inexistente;
- tratar `cleanup`, `uninstall` e `--purge` com plano explícito e recuperação;
- eliminar dependências residuais dos entrypoints em ferramentas do
  repositório e reduzir adaptações de plataforma ainda locais;
- executar a regressão integral e a matriz Linux, macOS e Windows sobre o
  snapshot final da PR.

Os testes focais já obtidos comprovam somente as unidades integradas até
`49594cd`; não substituem a matriz final nem os smokes nativos do candidato. A
issue #52 permanece aberta. A release pública `0.7.1` não é alterada, nenhuma
versão nova é preparada e esta decisão não autoriza publicação.
