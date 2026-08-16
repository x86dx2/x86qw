# Release truth — escopo corrente M3-first

Esta projeção separa source, candidate/release, deployment e development. O
contrato corrente cobre um único usuário e um único laboratório nativo: Apple
M3.

## Estado verificado em 2026-08-16T18:53:51Z

- MAIN=GREEN: main@fedb79dd7d4df05572007153afb505ebefe0a151; Validate
  31965464008 passou na primeira tentativa com 3/3 contexts protegidos
  (macos 3.10, macos 3.13, native-contract) e 5/5 jobs do Validate em push.
- TUF=HEALTHY: root v2, timestamp v22, snapshot/targets v19 e 75 pacotes foram
  autenticados; expiry 2026-08-23T17:56:54Z; monitor 6 h 31965691395 healthy.
  A operação técnica continua obrigatória enquanto o instalador owner-only
  usar os endpoints públicos.
- 1.0.0 OWNER-ONLY=VALID_FOR_SINGLE_USER_M3: o candidato exato passou 25/25
  casos no Apple M3 e o instalador publicado permanece bound ao SHA-256
  d3274e6aa2f1e3078ac5000ffae8b97c9efd329f3c2a87499bf1c57e5f388cb8.
- EXTERNAL-PUBLIC=NO-GO.
- FEATURE WORK=ALLOWED enquanto S0-M3 permanecer verde.

## S0-M3 — único gate funcional

S0 exige main verde, cadeia TUF técnica saudável, evidência nativa do candidato
exato no M3 e lifecycle owner-only recuperável. Migração histórica, usuário
externo, plataformas não-M3, custódia TUF independente e QWLeague não fazem
parte desse gate. S0.3 passou com o receipt de projeção; S0.4 registrou a
primeira observação do mantenedor e continua como operação leve.

## Migração e futura publicação

A migração de 0.7.13 não é necessária se a primeira publicação para terceiros
declarar uma baseline nova e instalação limpa, sem prometer upgrade de
instalações históricas. Ela volta a ser requisito somente se o produto oferecer
esse upgrade. Apagar histórico, tags, releases ou evidências é uma decisão
destrutiva separada; não é requisito para desenvolver funcionalidades.

## Projeção pública

O run 31965901520 verificou os handoffs imutáveis, autenticou TUF v22, validou
bootstraps e product, publicou a projeção e confirmou release-truth HTTP 200
com `snapshot_commit` fedb79dd… e audiência owner-only. O probe da raiz
retornou HTTP 200 com o marcador `owner-only` (`verified`, não só assembled).
Artifact `site-projection-repair-31965901520-1` id 9268487003; SHA-256 do
recibo `827b59ef2e2cb581641ebe9d97266b68d9a16a568bdce28a325854b33541932c`.
O primeiro despacho 31965760009 publicou os mesmos bytes mas falhou a leitura
imediata do JSON por corrida de CDN; não republicou instalador nem metadata
TUF.

## Observação owner-only (S0.4)

Em 2026-08-16T18:54Z neste host: `doctor` no destino default `quake-world`
reportou instalação ausente, cache TUF local v18 expirado (não é o TUF
público v22), runtime macOS arm64 ok e `healthy=false`. Não existe
`~/Games/x86qw`. `x86qw ui --output /tmp/x86qw-s04-ui.html` escreveu o
painel read-only fora da instalação. Isso não é soak, M3 gameplay nem
aceitação de usuário externo.

## Autoridades

| Autoridade | Verdade corrente |
| --- | --- |
| source | baseline histórica 0.7.13, sem representar a audiência corrente |
| candidate/release | 1.0.0 owner-only, bytes imutáveis e digest verificado |
| deployment | candidate site, product, catálogo, bootstraps e TUF públicos |
| development | main@fedb79d…, Validate 31965464008 verde |
| scope | um usuário, Apple M3, funcionalidades liberadas após S0 |

## Residuais não bloqueantes de funcionalidades

- receipt histórico aponta RC1; a evidência final M3 permanece separada;
- ownership/SBOM precisa de classificação antes de redistribuição ampla;
- custódia independente, backup humano e RTO TUF pertencem a external-public;
- Windows, Linux e macOS Intel continuam preview/not-run;
- QWLeague permanece BLOCKED_EXTERNAL.

## Próxima capacidade

S0, F1–F3 e FUNC-008 estão na main. A frente contínua é a lease TUF e a
observação owner-only. EP-0 a EP-5 e QWLeague permanecem fora do caminho
funcional.
