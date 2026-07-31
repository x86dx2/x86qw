# Baseline da consolidação — 31 de julho de 2026

Esta nota registra o estado observado antes da consolidação solicitada. Ela não
substitui o catálogo, os inventários nem os roadmaps.

## Identidade analisada

- branch sincronizada: `main`;
- HEAD de origem: `eeea45786401aae166efcd04f5d126faea740da2`;
- versão corrente do instalador: `0.1.25`;
- catálogo público: 48 pacotes;
- BOM: 21 componentes;
- testes iniciais: 194 testes de manutenção e 3 do site; 1 teste de rede
  ignorado explicitamente;
- validação inicial: `PYTHONDONTWRITEBYTECODE=1 ./maintenance/manage.py verify`
  concluída no macOS arm64.

## Superfície existente

A CLI já oferece `install`, `play`, `host`, `proxy`, `qtv`, `version`, `update`,
`upgrade`, `components`, `presets`, `hub`, `verify`, `cleanup` e `uninstall`.
A CLI instalada restringe `install`, `components` e `presets` ao bootstrap.

Os cinco jogos existentes são KTX, Final Arena, Pro-X, Team Fortress e Total
Destruction 2. Stable e nightly coexistem. MVDSV, QTV e QWFWD já são componentes
executáveis em primeiro plano; não são trabalho novo desta consolidação.

## Plataformas

O catálogo distribui o ezQuake para macOS universal, Linux x86-64 e Windows
x64. Os serviços distribuem macOS arm64, Linux amd64 e Windows x64; macOS Intel
não possui runtime de serviço.

Antes desta tarefa não havia `.github/workflows/`. A suíte completa havia sido
executada localmente no macOS arm64, incluindo smokes registrados no roadmap.
Linux e Windows eram declarados e possuíam validações de formato, mas ainda não
tinham uma matriz contínua real. Testes portáveis em runners não equivalem a
smoke do runtime nativo.

## Acoplamentos e hardcodings confirmados

- `manager.py` contém a matriz fixa do ezQuake e detecção de sistemas;
- `gameplay.py` usa `LOCAL_GAMES`, conjuntos de capacidades legadas e
  condicionais por nome de jogo;
- `services.py` usa `RUNTIME_NAMES`, `runtime_variant` e condicionais por KTX e
  Pro-X;
- dados de modos KTX já vêm de `modes.json` e servem de padrão declarativo;
- `play` e `host` chamam `ensure_local_play_support`, podendo criar payload,
  recibo e configuração pessoal durante execução;
- a materialização PK3 dedicada usa `Path(info.filename)`, portanto depende da
  semântica de caminho do host;
- QTV aceitava upstream como texto genérico e não existia preflight conjunto
  de portas;
- senhas eram aceitas somente por argumentos;
- não existia journal recuperável de sessões interrompidas.

## Contradições documentais confirmadas

- o site e o manual ainda diziam “18 componentes”, enquanto o BOM possui 21;
- a primeira lista do README omitia `host`, `proxy` e `qtv`;
- o `ROADMAP.md` ainda tratava MVDSV, QTV, QWFWD e o menu KTX como futuros;
- o roadmap exclusivo apontava para um HEAD anterior e misturava entrega
  funcional com validação de plataforma;
- contagens, versão, comandos e plataformas eram repetidos manualmente.

## Entregue versus planejado

Já entregue: instalador multiplataforma, stable/nightly, cinco jogos, 24 modos
KTX declarativos, Frogbots, `play`, `host`, MVDSV, QTV HTTP, QWFWD, `hub`,
update/upgrade por perfil, verificação, limpeza e desinstalação.

Ainda planejado no baseline: CI multiplataforma, hardening completo de arquivos
e endpoints, senhas fora da linha de comando, preflight/readiness integral,
journal e recuperação, catálogo declarativo de runtimes/jogos, execução sem
mutação, reparo explícito, perfis operacionais de host e central de demos.

## Invariantes desta consolidação

- preservar `id1/pak0.pak` e `id1/pak1.pak` e a estratégia técnica atual;
- não incorporar runtime, engine, mod ou mapa novo;
- manter FTEQCC somente como toolchain já existente, sem introduzir FTEQW;
- preservar comandos, recibos, inventários, configurações pessoais e bundles
  publicados;
- não publicar nem alterar o bootstrap público sem aprovação separada.
