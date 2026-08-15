# Release truth — projeção corrente

Esta é a observação corrente da autoridade machine-readable
[release-truth-current.json](release-truth-current.json). Ela é uma fotografia;
a source tree e o deployment live não são presumidos iguais.

## Estado observado em 2026-08-15T15:38:43Z

- **MAIN=GREEN:** main@fdd5a7267ec85674db70344b546ffc6a56417cb2; Validate
  31891985767, com 7/7 contexts protegidos verdes e 8/8 jobs concluídos.
- **TUF=WARNING:** a cadeia pública autentica root v2 e roles v18, mas o
  timestamp expira em 2026-08-15T21:09:01Z e já está dentro do alerta
  configurado de 6 horas. A verificação com limiar de 1 hora ainda é saudável.
  Nenhuma renovação ou publicação foi executada nesta auditoria.
- **1.0.0 owner-only=AT-RISK:** o instalador final continua ligado ao candidato
  e12ed081b968f820f47200e4be954a4f444056a1, SHA d3274e6a…, e a evidência nativa
  exata é E3/M3 25/25. O recibo público, contudo, aponta para a aceitação
  histórica 1.0.0-rc.1.
- **EXTERNAL-PUBLIC=NO-GO; FEATURE WORK=BLOCKED.** O warning TUF, o drift de
  audiência/deployment e a migração pública ainda não resolvida ativam os gates
  de parada.

## Quatro autoridades

| Autoridade | Verdade atual | Não deve ser confundida com |
| --- | --- | --- |
| source | baseline versionada 0.7.13 | release Latest |
| candidate/release | 1.0.0, digest do instalador/candidato, audience owner-only | HEAD atual |
| deployment | product/catalog/TUF servidos em 1.0.0/v18; site root ainda anuncia 0.7.13; /api/v1/release-truth.json retorna 404 | source tree |
| development | main@fdd5a726…, Validate verde | bytes publicados |

A comparação live observou product.json e catalog.json em 1.0.0, enquanto a
raiz do site ainda contém o hero “Distribuição 0.7.13 pública e verificável” e
não informa owner-only. A igualdade GitHub/GitLab foi verificada apenas para
o instalador (E2); convergência de todas as superfícies permanece desconhecida.

## Migração histórica

O instalador público exato x86qw-installer-0.7.13.zip
(SHA-256 11460440…) foi instalado em destino descartável e submetido ao CLI
final 1.0.0. A operação migrate foi bloqueada porque o estado histórico não
possui uma versão autenticada. Isso é uma observação local E1 do caminho público; nenhum log ou receipt durável desta execução foi anexado,
não uma falha de bytes automaticamente atribuída ao runtime nem uma aprovação de migração.

A evidência nativa final declara migration-0.7.13-real como aprovada, mas o
harness versionado em maintenance/native_case_entrypoint.py injeta
installation_version=0.7.13 no fixture antes de chamar a migração. Portanto,
essa evidência M3 é válida para o fixture do harness, mas não substitui EP-1 com
o instalador público exato.

## Regra de projeção

README, site, product, catálogo, release page, CLI e status devem apontar para
esta autoridade ou para projeção gerada dela. Nenhuma projeção pode declarar
external-public, supported ou aceitação do candidato final sem receipt
correspondente. O arquivo fonte ainda não foi publicado no site; não confundir
esta atualização documental com deploy.
