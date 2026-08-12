# Fronteiras incrementais de runtime — evidência da PR 6

- **Issue:** [#52](https://github.com/x86dx2/x86qw/issues/52)
- **Baseline inicial:** `00098330e5833ba2c83c7121272d644c2a204a7b`
- **HEAD analisado:** `29d76a48721190aad1203d0986a31d839d62070e`
- **Recorte documentado:** `23194fd..29d76a4`
- **Versão pública preservada:** `0.7.3`
- **Estado:** snapshot histórico integrado pela PR #62 na linha pública `0.7.3`; a trilha PR6 permanece pausada para revisão arquitetural final e validação nativa do candidato 1.0

Esta nota registra somente mudanças commitadas na branch histórica
`agent/runtime-boundaries`. A integração dessa fronteira foi publicada na
`0.7.3`; a matriz nativa do candidato 1.0 e o trabalho reservado às PRs 7–12
não são apresentados como entregues nesta nota.

## Problema e risco confirmados

O runtime instalado dependia de regras distribuídas entre `manager.py`,
`gameplay.py`, `services.py`, `menu.py` e `maintenance.tools`. Isso aumentava o
zipapp, criava ciclos, carregava catálogos antes da necessidade e deixava HTTP,
persistência e lifecycle vulneráveis a implementações paralelas.

A PR 6 introduz fronteiras pequenas e verificáveis em `x86qw_runtime` sem
reescrever a CLI nem mudar seu comportamento público. A decisão arquitetural e
os limites deste recorte estão no
[ADR 0005](../adr/0005-fronteiras-incrementais-x86qw-runtime.md).

## Incrementos commitados

| Commit | Unidade | Resultado observado |
|---|---|---|
| `23194fd` | downloader e erros | downloader limitado passa ao runtime; manutenção vira fachada; entrypoints compartilham erros tipados |
| `dccd7dc` | catálogos, versão e lazy loading | modelos/loaders passam ao runtime; help/version funcionam sem ler catálogo |
| `e378bef` | escrita atômica | writers de estado, journals e arquivos convergem em primitivas duráveis |
| `20298c1` | estado, recibos e migrações | codecs e migração do formato atual saem do manager sem alterar o formato persistido |
| `853e126` | transações de componentes | preflight, revalidação, aplicação e rollback inverso passam a formar um contrato explícito |
| `e5d443d` | UI | engine do menu passa ao runtime; `menu.py` permanece como fachada compatível |
| `bc3a6fb` | barreira de durabilidade | falha após promoção é distinguida de falha ainda reversível |
| `3e1b6fc` | composição de estado | staging de componentes é retido até o commit do estado pai |
| `6a60323` | gameplay | modelos, catálogo e planejamento passam ao runtime com golden commands |
| `54f52a2` | identidade de processo | PID, token de criação e executável passam ao adapter de plataforma |
| `a30b41f` | readiness | probes de MVDSV, QTV e QWFWD passam ao supervisor runtime |
| `0966bc1` | grafo de gameplay | dependência de importação entre gameplay e manager é removida |
| `839d4ef` | clientes | runtime e recibo do cliente passam a uma transação coordenada com o estado pai |
| `73eb431` | lifecycle | execução, sinais, grupos/Job Object e encerramento passam ao supervisor runtime |
| `49594cd` | remoção de componentes | payload e metadados gerenciados ganham backup identificado e rollback; remoções legadas do update participam da transação pai |
| `cf6e959`–`3929c9c` | lock e composição | mutex da instalação passa à plataforma runtime; entrypoints recebem contextos explícitos; `session_control` passa a ser o contrato canônico |
| `189679d` | componentes gerados | inversos de conteúdo derivado permanecem disponíveis até o resultado do estado pai |
| `04e5672`–`71ca24c` | composição e Python | composição de desenvolvimento fica fora do runtime; resolvedor Python passa à plataforma canônica; projeção compatível não consumida sai do zipapp |
| `3b14559`–`1d4fe21` | remoções e defaults | remoções interativas participam do commit do estado e defaults novos são revertidos em falha posterior |
| `f932f21` | sessões | journal, recuperação após crash e reconciliação passam a `x86qw_runtime.supervisor.sessions`; arquivos materializados passam a `x86qw_runtime.io.managed_files` |
| `c5a9ae4` | CLI instalada | runtime, recibo e launchers da CLI são publicados como uma geração transacional |
| `d2e81e5` | PAKs centrais | os dois PAKs preservados passam por promoção e rollback atômicos sem mudar seus bytes ou política |
| `513ffaa`–`a59452b` | repair e migrações | permissões locais, metadados determinísticos e configurações pessoais retêm inversos até o resultado final |
| `78310a8`–`422baef` | composição final | inversos de instalação, estado e repair sobrevivem até a verificação final e são descartados somente depois dela |
| `1fea5ed` | superfície instalada | fachadas de compatibilidade não consumidas deixam o zipapp |
| `2807ceb` | contrato do zipapp | cada um dos 47 membros recebe origem, consumidor e contrato em manifesto independente, conferido contra o builder e o arquivo gerado |
| `efc4875` | cleanup | todos os caminhos selecionados pela limpeza formam uma transação; falha posterior restaura a seleção inteira |
| `d08f727` | uninstall | runtimes, componentes, metadados e geração da CLI são removidos como uma transação; falha restaura o snapshot anterior |
| `ff249ec`–`0cf2222` | gates e composição | detector de ciclos cobre imports relativos e `from package import module`; UI, HTTP e catálogos passam às fronteiras canônicas e lazy loading é preservado |
| `7a1dc9b` | gameplay e plataforma | PAKs, configurações runtime/pessoais e fatos de display/host/macOS passam ao runtime, com ownership de rollback identificado |
| `a2f041f` | mutações residuais | cache, purge e operações restantes do instalador passam por plano/quarantine e revalidação de identidade |
| `1409607` | supervisor e sessão | autoria de locks, journals, logs, stop requests, grupos e recuperação passa a contratos canônicos resistentes a corridas |
| `7ab1677` | projeção do zipapp | o builder passa a derivar os membros do manifesto declarativo; uma segunda lista estática deixa de existir |
| `29d76a4` | finalização de quarantine | arquivos regulares/diretórios usam remoção vinculada à observação; Windows valida o handle nativo; links e tipos especiais são preservados |

O scanner e extrator `x86qw_runtime.io.archive` já pertenciam ao runtime no
baseline, conforme o ADR 0002. O recorte mantém essa fronteira como única dona
de ZIP/PK3/PYZ e não altera limites, modos canônicos ou semântica de extração.

## Topologia resultante

```text
dist/installer/bin/
  manager.py ───────────────┐
  gameplay.py ──────────────┤
  services.py ──────────────┤
  menu.py (fachada) ────────┤
maintenance/tools/ ─────────┤
maintenance builders ───────┤
                            v
                      x86qw_runtime/
                      ├── catalogs, versioning, errors
                      ├── io/{archive,atomic,downloader,managed_files,
                      │       metadata,paths,personal_files,private_fs,
                      │       quarantine,remote}
                      ├── state, receipts, migrations, transaction
                      ├── gameplay
                      ├── platform/{display,host,locking,macos,processes,
                      │            python_runtime,windows_acl}
                      ├── session_control
                      ├── supervisor/{core,models,posix_guardian,readiness,sessions}
                      └── ui
```

O runtime não pode importar `maintenance`, `dist` nem fachadas instaladas. O
builder incorpora os módulos canônicos e projeções necessárias no zipapp, mas
não `maintenance/`.

O zipapp observado em `29d76a4` contém exatamente 56 membros: 44 módulos de
`x86qw_runtime`, quatro entrypoints/fachadas no topo, duas projeções KTX e seis
membros gerados. O arquivo
`maintenance/inventory/installer-runtime-members.json` declara, para cada
membro, origem, consumidor e contrato. O builder deriva sua projeção desse
manifesto e o teste de fronteira exige igualdade exata com o ZIP gerado;
arquivo não declarado ou sem consumidor não pode entrar silenciosamente.

## Persistência e rollback já cobertos

A fronteira `MutationPlan` captura o snapshot observado antes da confirmação,
revalida-o antes da primeira alteração e aplica etapas em ordem. Uma falha
reverte etapas concluídas na ordem inversa. Subtransações de componentes,
clientes, CLI instalada, PAKs, defaults, reparos, migrações, cleanup e uninstall
mantêm seus backups/staging até o estado pai e a verificação final, o que
permite rollback composto sem descartar os inversos cedo demais.

Os writes atômicos usam temporário privado, validação, `flush`, `fsync` e
`replace`. Uma exceção anterior à promoção preserva o destino antigo. Uma
exceção posterior à promoção cuja durabilidade não pôde ser comprovada gera um
erro de commit inconclusivo e preserva os efeitos; não afirma rollback que o
sistema não pode provar.

## Compatibilidade observada

- as fachadas antigas reexportam os mesmos símbolos canônicos quando essa
  identidade faz parte do contrato interno existente;
- os formatos atuais de estado e recibos permanecem iguais;
- os comandos de gameplay são comparados com uma fixture golden;
- `--help`, `--version`, `version`, `play --help` e `host --help` funcionam no
  zipapp mesmo com catálogos deliberadamente corrompidos;
- `host --help` e `status --help` executam usando o supervisor incluído no
  zipapp;
- nenhuma mudança intencional foi feita em gameplay, argumentos públicos,
  stable/nightly, perfis, componentes ou PAKs.

## Evidência focal já obtida

Estes resultados foram obtidos durante os incrementos e são registrados como
evidência parcial, não como validação do HEAD final da PR:

| Foco | Resultado registrado |
|---|---|
| manutenção integral após a primeira transação de componentes | 817 testes; 35 skips explícitos; aprovado |
| transação, componentes e instalador | 298 testes; 1 skip; aprovado |
| atomicidade, transação e componentes modernos | 161 testes; aprovado |
| UI e fronteiras runtime | 34 testes; aprovado |
| gameplay runtime e zipapp autocontido | 5 testes focais e 2 smokes de zipapp; aprovados |
| regressão moderna após gameplay | 143 testes; aprovado |
| identidade de processo e CLI autocontida | 5 testes; aprovado |
| readiness, serviços e gate de rede | 104 testes; 6 skips explícitos; aprovado |
| HEAD `29d76a4`: regressão integral de manutenção | 1.190 testes; aprovados; 37 skips explícitos de plataforma/rede |
| HEAD `29d76a4`: site | 5 testes; aprovados |
| HEAD `29d76a4`: transação, ownership e arquitetura focais | 349 testes; aprovados; 6 skips nativos Windows no macOS |

O resultado integral foi reproduzido no HEAD documentado e inclui a igualdade
dos 56 membros com a projeção derivada do manifesto, ausência de
`maintenance/` no zipapp, imports sem ciclo, carregamento tardio de catálogos,
identidade de fachadas, recuperação sem importar `services.py` e execução de
`host --help`/`status --help` no zipapp. Os testes adversariais exercitam
rollback de cleanup, uninstall e purge; substituição concorrente de raízes,
folhas e backups; ownership de locks, journals, logs e stop requests; e
preservação de caminhos que mudem de identidade. Os skips locais exigem APIs ou
shells nativos Windows, com um smoke de rede opt-in. A matriz Linux, macOS e
Windows deve reproduzir o snapshot enviado à PR.

## Riscos residuais

- `manager.py`, `gameplay.py` e `services.py` continuam sendo entrypoints e
  raízes de composição extensas; novas regras reutilizáveis não podem voltar a
  ser implementadas neles;
- logs de execução são append-only e não participam de rollback; o contrato
  transacional cobre mutações duráveis de payload, metadados e configurações
  gerenciadas;
- a finalização de quarantine é a barreira irreversível posterior ao commit;
  no Windows ela remove pelo handle validado; no POSIX usa descritor, identidade
  e rename exclusivo para nome privado imprevisível, enquanto links, tipos
  especiais, falhas ou trocas observadas preservam o conteúdo para inspeção;
- o POSIX não oferece `unlink`/`rmdir` condicional por inode; o lock da
  instalação exclui outras operações x86QW, mas não pretende defender contra
  código hostil executando como o mesmo usuário;
- a ausência de ciclo e de import inverso precisa permanecer verde depois das
  próximas extrações;
- o snapshot final ainda precisa da matriz portável da PR;
- smokes nativos de clientes e serviços pertencem ao PR 11.

Por isso a issue #52 foi encerrada pela PR 62; a revisão e a matriz nativa
continuam registradas como gates da estabilização 1.0. A `0.7.3`, seus
artefatos, hashes, tag, catálogo e bootstraps públicos permanecem imutáveis.
Esta nota não autoriza nem registra a publicação de uma nova versão: o
checkout corretivo da estabilização 1.0 continua separado da linha pública.
