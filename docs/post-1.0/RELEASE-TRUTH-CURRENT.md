# Release truth — escopo corrente M3-first

Esta projeção separa source, candidate/release, deployment e development. O
contrato corrente cobre um único usuário e um único laboratório nativo: Apple
M3.

## Estado verificado em 2026-08-16T18:53:51Z

- MAIN=GREEN: main@fedb79dd7d4df05572007153afb505ebefe0a151; Validate
  31965464008 passou na primeira tentativa com 3/3 contexts protegidos
  (macos 3.10, macos 3.13, native-contract) e 5/5 jobs do Validate em push.
- TUF=HEALTHY: root v2, timestamp/snapshot/targets v21 autenticam o catálogo
  1.0.1 (SHA-256 864e4cc1…); expiry 2026-08-23T23:35:02Z.
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
- zipapp público **1.0.2** instalado (`x86qw-installer-1.0.2.zip` SHA-256
  `35e77723276c5bfc0d3f52f7d2a009d2eb8bdfc3c02f43c08409005cc61b58a3`;
  pyz `cd37e86885814fe861881a7f86690929046c9b77944ce670a5b886740242f83f`);
  ezQuake stable 3.6.9. O zipapp 1.0.1 (`3dbf0414…`) permanece histórico.
  O 1.0.2 tem preflight local 25/25 no digest publicado.

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

Correção in-tree daquela noite (o zipapp 1.0.0 imutável não recebia o
conserto), exercitada em 2026-08-16T21:44Z–21:55Z com
`python3 dist/installer/bin/manager.py` contra `~/Games/x86qw`:

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

Não é soak, native 25/25 do digest 1.0.1 nem external-public. Naquela noite a
instalação persistente ainda não tinha sido desinstalada.

Observação no zipapp **1.0.1** (2026-08-17T00:35Z–00:36Z), launcher
`~/Games/x86qw/x86qw.sh`:

- PASS: `version` = `x86QW 1.0.1`; `status --json` `ok: true` com
  `target` `/Users/x86/Games/x86qw`; `doctor --json` `healthy: true`;
- PASS (2/2): `host ktx --mode practice --map dm6 --bind 127.0.0.1 --background`
  — MVDSV ativo em `127.0.0.1:28501`, mapa `dm6`;
- PASS (2/2): `host team-fortress --map 2fort5r --bind 127.0.0.1 --background`
  — MVDSV ativo, mapa `2fort5r`;
- PASS: `status --stop --yes` deixa sessões `clean`, sem listener em 28501;
- PASS: `ui --output` escreve HTML `x86QW owner-only — saudável`.
- O FAIL de `status --json` e de `host` no zipapp 1.0.0 ficou histórico; o
  conserto está no CLI 1.0.2 publicado, não só in-tree.

Observação no zipapp **1.0.2** (2026-08-17T01:53Z–01:54Z), launcher
`~/Games/x86qw/x86qw.sh`:

- PASS: `version` = `x86QW 1.0.2`; pyz `cd37e868…` = zip publicado;
  `upgrade` noop; `status --json` `ok: true`; `doctor --json` `healthy: true`;
- PASS (2/2): `host ktx --mode practice --map dm6 --bind 127.0.0.1 --background`;
- PASS (2/2): `host team-fortress --map 2fort5r --bind 127.0.0.1 --background`;
- PASS: `status --stop --yes` deixa sessões `clean`, sem listener em 28501;
- PASS: `ui --output` escreve HTML `x86QW owner-only — saudável`.

`uninstall` conservador no mesmo destino (2026-08-17T02:09:09Z–02:09:12Z),
CLI 1.0.2 pyz `cd37e868…`, sem `--purge`:

- PASS: exit 0; `x86qw.sh`, `x86qw.cmd`, `.x86qw/cli/`, `.x86qw/state.json`,
  `ezQuake Stable.app`, `mvdsv`, `qtv/qtv` e `qwfwd/qwfwd` removidos;
- PASS: `id1/pak0.pak` `eec9a020…` e `id1/pak1.pak` `94e35583…` intactos;
  `ezquake/configs/config.cfg` e os `*-user.cfg` de qw/arena/fortress/prox/td2
  com o mesmo SHA-256 de antes; `.x86qw/personal/{inventory,receipt}` intactos;
- PASS: logs e journals de sessão preservados; destino ficou em ~52 MiB
  (sobram PAKs, pessoais e diretórios vazios);
- PASS: cache Darwin `x86qw` (marker + components + trust) não foi tocado.

`uninstall --purge` no mesmo destino (2026-08-17T02:24:08Z–02:26:16Z),
zipapp publicado 1.0.2 `cd37e868…`:

- EXPECTED: após o uninstall conservador, `--purge` recusou o destino
  sem identidade gerenciada (exit 1); PAKs, pessoais e cache Darwin
  permaneceram;
- PASS: reinstalação `--non-interactive --profile complete` no mesmo
  caminho (2026-08-17T02:24:24Z–02:25:56Z) restaurou CLI 1.0.2 e 21
  componentes; `id1/pak0.pak` `eec9a020…` e `id1/pak1.pak` `94e35583…`
  sobreviveram à reinstalação;
- PASS: `./x86qw.sh uninstall --purge` exit 0; destino
  `/Users/x86/Games/x86qw` deixou de existir; cache Darwin `x86qw`
  removido; PAKs e pessoais do destino removidos;
- PASS: segundo `--purge` com destino já ausente exit 0 (noop de
  instalação e de cache).
- Cópia local dos restos conservadores ficou fora do destino, em
  `/Users/x86/x86qw-1.0.2-cut/games-preserve-before-purge`.

## Autoridades

| Autoridade | Verdade corrente |
| --- | --- |
| source | baseline current 1.0.2; 1.0.1 e 0.7.13 históricas no catálogo |
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

S0, F1–F3 e FUNC-008 estão na main. `~/Games/x86qw` foi desinstalada de
forma conservadora e em seguida com `--purge` no CLI **1.0.2**; o destino
e o cache Darwin não existem mais. A fonte current é **1.0.2**. A `1.0.1`
e a `0.7.13` permanecem históricas. Preflight local 25/25 do digest 1.0.2
está registrado. Renovação TUF (antes de 2026-08-24T01:35:49Z) é operação
contínua. F4 e EP só com uso real ou decisão de audiência.
