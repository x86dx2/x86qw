# Release truth — projeção corrente

Esta é a observação corrente da autoridade machine-readable
[release-truth-current.json](release-truth-current.json). Source tree,
candidate/release, deployment e development são autoridades separadas.

## Estado observado em 2026-08-15T19:58:26Z

- **MAIN=GREEN:** main@493cedb2344085b4f5d1f4a04b2217247e8f945d; Validate
  31896128822, com 7/7 contexts protegidos verdes e 8/8 jobs concluídos.
- **TUF=HEALTHY:** root v2 autenticou timestamp v19, snapshot/targets v18 e
  75 pacotes. O timestamp expira em 2026-08-16T18:15:26Z, fora da janela de
  alerta de 6 horas. A renovação protegida foi executada no run 31900570555,
  o recovery drill foi registrado no run 31900793093 e a publicação
  timestamp-only foi verificada no run 31900914825.
- **1.0.0 owner-only=AT-RISK:** os bytes finais continuam ligados ao candidato
  e12ed081b968f820f47200e4be954a4f444056a1, instalador SHA
  d3274e6aa2f1e3078ac5000ffae8b97c9efd329f3c2a87499bf1c57e5f388cb8 e
  candidate SHA
  0bde0550895cab24abf8a3ee974da011e031fea11279148a41635e173cbdcc21. O
  deployment ainda serve uma geração anterior do site: release-truth retorna
  404, product.json não expõe release_audience e o hero mantém o claim
  histórico 0.7.13.
- **EXTERNAL-PUBLIC=NO-GO; FEATURE WORK=BLOCKED.** A verdade de deployment
  precisa convergir e os gates 0D/0E e EP-0..EP-5 continuam abertos.

## Quatro autoridades

| Autoridade | Verdade atual | Não deve ser confundida com |
| --- | --- | --- |
| source | baseline versionada 0.7.13 e projeções owner-only na main | release Latest |
| candidate/release | 1.0.0, digest do instalador/candidato, audience owner-only | HEAD atual |
| deployment | product/catalog/TUF servidos; geração candidata verificada e convergente | source tree |
| development | main@493cedb…, Validate verde | bytes publicados |

## TUF e recuperação

A cadeia pública está autenticada e saudável após a renovação limitada de
timestamp. O drill técnico passou e foi registrado com operator
release-operator, custody host offline-signer-01 e SLA de 6 horas. Isso fecha o
gap técnico imediato, mas não prova custódia independente, backup humano, RTO
de produção ou sucessão; esses pontos permanecem em #148.

## Contradições abertas

| ID | Contradição | Gate | Estado |
| --- | --- | --- | --- |
| RT-01 | receipt public_acceptance aponta RC1; evidência M3 final está separada | 0C | BLOCKED |
| RT-02 | deployment root e endpoint não refletem a projeção owner-only da main | 0C | BLOCKED |
| RT-03 | ownership/SBOM final possui 87/87 itens unclassified/NOASSERTION | 0C | BLOCKED |
| RT-04 | cleanup --personal-data não cobre qw/demos prometidos | 0C/EP-1 | BLOCKED |
| RT-05 | migração do instalador público 0.7.13 exato não substitui o harness M3 | EP-1 | BLOCKED |
| RT-06 | renovação/recovery/publicação TUF passaram; custódia independente/RTO faltam | 0B | RESOLVED_TECHNICAL |
| RT-07 | product.json live expõe release_audience e release-truth responde 200 | 0C | BLOCKED |

## Regra de projeção

README, site, product, catálogo, release page, CLI e status devem apontar para
esta autoridade ou para uma projeção gerada dela. Nenhuma projeção pode elevar
audiência, suporte ou evidência. Artifact availability não equivale a
external-public. O próximo deploy deve ser uma geração única, verificada e
rollbackável; não é uma nova release de produto por si só.
