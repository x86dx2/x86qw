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

Primeira leitura em 2026-08-16T18:54Z: `doctor` no destino default
`quake-world` do clone reportou instalação ausente, cache TUF local v18
expirado e `healthy=false`.

Instalação persistente owner-only neste Apple M3 Pro, fora do git:

- 2026-08-16T20:41:04Z: perfil `essential`; 2026-08-16T20:53:26Z: o mesmo
  destino foi convergido a `complete` (~657 MiB, 21 componentes,
  fingerprint `650b3596a35ae601f6931a82bab2c86ff09eae15116a8a72fe4956867f73c561`);
- zipapp público 1.0.0 (`x86qw-installer-1.0.0.zip` SHA-256
  `d3274e6aa2f1e3078ac5000ffae8b97c9efd329f3c2a87499bf1c57e5f388cb8`);
  ezQuake stable 3.6.9; `verify --json` continua `ok` depois da matriz.

Matriz 2026-08-16T20:55Z–21:07Z (CLI 1.0.0 salvo onde indicado):

- PASS: `version`, `verify`, `update --dry-run`, `upgrade --dry-run`,
  `repair --dry-run`, `migrate --dry-run`, `changes`, `update --yes`
  (noop), `hub --json`;
- PASS play (ezQuake 3.6.9 windowed, `qconsole` com Initialized / GL 4.1
  Metal): ktx practice, ktx frogbot, final-arena, pro-x, team-fortress, td2;
- PASS: `proxy --background`, `qtv --background`, `status --stop --yes`;
- PASS main@`132a317…`: `doctor` healthy, `ui`, `profile`, `library`
  add/remove, `host --save-preset s04-complete`;
- FAIL: `./x86qw.sh status --json` (launcher 1.0.0 injeta `--target`;
  `status --json` posicional no pyz funciona);
- FAIL: `host` na CLI 1.0.0 instalada (zipapp imutável): o preflight
  UDP/RCON de 8 s não recebe `status`.
- A CLI 1.0.0 não tem `doctor`/`ui`; jogos não-KTX pedem confirmação se
  `--mode` está ausente (stdin EOF cancela).

Correção in-tree (ainda não no zipapp 1.0.0), exercitada em
2026-08-16T21:44Z–21:55Z com `python3 dist/installer/bin/manager.py`
contra `~/Games/x86qw`:

- causa: MVDSV 1.11 descarta `+exec` / `rcon exec` quando o basename do
  cfg passa de ~35 caracteres (`x86qw_host_` + `token_hex(12)` + `.cfg`
  = 39); o mapa não carrega e o `status` UDP não responde. Sem
  `server.cfg` do KTX, `sv_crypt_rcon` fica ligado e o preflight RCON
  plaintext falha. `token_urlsafe` também pode partir o token no `-`.
- PASS in-tree: `host ktx --mode practice --map dm6 --bind 127.0.0.1`
  (`status` UDP com `map\\dm6` e `*gamedir\\qw`); stack
  `--with-qtv --with-proxy`; `host` final-arena, pro-x, team-fortress e
  td2; `status --stop --yes`. Prefixo efêmero `xh_`/`xp_` (31 chars),
  RCON bootstrap `token_hex(12)`, `sv_crypt_rcon 0` na sessão.

Não é soak, 1.0.1, native 25/25 nem external-public. A instalação não foi
desinstalada. Uninstall/purge não foram exercitados neste destino.

## Autoridades

| Autoridade | Verdade corrente |
| --- | --- |
| source | baseline current 1.0.1; 0.7.13 histórica no catálogo |
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

S0, F1–F3 e FUNC-008 estão na main. Há instalação persistente 1.0.0 complete
em `~/Games/x86qw`. A fonte current é **1.0.1**. A `0.7.13` permanece histórica. F1–F3, FUNC-008 e
o conserto do `host`/launcher entram nessa linha 1.0.x; não há 1.1 nesta fase.
A promoção pública (TUF assinado, GitHub Latest, evidência M3 do candidato
exato) continua um gate separado. A frente contínua é a lease TUF (renovar
antes de 2026-08-23T11:56:54Z). EP-0 a EP-5 e QWLeague permanecem fora do
caminho funcional.
