# Fronteiras incrementais de runtime — implementação parcial da PR 6

- **Issue:** [#52](https://github.com/x86dx2/x86qw/issues/52)
- **Baseline inicial:** `00098330e5833ba2c83c7121272d644c2a204a7b`
- **Recorte documentado:** `23194fd..49594cd`
- **Versão pública preservada:** `0.7.1`
- **Estado:** implementação parcial em branch; PR 6 ainda aberta; não publicada

Esta nota registra somente mudanças já commitadas na branch
`agent/runtime-boundaries`. Alterações locais posteriores, critérios ainda
abertos da issue e trabalho reservado às PRs 7–12 não são apresentados como
entregues.

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
                      ├── io/{archive,atomic,downloader,metadata,paths}
                      ├── state, receipts, migrations, transaction
                      ├── gameplay
                      ├── platform/processes
                      ├── supervisor
                      └── ui
```

O runtime não pode importar `maintenance`, `dist` nem fachadas instaladas. O
builder incorpora os módulos canônicos e projeções necessárias no zipapp, mas
não `maintenance/`.

## Persistência e rollback já cobertos

A fronteira `MutationPlan` captura o snapshot observado antes da confirmação,
revalida-o antes da primeira alteração e aplica etapas em ordem. Uma falha
reverte etapas concluídas na ordem inversa. Subtransações de componentes e
clientes mantêm seus backups/staging até o estado pai ser gravado, o que
permite rollback composto.

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

Os skips pertencem às condições de plataforma/rede já declaradas pelas suítes;
o fechamento da PR precisa registrar novamente cada skip no snapshot final. A
matriz Linux, macOS e Windows e a regressão integral após todos os incrementos
continuam obrigatórias.

## Riscos residuais

- instalação e atualização da CLI ainda não estão integralmente sob a mesma
  transação composta;
- defaults, ordem de pacotes e outros artefatos derivados ainda possuem
  caminhos de mutação fora do contrato comum;
- chamadores residuais de remoção, migrações mutáveis, `cleanup`, `uninstall` e
  `--purge` precisam de planos explícitos que não prometam rollback impossível;
- existem responsabilidades residuais de plataforma e composição nos
  entrypoints;
- a ausência de ciclo e de import inverso precisa permanecer verde depois das
  próximas extrações;
- o snapshot final ainda precisa de regressão integral, matriz portável e
  inspeção do zipapp mínimo;
- smokes nativos de clientes e serviços pertencem ao PR 11.

Por isso a issue #52 e a PR 6 permanecem abertas. A `0.7.1`, seus artefatos,
hashes, tag, catálogo e bootstraps públicos permanecem imutáveis. Não houve
publicação.
