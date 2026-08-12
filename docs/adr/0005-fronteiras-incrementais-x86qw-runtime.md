# ADR 0005 — Fronteiras incrementais em `x86qw_runtime`

- **Estado:** aceita e implementada no código da PR 6; revisão aberta; não publicada
- **Data:** 2026-08-04
- **Issue:** [#52](https://github.com/x86dx2/x86qw/issues/52)
- **Baseline:** `00098330e5833ba2c83c7121272d644c2a204a7b`
- **HEAD analisado:** `29d76a48721190aad1203d0986a31d839d62070e`
- **Recorte documentado:** `23194fd..29d76a4`

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
6. o zipapp inclui somente o runtime, os entrypoints consumidos e as projeções
   declarativas necessárias, sem incorporar `maintenance/`; cada membro deve
   declarar origem, consumidor e contrato em manifesto independente;
7. a extração continua pertencendo à fronteira única
   `x86qw_runtime.io.archive`, aceita anteriormente no
   [ADR 0002](0002-fronteira-unica-de-arquivos.md); esta PR não cria um scanner
   paralelo nem altera seus limites;
8. códigos, textos, formatos persistidos e comportamento público permanecem
   estáveis durante esta refatoração. Mudanças públicas de SemVer, schemas,
   códigos de saída e JSON pertencem à PR 7.

## Ownership implementado no HEAD analisado

| Área | Fronteira canônica | Estado em `29d76a4` |
|---|---|---|
| Versão e erros | `x86qw_runtime.versioning`, `x86qw_runtime.errors` | consumidores compartilham tipos e parser atuais |
| Download | `x86qw_runtime.io.downloader` | manutenção é fachada; zipapp leva somente o runtime |
| Arquivos | `x86qw_runtime.io.archive` | contrato seguro anterior preservado como fronteira única |
| Persistência e filesystem | `x86qw_runtime.io.atomic`, `io.metadata`, `io.paths`, `io.private_fs`, `io.managed_files`, `io.personal_files`, `io.quarantine` | gravação durável, objetos privados, identidade, arquivos pessoais e remoção reversível centralizados |
| Catálogos | `x86qw_runtime.catalogs` | modelos/loaders canônicos e carregamento tardio |
| Estado persistido | `x86qw_runtime.state`, `receipts`, `migrations` | parsing, serialização e migração atuais extraídos sem mudar formato |
| Transações | `x86qw_runtime.transaction` | componentes, clientes, geração da CLI, PAKs, defaults, repair, migrações, cleanup e uninstall retêm inversos até o resultado lógico final |
| UI | `x86qw_runtime.ui` | menu, console e parser canônicos compartilhados; manager permanece raiz de composição do grafo de comandos |
| Gameplay | `x86qw_runtime.gameplay` | modelos, catálogos e planejamento extraídos; ciclo com manager removido |
| Plataforma | `x86qw_runtime.platform.{display,host,locking,macos,processes,python_runtime,windows_acl}` | display, cache/variantes, preferências macOS, Python, mutex, identidade, encerramento e ACL pertencem aos adapters runtime |
| Sessão da instalação | `x86qw_runtime.session_control` | lock, ownership e reclamação conservadora são canônicos |
| Supervisor | `x86qw_runtime.supervisor.{core,models,posix_guardian,readiness,sessions}` | lifecycle, readiness, journal, gate de grupo POSIX e recuperação após crash centralizados |
| Artefato instalado | `maintenance/inventory/installer-runtime-members.json` | 56 membros com origem, consumidor e contrato; a projeção do builder é derivada do manifesto e conferida contra o zipapp |

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
- o zipapp deixa de incorporar módulos de manutenção e fachadas não consumidas;
- um manifesto declarativo passa a declarar cada membro instalado e seu
  consumidor, gera a projeção do builder e rejeita divergência com o ZIP;
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

A transação comum melhora a composição de mudanças: o estado pai e a
verificação final podem reverter uma subtransação concluída enquanto seu
staging ainda existe. Isso já abrange a geração da CLI, os PAKs preservados,
defaults, reparos e migrações pessoais/metadata. Ao mesmo tempo, uma falha
depois de `replace` que não permite comprovar `fsync` é relatada como efeito
comprometido e preservado para recuperação, não como rollback completo.

O zipapp do HEAD analisado possui exatamente 56 membros: 44 módulos canônicos
de `x86qw_runtime`, quatro entrypoints/fachadas no topo, duas projeções KTX e
seis membros gerados. O manifesto é a fonte declarativa consumida pelo
builder, não uma segunda lista copiada no código. O teste compara a projeção
derivada com o arquivo realmente produzido.

## Riscos residuais e condição de fechamento

Esta decisão declara concluído o recorte de código da PR 6, mas não sua revisão.
Os entrypoints continuam sendo raízes de composição extensas; isso é permitido
desde que não voltem a possuir HTTP, ZIP, persistência, plataforma ou regras de
domínio já extraídas. Logs de execução são append-only e não participam de
rollback; mutações duráveis de payload, metadados e configurações gerenciadas
usam planos, quarantine ou inversos identificados. A finalização do quarantine
remove arquivos regulares e diretórios vazios após descritor, identidade e
rename exclusivo para nome privado imprevisível no POSIX, e pelo handle validado
no Windows. Links e tipos especiais são preservados com diagnóstico. Como o
POSIX não oferece `unlink`/`rmdir` condicional por inode, código hostil com a
mesma identidade do usuário permanece fora da fronteira de confiança, assim
como já documentado para ACLs privadas.

No HEAD `29d76a4`, 1.190 testes de manutenção passaram com 37 skips explícitos,
e os 5 testes do site passaram. Eles comprovam localmente o ownership, o grafo
sem ciclo, o zipapp de 56 membros e as corridas adversariais de rollback,
quarantine, locks, journals e arquivos pessoais. A matriz Linux, macOS e
Windows do snapshot enviado à PR continua necessária e não substitui os smokes
nativos do candidato previstos no PR 11. A issue #52 foi encerrada pela PR 62;
a revisão nativa da estabilização 1.0 continua pendente. A release pública
`0.7.1` não é alterada e esta decisão não autoriza
publicação.
