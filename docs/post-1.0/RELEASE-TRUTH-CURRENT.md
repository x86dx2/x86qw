# Release truth — projeção corrente

Este documento é a projeção datada da autoridade machine-readable
[`release-truth-current.json`](release-truth-current.json). O endpoint público
correspondente é [`/api/v1/release-truth.json`](../../site/public/api/v1/release-truth.json)
quando a árvore for publicada; a existência do arquivo na source tree não é
prova de deploy live.

## Estado observado

- `MAIN=GREEN` após o merge do Gate 0A e do Master Plan; o run
  `31888249914` concluiu os sete contexts protegidos com sucesso.
- `TUF=HEALTHY` é a saúde técnica da lease observada, não prova de custódia,
  backup ou recovery de produção. #148 e #152 permanecem abertos.
- `1.0.0 owner-only=AT-RISK`: os bytes e digests publicados permanecem
  vinculados ao candidato, mas receipt/audiência/projeções ainda exigem
  reconciliação completa.
- `EXTERNAL-PUBLIC=NO-GO`; `FEATURE WORK=BLOCKED` até 0B–0E e EP-0.

## Quatro autoridades

| Autoridade | Verdade | Não deve ser confundida com |
| --- | --- | --- |
| source | baseline versionada `0.7.13` | release Latest |
| candidate/release | `1.0.0`, digest do instalador/candidato e audiência `owner-only` | HEAD atual |
| deployment | site/catalog/product/TUF servidos; deploy live ainda exige verificação | source tree |
| development | `main@2749d6b…`, Validate `31888249914` | bytes publicados |

`owner-only` significa que o fluxo publicado é destinado ao mantenedor/escopo
single-user documentado. Não significa que o artefato esteja privado, que o
GitHub Latest seja um canal de suporte externo ou que as plataformas preview
tenham evidência nativa.

## Política de projeção

README, site, product, catálogo, release page, CLI e status devem apontar para
esta autoridade ou para uma projeção gerada dela. Uma superfície pode
preservar a história `0.7.13`/RC1, desde que a qualifique como baseline ou
histórico. Nenhuma projeção pode declarar `external-public`, `supported` ou
aceitação do candidato exato sem receipt correspondente.

O arquivo é uma fotografia datada: uma alteração de main, release, catálogo,
TUF ou audiência exige uma nova observação e novo digest do ledger. O endpoint
fonte e o JSON em `docs/` devem permanecer byte a byte iguais; o teste de
projeção falha em caso de drift.
