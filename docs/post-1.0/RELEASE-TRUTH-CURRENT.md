# Release truth — escopo corrente M3-first

Esta projeção separa source, candidate/release, deployment e development. O
contrato corrente cobre um único usuário e um único laboratório nativo: Apple
M3.

## Estado verificado em 2026-08-16T00:36:49Z

- MAIN=GREEN: main@e0a8418201c3521af9e1f2aed714c82115a5ad6c; Validate
  31917327277 passou na primeira tentativa com 7/7 contexts protegidos e 8/8
  jobs.
- TUF=HEALTHY: root v2, timestamp v20, snapshot/targets v18 e 75 pacotes foram
  autenticados; a operação técnica continua obrigatória enquanto o instalador
  owner-only usar os endpoints públicos.
- 1.0.0 OWNER-ONLY=VALID_FOR_SINGLE_USER_M3: o candidato exato passou 25/25
  casos no Apple M3 e o instalador publicado permanece bound ao SHA-256
  d3274e6aa2f1e3078ac5000ffae8b97c9efd329f3c2a87499bf1c57e5f388cb8.
- EXTERNAL-PUBLIC=NO-GO.
- FEATURE WORK=ALLOWED enquanto S0-M3 permanecer verde.

## S0-M3 — único gate funcional

S0 exige main verde, cadeia TUF técnica saudável, evidência nativa do candidato
exato no M3 e lifecycle owner-only recuperável. Migração histórica, usuário
externo, plataformas não-M3, custódia TUF independente e QWLeague não fazem
parte desse gate.

## Migração e futura publicação

A migração de 0.7.13 não é necessária se a primeira publicação para terceiros
declarar uma baseline nova e instalação limpa, sem prometer upgrade de
instalações históricas. Ela volta a ser requisito somente se o produto oferecer
esse upgrade. Apagar histórico, tags, releases ou evidências é uma decisão
destrutiva separada; não é requisito para desenvolver funcionalidades.

## Projeção pública

O run 31917579279 verificou os handoffs imutáveis, autenticou TUF, validou os
bootstraps e product, publicou a projeção e confirmou release-truth HTTP 200 com
a audiência owner-only. A etapa final recebeu HTTP 403 ao consultar a raiz a
partir do IP do GitHub runner; por isso o workflow terminou sem receipt. Esse é
um false negative do verificador de vantage Cloudflare, não falha dos bytes,
TUF, produto ou runtime. O gate da raiz permanece estrito até receber uma
solução que não transforme 403 em sucesso.

## Autoridades

| Autoridade | Verdade corrente |
| --- | --- |
| source | baseline histórica 0.7.13, sem representar a audiência corrente |
| candidate/release | 1.0.0 owner-only, bytes imutáveis e digest verificado |
| deployment | candidate site, product, catálogo, bootstraps e TUF públicos |
| development | main@e0a8418..., Validate 31917327277 verde |
| scope | um usuário, Apple M3, funcionalidades liberadas após S0 |

## Residuais não bloqueantes de funcionalidades

- receipt histórico aponta RC1; a evidência final M3 permanece separada;
- ownership/SBOM precisa de classificação antes de redistribuição ampla;
- custódia independente, backup humano e RTO TUF pertencem a external-public;
- Windows, Linux e macOS Intel continuam preview/not-run;
- a raiz do site precisa de um probe compatível com a política Cloudflare sem
  relaxar a verificação;
- QWLeague permanece BLOCKED_EXTERNAL.

## Próxima capacidade

Com S0-M3 verde, a primeira frente é FUNC-001: doctor read-only, seguida por um
bundle diagnóstico sanitizado. Cada PR mantém main verde e recebe aceitação
nativa no M3 quando houver mudança de comportamento.
