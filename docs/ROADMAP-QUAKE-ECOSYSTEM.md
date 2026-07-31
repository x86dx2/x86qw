# Roadmap técnico do ecossistema Quake no x86QW

Este documento orienta a evolução incremental do x86QW de uma distribuição
moderna de QuakeWorld para uma plataforma capaz de instalar, atualizar, jogar,
treinar, hospedar, transmitir, observar, gravar, reproduzir, analisar e preservar
conteúdo Quake. Ele é autocontido, não substitui o `ROADMAP.md` geral e não
redefine os marcos já registrados naquele arquivo.

O baseline foi levantado na branch `main`, commit
`3ffbb1125fb673a45db17255aec079fc32eb266e`, em 30 de julho de 2026. Versões e
plataformas externas abaixo descrevem o estado observado nessa data; cada
incorporação futura ainda deve fixar a versão ou o commit, preservar a origem,
registrar tamanho e SHA-256 e passar pelos testes previstos neste roadmap.

## Resultado de produto

O x86QW terá dois trilhos independentes no runtime e compartilhados apenas na
infraestrutura de distribuição:

- **Trilha A — QuakeWorld:** ezQuake, unezQuake experimental, KTX, partidas
  online e locais, bots, treino, MVDSV, QTV, QWFWD e demos MVD/QWD;
- **Trilha B — Quake clássico:** vkQuake, `id1`, `hipnotic`, `rogue`, campanha,
  single-player e, depois do MVP, conteúdo compatível do relançamento de 2021.

As trilhas compartilham instalador, catálogo, mirrors, hashes, recibos,
inventários, gerenciador, testes, site e documentação. Elas não compartilham
automaticamente configurações, saves, diretórios pessoais, perfis visuais,
argumentos de inicialização nem regras de carregamento de pacotes.

## Princípios preservados

- `dist/` permanece a fonte canônica do produto;
- artefatos publicados são derivados imutáveis;
- todo artefato registra origem, tamanho e SHA-256;
- a cópia upstream permanece separada da camada `x86QW`;
- cada componente mantém recibo e inventário independentes;
- instalação, migração e atualização são transacionais e possuem rollback;
- ezQuake stable e nightly coexistem;
- arquivos pessoais e modificados são preservados;
- uma atualização não substitui silenciosamente um arquivo modificado;
- componentes instalados podem ser verificados offline;
- o catálogo é versionado e dependências são explícitas;
- remoção é segura e respeita propriedade por inventário;
- `update` e `upgrade` mostram um plano antes de alterar o destino;
- macOS, Linux e Windows são tratados explicitamente, sem fingir suporte onde
  um upstream não oferece artefato validado;
- nenhuma instalação global é exigida quando um runtime pode permanecer
  autocontido;
- mapas, gráficos e outros acervos externos não são baixados em massa;
- conteúdo comunitário entra somente por curadoria e com consumidor declarado;
- instalação, atualização, verificação, migração e remoção possuem testes;
- a evolução é incremental e compatível com instalações atuais, sem reescrita
  completa do projeto.

## Estado atual verificado

### Produto e catálogo

- o instalador público corrente nesta árvore é `0.1.25`;
- o catálogo da árvore contém 48 pacotes e o BOM contém 21 componentes;
- ezQuake stable `3.6.9` e nightly
  `20260616-101233_a86996a` estão preservados para macOS universal, Linux
  x86-64 e Windows x64;
- os componentes jogáveis atuais incluem KTX `1.47+x86qw.12`, Final Arena,
  Pro-X, Team Fortress e Total Destruction 2; MVDSV `1.11+x86qw.2`, QTV
  `0+025ca949aca0+x86qw.1` e QWFWD `1.30+x86qw.2` completam o runtime de
  servidor e transmissão;
- os 21 componentes atuais são `nquake-bootstrap`, `nquake-visual-core`, `ktx`,
  skins, miras, skyboxes, modelos, bandeiras de placar, texturas externas,
  texturas-base, mapas, matchinfo, documentação nQuake, QRP, Final Arena,
  Pro-X, Team Fortress, TD2, MVDSV, QTV e QWFWD;
- `dist/` já separa clientes, distribuição nQuake, dados-base, mods,
  instalador, servidores, serviços e toolchains.

### CLI e instalação

- o bootstrap `install.sh`/`install.ps1` instala o cliente e os componentes; a
  CLI permanente rejeita instalação e seleção arbitrária de conteúdo;
- a CLI permanente expõe `play`, `host`, `proxy`, `qtv`, `hub`, `update`, `upgrade`, `verify`,
  `cleanup` e `uninstall`, incluindo `uninstall --purge`;
- `update` atualiza somente runtimes e componentes presentes; `upgrade` também
  incorpora componentes novos pertencentes ao perfil registrado;
- ambos mostram somente mudanças, aceitam `--dry-run`, exigem confirmação e
  aceitam `--yes` para automação;
- `hub` já lista servidores, entra, observa diretamente e usa um stream QTV
  publicado pelo servidor; isso será expandido, não recriado;
- `play` valida recibos e arquivos, aceita o jogo como subcomando, escolhe entre
  ezQuake stable/nightly, lista mapas e inicia com `-nohome`; KTX possui 24
  modos declarados e opções de Frogbot, CTF e Race;
- `host`, `proxy` e `qtv` verificam seus componentes e executam MVDSV, QWFWD e
  QTV em primeiro plano, com loopback seguro por padrão e encerramento
  coordenado.

### Perfis e estado

- `essential`, `recommended` e `complete` são perfis declarados no BOM;
- `custom` é uma seleção individual reconhecida pelo instalador e gravada no
  estado, embora não seja uma lista fixa no objeto `profiles`;
- `state.json` registra formato 1, perfil, componentes solicitados,
  componentes registrados e componentes conhecidos;
- fingerprints históricos permitem reconhecer perfis antigos;
- configurações mutáveis, como `config.cfg`, são tratadas como pessoais e
  ficam fora dos inventários imutáveis.

### Metadados e acoplamentos atuais

- clientes usam `.install/clients/ezquake/<plataforma>/<canal>.receipt`;
- componentes usam `.install/components/<componente>/{receipt,inventory}`;
- formatos planos antigos ainda são lidos e migrados;
- `manager.py` codifica ezQuake, macOS/Linux/Windows, stable/nightly, caminhos
  de runtime e vários diretórios QuakeWorld;
- `gameplay.py` mantém em Python uma tupla fixa com nome, gamedir, marcador,
  gamecode, mapa, perfil, versão, descrição e confirmação de cinco jogos;
- condicionais específicas controlam KTX, Pro-X, Team Fortress e gamecodes
  PR1; esse é o principal ponto a migrar para dados declarativos;
- `play` ainda pode materializar a camada interna `play-support` durante a
  execução. A generalização deve preparar essa camada em instalação, reparo ou
  upgrade, preservando o contrato de que gameplay não instala conteúdo.

### Testes atuais

As suítes em `maintenance/tests/` já cobrem catálogo, receitas, distribuição,
gerenciador, instalador, componentes modernos, upstreams e vários fluxos de
play/update/upgrade/uninstall. A integração adicionou testes de argumentos,
segurança de configuração, recibos, hashes e materialização reversível do KTX,
além de smokes reais de MVDSV/KTX, QTV e QWFWD no macOS arm64. A matriz real
Linux/Windows continua como validação de release em CI/máquina nativa.

## Baseline dos upstreams aprovados

| Runtime | Estado observado | Plataformas e lacunas observadas |
|---|---|---|
| [ezQuake](https://github.com/QW-Group/ezquake-source/releases/tag/3.6.9) | release `3.6.9`; nightly x86QW fixada no snapshot acima | assets oficiais para macOS universal, Linux x86-64 e Windows x64 |
| [KTX](https://github.com/QW-Group/ktx/releases/tag/1.47) | release `1.47`; o x86QW já usa o QVM oficial | nativos Linux/Windows e QVM; fonte confirma `BOT_SUPPORT`, modos competitivos, CA, CTF, Race, Midair e HoonyMode |
| [MVDSV](https://github.com/QW-Group/mvdsv/releases/tag/1.11) | release `1.11`, integrada como `1.11+x86qw.2` | Linux/Windows oficiais preservados; macOS arm64 reproduzido da fonte e corrigido para argumentos QVM de 64 bits |
| [QTV](https://github.com/QW-Group/qtv) | commit fixado `025ca949aca06cad6777de0075148ac06a15f4f0` | builds macOS arm64, Linux amd64 e Windows x64 incorporados; HTTP validado, upload desativado por padrão |
| [QWFWD](https://github.com/QW-Group/qwfwd/releases/tag/1.30) | release `1.30`, integrada como `1.30+x86qw.2` | builds macOS arm64, Linux amd64 e Windows x64 incorporados; bind local validado |
| [MVDParser](https://github.com/QW-Group/mvdparser/releases/tag/1.20) | release `1.20` | assets oficiais Linux e Windows; macOS não está declarado |
| [QWDTools](https://github.com/QW-Group/qwdtools/releases/tag/0.34) | release `0.34`, sem assets anexados no GitHub | builds oficiais e CI cobrem Linux e Windows; macOS não está declarado |
| [vkQuake](https://github.com/Novum/vkQuake/releases/tag/1.35.0) | release `1.35.0` | assets oficiais Linux x86-64 e Windows x64/arm64; macOS possui CI e build documentado, mas os binários indicados pelo upstream vêm de um provedor externo e exigem validação própria |
| [unezQuake](https://github.com/dusty-qw/unezquake/releases/tag/2.0.4) | release `2.0.4` | assets oficiais para macOS universal, Linux x86-64 e Windows x64 |

Essa tabela é evidência de planejamento, não uma autorização para incorporar a
versão automaticamente. A manutenção futura deve repetir a descoberta e fixar
o resultado no inventário antes de qualquer build.

## Decisões arquiteturais estabelecidas

1. ezQuake continua sendo o cliente QuakeWorld padrão.
2. unezQuake será opcional e experimental.
3. MVDSV será o servidor dedicado QuakeWorld padrão.
4. KTX será o gamecode competitivo e de treino principal.
5. vkQuake será o runtime de Quake clássico.
6. QuakeWorld e Quake clássico serão trilhas separadas.
7. O launcher passará a ser orientado a dados.
8. Novos componentes não serão baixados silenciosamente durante a execução.
9. Configurações pessoais continuarão fora dos inventários imutáveis.
10. Os perfis existentes serão preservados ou migrados explicitamente.
11. QTV e QWFWD serão opcionais.
12. MVDParser e QWDTools serão ferramentas independentes.
13. A inclusão de conteúdo comunitário continuará sendo curada.
14. Nenhuma nova engine própria será criada.
15. Runtimes não aprovados não serão incorporados.
16. Novos perfis serão conjuntos de capacidades combináveis com um perfil-base,
    e não cópias paralelas de todos os componentes.
17. `play`, `train`, `host`, `hub` e `demos` apenas executam e validam conteúdo
    já instalado; bootstrap, `upgrade` e um fluxo explícito de reparo iniciado
    pelo bootstrap continuam responsáveis por materializar payloads.

## Modelo de perfis decidido

O estado futuro separará um **perfil-base QuakeWorld** de **conjuntos de
capacidades**:

| Tipo | Valores | Semântica |
|---|---|---|
| perfil-base | `none`, `essential`, `recommended`, `complete`, `custom` | preserva exatamente a seleção atual e sua semântica de `upgrade` |
| capacidades | `training`, `host`, `replay`, `classic`, `experimental` | acrescenta dependências sem duplicar a definição do perfil-base |

| Seleção | Conteúdo principal planejado |
|---|---|
| `essential` | experiência QuakeWorld mínima já existente |
| `recommended` | experiência QuakeWorld recomendada já existente |
| `complete` | conteúdo QuakeWorld completo já existente |
| `custom` | seleção individual já existente |
| `training` | ezQuake, MVDSV, KTX com Frogbots e mapas/rotas de treino curados |
| `host` | MVDSV, KTX e QTV; QWFWD permanece escolha opcional explícita |
| `replay` | cliente compatível, MVDParser, QWDTools e suporte a demos |
| `classic` | vkQuake, `id1`, `hipnotic` e `rogue` |
| `experimental` | unezQuake e capacidades experimentais declaradas |

Composições como `recommended + training`, `essential + host` e `classic`
isolado tornam-se possíveis. `upgrade` converge cada conjunto registrado para
o manifesto atual, preserva extras e nunca adiciona um conjunto que o usuário
não escolheu. Instalações no formato 1 são interpretadas como perfil-base sem
capacidades até uma migração transacional.

## Superfície de CLI planejada

O menu sem argumentos continuará sendo a entrada humana, enquanto os mesmos
fluxos terão subcomandos determinísticos para uso manual e automação:

```text
x86qw play
x86qw play ktx [--mode <modo>]
x86qw play quake|hipnotic|rogue
x86qw train [--preset <preset>]
x86qw host [--preset <preset>]
x86qw hub
x86qw demos list
x86qw demos play <arquivo>
x86qw demos inspect <arquivo>
x86qw demos stats <arquivo>
x86qw demos convert <arquivo>
x86qw demos open-folder
x86qw update
x86qw upgrade
x86qw verify
x86qw cleanup
x86qw uninstall [--purge]
```

Operações que alteram estado oferecerão `--dry-run`, plano visível e confirmação;
`--yes` apenas responderá a essa confirmação. `--verbose` exibirá executáveis,
argumentos, diretórios e decisões de compatibilidade sem revelar senhas. Os
comandos de execução não baixarão nada: quando faltar conteúdo, indicarão o
componente e orientarão `upgrade` quando ele pertencer aos conjuntos registrados
ou a reexecução explícita do bootstrap para adicionar ou reparar componentes.
Mensagens permanecerão coerentes em português, serão acionáveis e terão forma
equivalente em macOS/Linux e Windows. `-nohome` será preservado onde o runtime o
suportar; isolamento equivalente será declarado por runtime, não presumido.

## Sequência, dependências e estimativa relativa

| Fase | Entrega | Complexidade relativa | Depende de |
|---|---|---:|---|
| 0 | levantamento, contratos, compatibilidade, perfis e matriz de testes | alta | baseline atual |
| 1 | launcher orientado a dados e migração de metadados | alta | fase 0 |
| 2 | modos KTX, Frogbots e presets; o comando `train` aguarda o runtime MVDSV | alta | fase 1 |
| 3 | MVDSV, conclusão de `train` e `host` em primeiro plano | alta | fase 1; KTX-01 e TRAIN-01 |
| 4 | QTV, QWFWD e perfis de transmissão | alta | fase 3 |
| 5 | central de demos e estatísticas | alta | fases 1 e 3 |
| 6 | vkQuake e campanhas clássicas | alta | fases 0–1 |
| 7 | unezQuake experimental | média | fases 0–1 |
| 8 | conteúdo curado, site e documentação pública | média/alta | fases 2–7 estáveis |
| 9 | estabilização, migrações finais e release 1.0 | alta | fases anteriores |

O caminho crítico é `ARCH-01 → ARCH-02/ARCH-03 → ARCH-04 → PLAY-01 →
MVDSV-01 → HOST-01`. A fase 2 possui dois gates: KTX-01/TRAIN-01 podem terminar
antes do servidor; TRAIN-02 só termina depois de MVDSV-01, na fase 3. Isso evita
um servidor alternativo apenas para cumprir a numeração. Demos e Quake clássico
podem avançar em paralelo depois que os contratos da fase 0 estiverem congelados.

## Definição global de pronto

Uma entrega deste roadmap só está pronta quando:

- o contrato declarativo e sua versão estão documentados;
- toda origem está fixada e todo artefato registra tamanho e SHA-256;
- dependências, compatibilidades, conflitos e plataformas são validados;
- upstream e camada x86QW continuam separados em `dist/`;
- instalação, atualização, verificação e remoção são transacionais;
- arquivos pessoais sobrevivem a update, upgrade, reparo e remoção normal;
- o catálogo público é uma projeção validada da fonte canônica;
- testes unitários, integração, migração e smoke aplicáveis passam;
- um smoke inicia e encerra o processo sem órfãos nem mutação pessoal;
- CLI, site e documentação refletem suporte e limitações reais;
- falha parcial possui recuperação testada;
- nenhuma operação de gameplay baixa conteúdo silenciosamente.

# Fase 0 — Levantamento e contratos

## [ARCH-01] Catálogo declarativo de runtimes

**Estado:** implementado\
**Prioridade:** P0\
**Complexidade:** alta\
**Depende de:** nenhuma\
**Bloqueia:** ARCH-02, ARCH-03, ARCH-04, PLAY-01, MVDSV-01, VKQ-01, UNEZ-01, DEMO-02

### Objetivo

Definir um contrato versionado capaz de representar cliente, servidor, serviço,
ferramenta, jogo, mod e conteúdo sem codificar cada novo runtime no Python.

### Estado atual

`PlatformSpec`, `PLATFORMS`, stable/nightly, executáveis e recibos do ezQuake
estão fixos em `manager.py`. O catálogo público conhece pacotes, mas não possui
uma entidade de runtime com capacidades e diretórios próprios.

### Escopo

- declarar identificador, nome, tipo, versão, canal, plataformas e arquiteturas;
- declarar protocolos, capacidades, executável, argumentos e variáveis de
  ambiente estritamente necessárias;
- declarar diretórios gerenciados, configuração, saves, demos e logs;
- declarar origem, artefatos, tamanho, SHA-256 e dependências;
- declarar componentes compatíveis e testes obrigatórios;
- versionar schema, regras de extensão e rejeição de campos desconhecidos;
- gerar a projeção do catálogo público a partir da fonte canônica.

### Fora do escopo

- mover bytes ou instalar novos runtimes neste épico;
- alterar o comportamento atual do ezQuake.

### Áreas provavelmente afetadas

- `maintenance/inventory/`;
- `maintenance/tools/`;
- `dist/clients/` e futuros namespaces de runtime;
- `site/public/api/v1/catalog.json`;
- `maintenance/tests/`.

### Critérios de aceite

- [ ] o schema representa todos os runtimes aprovados sem campos exclusivos
  obrigatórios de uma única engine;
- [ ] ezQuake stable/nightly atuais podem ser expressos sem perda de dados;
- [ ] plataformas indisponíveis são representadas explicitamente;
- [ ] todo artefato exige tamanho e SHA-256;
- [ ] dependências inexistentes e ciclos são rejeitados;
- [ ] a documentação inclui ao menos um exemplo por tipo de runtime.

### Testes necessários

- schema válido e inválido;
- ciclos, IDs duplicados e artefatos incompletos;
- projeção determinística para o catálogo público;
- equivalência do ezQuake atual no modelo novo.

### Riscos e decisões

- um schema excessivamente genérico pode esconder invariantes; usar blocos
  comuns com validação específica por tipo;
- canais pertencem ao runtime, não ao ecossistema inteiro;
- capacidades são enumeradas e versionadas, não texto livre.

## [ARCH-02] Protocolos, capacidades e matriz de compatibilidade

**Estado:** planejado\
**Prioridade:** P0\
**Complexidade:** alta\
**Depende de:** ARCH-01\
**Bloqueia:** PLAY-01, KTX-01, TRAIN-01, HOST-01, CLASSIC-01, UNEZ-01

### Objetivo

Permitir que catálogo e CLI respondam por dados qual runtime executa cada jogo,
qual protocolo é requerido e quais combinações são válidas por plataforma.

### Estado atual

O código presume QuakeWorld e ezQuake. Compatibilidade é uma política global
stable/nightly e não uma relação entre runtime, gamecode, protocolo e conteúdo.

### Escopo

- distinguir `quakeworld` e `netquake`;
- registrar `multiplayer-client`, `singleplayer`, `local-server`,
  `dedicated-server`, `mission-packs`, `bots`, `mvd-recording`, `qtv`, `proxy`,
  `demo-playback`, `demo-analysis` e `experimental-client`;
- declarar relação runtime ↔ jogo/mod ↔ componente ↔ plataforma;
- declarar configuração e testes exigidos por combinação;
- fornecer uma consulta única consumida por instalador, launcher e site;
- impedir combinações inválidas, incluindo campanhas clássicas no ezQuake.

### Fora do escopo

- simular compatibilidade não comprovada;
- tornar todos os runtimes disponíveis em todas as plataformas.

### Áreas provavelmente afetadas

- `maintenance/inventory/`;
- `dist/installer/bin/`;
- `site/public/api/v1/catalog.json`;
- `maintenance/tests/test_catalog.py`.

### Critérios de aceite

- [ ] cada combinação responde runtime, protocolo, dependências e testes;
- [ ] uma plataforma sem artefato produz indisponibilidade acionável;
- [ ] Quake, Hipnotic e Rogue resolvem somente para vkQuake;
- [ ] KTX resolve para ezQuake no play atual e para MVDSV no host/treino;
- [ ] unezQuake aparece apenas quando instalado e compatível;
- [ ] a matriz é exposta ao site sem duplicação manual.

### Testes necessários

- consultas positivas e negativas;
- matriz macOS/Linux/Windows;
- protocolo incompatível;
- componente ausente e runtime ausente.

### Riscos e decisões

- capacidades controversas do cliente experimental precisam ser visíveis;
- suporte de fonte não equivale a artefato distribuível: registrar os dois
  estados separadamente.

## [ARCH-03] Manifestos declarativos de jogos e modos

**Estado:** planejado\
**Prioridade:** P0\
**Complexidade:** alta\
**Depende de:** ARCH-01, ARCH-02\
**Bloqueia:** PLAY-01, KTX-01, CLASSIC-02

### Objetivo

Externalizar nome, gamedir, marcador, gamecode, mapa, perfil, argumentos,
versão, runtime e confirmação hoje definidos em `LOCAL_GAMES`.

### Estado atual

KTX, Final Arena, Pro-X, Team Fortress e TD2 são uma tupla Python fixa, com
condicionais adicionais por chave. Perfis de cliente já vivem em `dist/`, mas
os metadados para encontrá-los e executá-los estão no código.

### Escopo

- definir manifesto de jogo e manifesto de modo/preset;
- representar KTX, Final Arena, Pro-X, Team Fortress, TD2, Quake, Scourge of
  Armagon e Dissolution of Eternity;
- declarar mapa padrão, mapas sugeridos/compatíveis e política de listagem;
- declarar argumentos antes/depois do mapa, configuração gerenciada e arquivo
  pessoal;
- declarar marcador, gamecode, mensagem de confirmação e smoke test;
- validar caminhos relativos, ordem de argumentos e referências a componentes;
- admitir extensões controladas sem condicionais por ID no executor.

### Fora do escopo

- converter todos os comandos internos de engines em uma linguagem genérica;
- oferecer jogos ainda não empacotados.

### Áreas provavelmente afetadas

- `maintenance/inventory/`;
- `dist/mods/*/*/x86qw/`;
- futuro `dist/games/` ou namespace equivalente decidido no contrato;
- `dist/installer/bin/gameplay.py`;
- `maintenance/tests/test_modern_components.py`.

### Critérios de aceite

- [ ] os cinco jogos atuais são descritos sem perder nenhum argumento;
- [ ] os três jogos clássicos cabem no mesmo contrato sem compartilhar config;
- [ ] caminhos absolutos, travessia e argumentos não permitidos são rejeitados;
- [ ] a ordem declarada reproduz os comandos atuais byte a byte onde aplicável;
- [ ] a adição de uma fixture de jogo não exige editar Python.

### Testes necessários

- schema de jogos;
- geração de comandos para cada jogo atual;
- manifesto malicioso ou inconsistente;
- compatibilidade runtime/jogo;
- golden tests de ordem de argumentos.

### Riscos e decisões

- argumentos são uma lista tipada, nunca uma string entregue ao shell;
- arquivos pessoais são referências de runtime e nunca payload imutável.

## [ARCH-04] Namespaces de instalação e migração de metadados

**Estado:** planejado\
**Prioridade:** P0\
**Complexidade:** alta\
**Depende de:** ARCH-01\
**Bloqueia:** PLAY-01, MVDSV-01, QTV-01, QWFWD-01, DEMO-02, VKQ-01, UNEZ-01

### Objetivo

Generalizar `.install/` para clientes, servidores, serviços, ferramentas e
componentes, preservando instalações e recibos existentes.

### Estado atual

Clientes ezQuake e componentes já usam diretórios contextuais. O código mantém
leitura de layouts planos antigos, mas não há namespaces para as novas classes.

### Escopo

- adotar `.install/clients/<cliente>/<plataforma>/<canal>/`;
- adotar `.install/servers/<servidor>/<plataforma>/`;
- adotar `.install/services/<serviço>/<plataforma>/`;
- adotar `.install/tools/<ferramenta>/<plataforma>/`;
- preservar `.install/components/<componente>/`;
- definir recibo versionado, inventário, transação, backup e rollback;
- ler recibos antigos e migrar unilateralmente durante update/upgrade/reparo;
- manter aliases legados por uma janela de duas releases públicas compatíveis,
  removendo-os somente após telemetria manual de testes e fixture de migração;
- registrar falha recuperável sem apagar metadados antigos.

### Fora do escopo

- migrar configurações pessoais para dentro de `.install/`;
- remover código legado antes da janela definida.

### Áreas provavelmente afetadas

- `dist/installer/bin/manager.py`;
- `.install/` nas fixtures de teste;
- `dist/installer/docs/installer.md`;
- `maintenance/tests/test_installer.py`.

### Critérios de aceite

- [ ] recibos atuais stable/nightly continuam legíveis;
- [ ] migração interrompida restaura o layout anterior;
- [ ] update e upgrade mostram a migração no plano;
- [ ] cada classe pode ser verificada e removida independentemente;
- [ ] nenhum arquivo pessoal entra no inventário;
- [ ] fixtures das versões públicas existentes migram com resultado idêntico.

### Testes necessários

- migração de layout plano e layout contextual atual;
- interrupção entre troca de payload e recibo;
- rollback e reexecução idempotente;
- Windows, macOS e Linux path semantics.

### Riscos e decisões

- diretórios de saves e demos são descobertos pelo runtime, mas sua propriedade
  continua pessoal;
- a janela de compatibilidade só começa quando o primeiro schema novo for
  publicado, não na data deste roadmap.

## [ARCH-05] Composição de perfis e capacidades

**Estado:** planejado\
**Prioridade:** P0\
**Complexidade:** média\
**Depende de:** ARCH-01, ARCH-02\
**Bloqueia:** TRAIN-02, HOST-02, DEMO-01, CLASSIC-01, UNEZ-01

### Objetivo

Evoluir os perfis sem renomear nem duplicar `essential`, `recommended`,
`complete` e `custom`.

### Estado atual

O formato 1 registra um único perfil e resolve dependências de componentes. O
histórico de fingerprints já permite upgrades após mudança de perfil.

### Escopo

- criar formato de estado com perfil-base e conjuntos de capacidades;
- declarar `training`, `host`, `replay`, `classic` e `experimental`;
- resolver clientes, servidores, serviços, ferramentas e componentes em um DAG;
- preservar histórico de fingerprints por conjunto;
- manter `update` conservador e `upgrade` convergente ao que foi registrado;
- preservar componentes extras e informar conflitos;
- migrar formato 1 para perfil-base sem capacidades.

### Fora do escopo

- instalar todos os conjuntos pelo perfil `complete` sem decisão explícita;
- transformar perfis em uma loja de conteúdo.

### Áreas provavelmente afetadas

- `maintenance/inventory/components.json` e novos manifestos;
- `.install/state.json`;
- `dist/installer/bin/manager.py`;
- catálogo e documentação.

### Critérios de aceite

- [ ] instalações antigas preservam exatamente seus componentes;
- [ ] `recommended + training` não duplica ezQuake, KTX ou mapas;
- [ ] `classic` pode existir sem perfil-base QuakeWorld;
- [ ] `upgrade` adiciona novidade somente a conjuntos registrados;
- [ ] remoção de um conjunto não remove dependência ainda usada por outro;
- [ ] `custom` continua registrando escolhas explícitas.

### Testes necessários

- resolução e ciclos;
- migração do estado formato 1;
- update versus upgrade;
- componentes compartilhados e extras preservados.

### Riscos e decisões

- perfis são intenção persistente; seleção ad hoc continua sendo `custom`;
- serviços opcionais, como QWFWD, precisam de escolha própria mesmo dentro de
  `host` quando a plataforma não os suportar.

## [TEST-01] Contrato de testes e fixtures do ecossistema

**Estado:** planejado\
**Prioridade:** P0\
**Complexidade:** média\
**Depende de:** ARCH-01, ARCH-02, ARCH-03, ARCH-04\
**Bloqueia:** todas as fases de runtime

### Objetivo

Definir antes da implementação a matriz mínima, fixtures e protocolo de smoke
para cada categoria nova.

### Estado atual

Há cobertura ampla de lógica Python, mas poucos processos reais e nenhuma
matriz completa de servidores, serviços, ferramentas e campanhas.

### Escopo

- definir fixtures de instalação atual e layouts legados;
- definir sandbox temporário sem usar diretórios pessoais reais;
- padronizar timeout, captura de log, encerramento e detecção de órfãos;
- declarar por runtime o sinal de prontidão e o código de saída esperado;
- separar teste offline, teste com rede e teste gráfico;
- criar matriz de suporte esperado, indisponível e ainda não validado.

### Fora do escopo

- implementar todos os smokes das fases posteriores neste épico.

### Áreas provavelmente afetadas

- `maintenance/tests/`;
- futuros `maintenance/fixtures/` e helpers de teste;
- CI.

### Critérios de aceite

- [ ] cada runtime aprovado possui uma especificação de smoke;
- [ ] nenhuma suite escreve em configuração pessoal real;
- [ ] falta de suporte é skip justificado, não sucesso falso;
- [ ] processos órfãos falham o teste;
- [ ] fixtures cobrem ao menos as versões públicas atuais do instalador.

### Testes necessários

- autoteste do harness;
- timeout e kill controlado;
- detecção de mutação pessoal;
- execução paralela sem colisão de porta.

### Riscos e decisões

- runtimes gráficos podem exigir runners próprios; a ausência de runner não
  reduz os critérios de release, apenas muda onde o smoke é executado.

# Fase 1 — Launcher orientado a dados

## [PLAY-01] Descoberta e execução declarativa de jogos

**Estado:** planejado\
**Prioridade:** P0\
**Complexidade:** alta\
**Depende de:** ARCH-02, ARCH-03, ARCH-04, TEST-01\
**Bloqueia:** KTX-01, TRAIN-02, CLASSIC-02, UNEZ-01

### Objetivo

Entregar `x86qw play`, `x86qw play <jogo>` e seleção de runtime sem uma tupla
Python por jogo.

### Estado atual

`play` já valida componentes, lista mapas, seleciona ezQuake stable/nightly e
preserva `-nohome`, mas só funciona por menu e contém ramificações por mod.

### Escopo

- descobrir runtimes e jogos pelos recibos e manifestos;
- filtrar combinações compatíveis e escolher o runtime padrão;
- permitir escolha manual quando houver mais de um runtime;
- suportar `play ktx`, `play quake`, `play hipnotic` e `play rogue`;
- preservar menu interativo equivalente;
- listar mapas quando o manifesto permitir;
- validar recibos antes de executar;
- orientar bootstrap, upgrade ou reparo quando faltar conteúdo;
- mover a preparação de `play-support` para instalação/reparo/upgrade;
- manter configuração pessoal e isolamento adequados a cada runtime.

### Fora do escopo

- baixar ou instalar conteúdo durante `play`;
- iniciar servidor dedicado ou treino, que terão comandos próprios.

### Áreas provavelmente afetadas

- `dist/installer/bin/gameplay.py`;
- `dist/installer/bin/manager.py`;
- manifestos de runtime/jogo;
- `maintenance/tests/test_modern_components.py`.

### Critérios de aceite

- [ ] os cinco mods atuais executam com os mesmos argumentos e confirmações;
- [ ] `play <id>` funciona sem prompt quando há uma combinação inequívoca;
- [ ] múltiplos clientes geram seleção explícita ou `--runtime`;
- [ ] componente ausente gera instrução acionável e zero downloads;
- [ ] nenhuma execução escreve um payload gerenciado novo;
- [ ] arquivos pessoais permanecem inalterados salvo escrita normal da engine.

### Testes necessários

- golden tests dos cinco mods atuais;
- seleção stable/nightly;
- runtime e componente ausentes;
- mapa inválido;
- smoke real de cada mod em stable e nightly.

### Riscos e decisões

- preservar aliases e caminhos legados durante a janela de migração;
- separar executor de processo, resolução de compatibilidade e UI.

## [CLI-01] Árvore de comandos e experiência consistente

**Estado:** planejado\
**Prioridade:** P1\
**Complexidade:** média\
**Depende de:** ARCH-01, PLAY-01\
**Bloqueia:** TRAIN-02, HOST-01, DEMO-01

### Objetivo

Preparar a CLI para `play`, `train`, `host`, `hub` e `demos` sem perder a
separação entre bootstrap e CLI permanente.

### Estado atual

O parser usa uma ação posicional plana e os launchers mantêm listas próprias de
comandos. `--dry-run` e `--yes` só existem para update/upgrade, corretamente.

### Escopo

- introduzir subcomandos mantendo aliases temporários da sintaxe atual;
- mostrar menu/ajuda quando não houver argumento;
- padronizar `--verbose`, `--no-color`, erros e códigos de saída;
- reservar `--dry-run` para operações que alteram estado;
- aceitar `--yes` somente onde existe confirmação explícita;
- manter mensagens em português e saída útil em logs;
- manter equivalência de argumentos no Windows sem montar comandos de shell;
- gerar ou validar a ajuda dos launchers a partir do contrato de comandos.

### Fora do escopo

- reescrever o instalador;
- permitir `install`, `components` ou `presets` na CLI permanente.

### Áreas provavelmente afetadas

- `dist/installer/bin/manager.py`;
- `dist/installer/bin/gameplay.py`;
- `dist/installer/bin/x86qw.sh` e `x86qw.cmd`;
- documentação e testes de CLI.

### Critérios de aceite

- [ ] ajuda e comandos são coerentes em Unix e Windows;
- [ ] a CLI instalada continua rejeitando instalação arbitrária;
- [ ] comandos sem alteração não aceitam `--yes` sem motivo;
- [ ] erros apontam componente, runtime ou ação corretiva;
- [ ] scripts não duplicam manualmente a lista completa de capacidades.

### Testes necessários

- parsing e códigos de saída;
- CLI de desenvolvimento versus CLI instalada;
- quoting no Windows e Unix;
- help snapshot.

### Riscos e decisões

- manter compatibilidade de comandos atuais durante duas releases públicas;
- flags específicas ficam no subcomando correspondente.

# Fase 2 — Modos KTX e treino

## [KTX-01] Presets de modos KTX confirmados

**Estado:** implementado\
**Prioridade:** P1\
**Complexidade:** média\
**Depende de:** PLAY-01, ARCH-03\
**Bloqueia:** TRAIN-02, HOST-01

### Objetivo

Expor KTX como conjunto de modos verificados, e não somente como uma entrada
genérica.

### Estado atual

KTX `1.47+x86qw.12` expõe 17 usermodes e sete variações oficiais por menu e
`play ktx --mode`. Mapa, assets, combinações incompatíveis e opções específicas
de Frogbot, CTF e Race são validados antes da abertura.

### Escopo

- auditar na versão fixada `duel`, `2on2`, `4on4`, `ffa`, `ctf`,
  `clan-arena`, `race`, `hoony`, `midair` e `practice`;
- marcar `practice` como preset x86QW se não for modo upstream autônomo;
- declarar jogadores recomendados, mapas, configs, comandos, regras e bots;
- fornecer `play ktx --mode <id>` e menu equivalente;
- documentar configuração visual sugerida sem sobrescrever preferências;
- exigir smoke específico por modo publicado.

### Fora do escopo

- declarar modo cuja inicialização não foi confirmada no KTX fixado;
- criar regras ou mecânicas novas no gamecode.

### Áreas provavelmente afetadas

- `dist/mods/ktx/<versão>/x86qw/`;
- manifestos de jogos/modos;
- launcher e documentação;
- testes KTX.

### Critérios de aceite

- [ ] cada modo publicado possui evidência no upstream e smoke reproduzível;
- [ ] mapas ausentes removem a opção ou geram erro acionável;
- [ ] comandos não alteram configuração pessoal permanentemente;
- [ ] CLI e menu mostram somente modos realmente suportados;
- [ ] `practice` é identificado como preset, caso seja composição x86QW.

### Testes necessários

- geração de comando por modo;
- smoke de carregamento e confirmação de serverinfo;
- mapa compatível/incompatível;
- regressão do KTX genérico atual.

### Riscos e decisões

- nomes comunitários podem divergir dos usermodes upstream; o manifesto mantém
  ID x86QW e comando upstream separados;
- CTF e Race podem exigir assets/ENT/rotas adicionais declarados.

## [TRAIN-01] Artefato KTX com Frogbots e dados de navegação

**Estado:** implementado no launcher local\
**Prioridade:** P0\
**Complexidade:** alta\
**Depende de:** ARCH-01, ARCH-02, TEST-01\
**Bloqueia:** TRAIN-02

### Objetivo

Garantir um gamecode KTX compatível com MVDSV, bots habilitados e dados de
navegação curados para treino.

### Estado atual

O pacote preserva o QVM oficial com `BOT_SUPPORT`, 77 arquivos `.bot` e suas
dependências. O launcher oferece quantidade/fill, skill, equipe, arma e vida e
rejeita previamente mapas sem rota ou modos incompatíveis.

### Escopo

- verificar se o QVM atual foi construído com bots;
- produzir build fixado com `BOT_SUPPORT` quando a verificação for insuficiente;
- validar QVM e, onde necessário, gamecode nativo contra MVDSV;
- inventariar comandos de adicionar/remover bot, skill e equipe;
- curar arquivos `.bot`, mapas e marcadores consumidos pelos presets;
- declarar comportamento quando um mapa não possui navegação;
- registrar origem, tamanho, SHA-256 e compatibilidade de cada conjunto;
- testar entrada de bot e execução mínima sem crash.

### Fora do escopo

- gerar automaticamente rotas para todo mapa;
- habilitar bots no servidor local do ezQuake sem validação separada.

### Áreas provavelmente afetadas

- `dist/mods/ktx/`;
- futuros componentes de treino em `dist/`;
- receitas e inventários;
- testes de runtime.

### Critérios de aceite

- [ ] o artefato declara inequivocamente suporte a bots;
- [ ] ao menos os mapas dos presets iniciais possuem dados confirmados;
- [ ] bot entra, recebe skill/equipe e permanece ativo pelo período do smoke;
- [ ] ausência de navegação é detectada antes de iniciar;
- [ ] gamecode funciona no MVDSV das plataformas suportadas;
- [ ] nenhum dado de treino invade configuração pessoal.

### Testes necessários

- inspeção/build de `BOT_SUPPORT`;
- smoke MVDSV + KTX + bot por tempo mínimo definido no harness;
- skill/equipe e remoção;
- mapa sem `.bot`;
- hash e remoção do componente.

### Riscos e decisões

- QVM é a primeira opção por portabilidade; binários nativos entram somente
  quando houver ganho ou requisito comprovado;
- dados de navegação são conteúdo curado com recibo próprio quando puderem
  evoluir independentemente.

## [TRAIN-02] Comando `train` e presets de treino

**Estado:** substituído por `play ktx` e `host`\
**Prioridade:** P1\
**Complexidade:** alta\
**Depende de:** KTX-01, TRAIN-01, MVDSV-01, ARCH-05, CLI-01\
**Bloqueia:** RELEASE-01

### Objetivo

Entregar treino KTX com seleção interativa e automação, sem duplicar a
superfície de execução.

### Estado atual

O desenho final não cria um comando `train` paralelo. `play ktx` concentra
modos e Frogbots no cliente local; `host` cobre sessões dedicadas. Quantidade ou
fill, skill, equipe, arma e vida têm validação declarativa, e mapas sem rota
falham antes da abertura.

### Escopo

- selecionar modalidade, mapa, bots, dificuldade, equipe, duração, fraglimit,
  armas, itens, powerups, respawn e comportamento;
- criar presets Duel iniciante/intermediário/avançado, Aim Lightning Gun,
  Rocket Arena, Movement, Rocket Jump, Teamplay e Item Control;
- declarar todos os comandos usados pelo preset;
- permitir overrides de CLI sem modificar o manifesto;
- usar diretório pessoal de treino separado da configuração gerenciada;
- mostrar comando final em `--verbose`;
- orientar bootstrap/upgrade/reparo se faltar dependência.

### Fora do escopo

- baixar mapas ou rotas durante `train`;
- alterar o gamecode para criar novos comportamentos.

### Áreas provavelmente afetadas

- CLI permanente;
- manifestos de treino;
- configuração x86QW de KTX/MVDSV;
- documentação de treino.

### Critérios de aceite

- [ ] cada preset inicia com bots e estado esperado;
- [ ] opções de CLI e menu produzem o mesmo plano;
- [ ] mapa sem navegação falha antes de abrir o servidor;
- [ ] configurações pessoais sobrevivem a update/upgrade;
- [ ] `train` não baixa nem instala componentes;
- [ ] processo encerra sem servidor órfão.

### Testes necessários

- cada preset;
- overrides e validação de limites;
- falta de runtime/gamecode/rota;
- smoke prolongado e encerramento.

### Riscos e decisões

- presets de aim/movement podem ser composições de cvars, não modos KTX;
- defaults devem ser seguros e documentados, sem persistir senha ou bind.

# Fase 3 — Servidor dedicado

## [MVDSV-01] Componente independente MVDSV

**Estado:** implementado\
**Prioridade:** P0\
**Complexidade:** alta\
**Depende de:** ARCH-01, ARCH-02, ARCH-04, TEST-01\
**Bloqueia:** TRAIN-02, HOST-01, QTV-01

### Objetivo

Incorporar MVDSV como servidor dedicado versionado, verificável, atualizável e
removível.

### Estado atual

MVDSV `1.11+x86qw.2` está em `dist/servers/mvdsv/`, com fonte, artefatos das
três plataformas, licença, hashes e patch macOS arm64 registrado. Smoke real
carregou KTX 1.47, duel, 4on4 e Race e encerrou sem resíduos temporários.

### Escopo

- fixar versão, fonte e artefatos por plataforma;
- priorizar Linux x86-64, Windows x64 e macOS Apple Silicon quando validável;
- separar upstream de configuração x86QW;
- criar recibo, inventário, verificação, update, remoção e rollback;
- declarar executável, portas, diretórios de config, logs e demos;
- validar KTX QVM e gravação MVD;
- preservar configuração administrativa fora do inventário.

### Fora do escopo

- instalar serviço do sistema no MVP;
- configurar firewall ou roteador.

### Áreas provavelmente afetadas

- futuro `dist/servers/mvdsv/`;
- inventários, receitas e catálogo;
- gerenciador e testes;
- documentação de servidor.

### Critérios de aceite

- [ ] cada plataforma anunciada possui artefato executado em smoke real;
- [ ] suporte indisponível é refletido no catálogo e na CLI;
- [ ] MVDSV inicia, carrega KTX, troca mapa e encerra limpo;
- [ ] gravação MVD produz arquivo válido em diretório pessoal;
- [ ] update preserva senhas, hostname e personalizações;
- [ ] remoção preserva demos e config pessoal por padrão.

### Testes necessários

- artefato/hash por plataforma;
- processo, porta pronta e encerramento;
- conexão local de ezQuake;
- KTX, troca de mapa e MVD;
- update/rollback/uninstall.

### Riscos e decisões

- a lacuna do asset macOS é uma investigação bloqueadora específica;
- portas de teste devem ser alocadas dinamicamente para evitar colisão.

## [HOST-01] Comando `host` e presets de servidor

**Estado:** implementado em primeiro plano\
**Prioridade:** P1\
**Complexidade:** alta\
**Depende de:** MVDSV-01, KTX-01, CLI-01\
**Bloqueia:** QTV-01, QWFWD-01, HOST-02

### Objetivo

Entregar hospedagem em primeiro plano com configuração explícita e preservável.

### Estado atual

`x86qw host` apresenta os jogos instalados de `play` e inicia somente o MVDSV.
KTX, Final Arena, Pro-X, Team Fortress e Total Destruction 2 aceitam mapa
explícito; KTX também aceita modos, bots e regras dedicadas de CTF/Race. O
comando seleciona bind, porta, hostname, limites e senhas, grava configuração
efêmera privada, coordena serviços opcionais e trata encerramento. Loopback é o
padrão; exposição externa é explícita.

### Escopo

- selecionar gamecode, modo, mapa, porta, LAN/externo, hostname e maxclients;
- configurar senhas de jogador, espectador e administração sem expô-las em log;
- ativar MVD e escolher diretório pessoal;
- preparar flags para QTV e QWFWD sem torná-los requisitos;
- fornecer presets duel, 2on2, 4on4, ffa, ctf, training, lan e tournament;
- executar em primeiro plano e tratar sinais/encerramento;
- mostrar comando final sanitizado em `--verbose`;
- manter novos gamecodes compatíveis no mesmo catálogo compartilhado com `play`.

### Fora do escopo

- daemonizar ou instalar serviço no MVP;
- abrir automaticamente portas externas.

### Áreas provavelmente afetadas

- CLI;
- manifestos de host/modos;
- configs MVDSV/KTX x86QW;
- testes de integração.

### Critérios de aceite

- [x] jogos e modos iniciam e aceitam consulta local no MVDSV;
- [x] troca de mapa, KTX e encerramento são confirmados;
- [ ] MVD opcional é gravado e validado;
- [x] gamedir fica selecionado sem iniciar o processo do cliente;
- [x] senhas e config pessoal sobrevivem a update;
- [x] `host` não baixa conteúdo e informa dependências ausentes.

### Testes necessários

- preset por modo;
- LAN versus bind externo;
- senha e redaction de logs;
- conexão/observação local;
- SIGINT/CTRL+C e processo órfão.

### Riscos e decisões

- acesso externo é configuração do usuário, nunca promessa de conectividade;
- primeiro plano simplifica diagnóstico e é requisito do MVP.

## [HOST-02] Execução opcional como serviço do sistema

**Estado:** planejado, pós-MVP\
**Prioridade:** P2\
**Complexidade:** alta\
**Depende de:** HOST-01, QTV-01, QWFWD-01\
**Bloqueia:** nenhuma entrega MVP

### Objetivo

Planejar lifecycle persistente via systemd, Windows Service e launchd após o
modo em primeiro plano estar estável.

### Estado atual

Não há instalação de serviço, o que deve permanecer no MVP.

### Escopo

- gerar plano explícito e exigir confirmação;
- instalar/remover definição de serviço com caminhos absolutos validados;
- executar com usuário sem privilégio quando possível;
- separar logs, config e credenciais;
- fornecer status, start, stop e diagnóstico;
- atualizar sem deixar processo usando binário antigo.

### Fora do escopo

- ativação automática durante instalação inicial;
- elevação silenciosa de privilégio.

### Áreas provavelmente afetadas

- CLI e templates por SO;
- documentação administrativa;
- testes privilegiados isolados.

### Critérios de aceite

- [ ] cada integração é opt-in e reversível;
- [ ] uninstall detecta serviço ativo e orienta parada;
- [ ] credenciais não entram no inventário nem em logs;
- [ ] update possui estratégia de stop/swap/start com rollback.

### Testes necessários

- instalação/remoção em ambiente descartável;
- reboot simulado quando possível;
- update e falha de restart;
- ausência de privilégios.

### Riscos e decisões

- esse épico pode ser adiado para depois de 1.0 se não houver ambiente seguro de
  teste por plataforma.

# Fase 4 — Transmissão e proxy

## [QTV-01] Serviço opcional QTV

**Estado:** implementado no MVP HTTP\
**Prioridade:** P1\
**Complexidade:** alta\
**Depende de:** ARCH-04, MVDSV-01, HOST-01\
**Bloqueia:** HOST-03

### Objetivo

Empacotar QTV como serviço independente, executável sozinho ou junto ao host.

### Estado atual

O commit upstream está fixado e empacotado para as três plataformas. `x86qw
qtv` executa separadamente; `host --with-qtv` conecta ao MVDSV e coordena o
lifecycle. HTTP e stream ao vivo foram validados; upload fica desativado.

### Escopo

- decidir e fixar commit upstream ou aguardar release, registrando a decisão;
- produzir artefatos reproduzíveis nas plataformas validadas;
- declarar porta, diretório de demos, logs e config;
- conectar ao MVDSV e iniciar junto ou separadamente;
- suportar encerramento coordenado e independente;
- validar HTTP, upload quando habilitado e IPv6 quando disponível;
- entregar HTTP no MVP e HTTPS em etapa posterior com configuração explícita;
- testar conexão de espectador pelo ezQuake;
- integrar ao `hub` e ao `host` sem substituir o QTV externo já consumido.

### Fora do escopo

- exigir QTV para hospedar ou jogar;
- emitir certificado automaticamente no MVP.

### Áreas provavelmente afetadas

- futuro `dist/services/qtv/`;
- catálogo, recibos e configs;
- `hub`, `host` e testes.

### Critérios de aceite

- [ ] versão/commit e toolchain Go estão fixados;
- [ ] plataformas publicadas passam smoke real;
- [ ] QTV conecta ao MVDSV e aceita espectador;
- [ ] logs e demos usam diretórios declarados;
- [ ] start/stop conjunto não deixa órfãos;
- [ ] IPv6 e HTTPS aparecem apenas quando validados.

### Testes necessários

- build reproduzível;
- HTTP e stream;
- upload habilitado/desabilitado;
- IPv4/IPv6 onde disponível;
- lifecycle separado e conjunto.

### Riscos e decisões

- a ausência de release exige política de pin por commit e versão derivada;
- macOS permanece indisponível até build e smoke próprios serem comprovados.

## [QWFWD-01] Serviço opcional QWFWD

**Estado:** implementado no MVP\
**Prioridade:** P2\
**Complexidade:** média/alta\
**Depende de:** ARCH-04, HOST-01\
**Bloqueia:** HOST-03

### Objetivo

Oferecer proxy QuakeWorld independente e opcional, sem se tornar requisito de
partida local.

### Estado atual

QWFWD `1.30+x86qw.2` está empacotado para as três plataformas. `x86qw proxy`
executa separadamente e `host --with-proxy` coordena seu lifecycle; o perfil
gerenciado não consulta masters públicos automaticamente.

### Escopo

- fixar fonte e artefatos nas plataformas validadas;
- declarar porta, logs e configuração;
- permitir execução independente ou associada a um perfil host;
- separar configuração gerenciada e pessoal;
- testar encaminhamento cliente → proxy → servidor;
- documentar limitações de rota e exposição externa.

### Fora do escopo

- exigir proxy para jogar, treinar ou hospedar localmente;
- prometer redução de latência.

### Áreas provavelmente afetadas

- futuro `dist/services/qwfwd/`;
- catálogo, host/hub e documentação;
- testes de rede.

### Critérios de aceite

- [ ] a versão 1.30 ou sucessora verificada está fixada;
- [ ] cada plataforma anunciada possui artefato e smoke;
- [ ] encaminhamento local funciona e encerra limpo;
- [ ] ausência na plataforma é exibida sem quebrar outros perfis;
- [ ] configuração pessoal sobrevive a update/remove normal.

### Testes necessários

- pacote/hash;
- bind e encaminhamento;
- porta ocupada;
- start/stop independente;
- remoção preservando logs pessoais.

### Riscos e decisões

- builds hospedados fora da release precisam ser espelhados após validação de
  hash e proveniência;
- macOS não entra até suporte verificável existir.

## [HOST-03] Perfis de host e integração com Hub

**Estado:** planejado\
**Prioridade:** P1\
**Complexidade:** média\
**Depende de:** QTV-01, QWFWD-01, ARCH-05\
**Bloqueia:** SITE-01, RELEASE-01

### Objetivo

Compor `host-minimal`, `host-broadcast` e `host-complete` e evoluir o Hub para
observar e diagnosticar a stack instalada.

### Estado atual

O Hub já entra, observa e abre streams QTV públicos. Não controla serviços
locais nem mostra capacidades instaladas.

### Escopo

- `host-minimal`: MVDSV + KTX;
- `host-broadcast`: MVDSV + KTX + QTV;
- `host-complete`: MVDSV + KTX + QTV + QWFWD onde suportado;
- validar a composição contra plataforma e recibos;
- permitir iniciar stack e mostrar endpoints locais;
- preservar o fluxo atual de servidores públicos;
- apresentar status e logs acionáveis no Hub.

### Fora do escopo

- hospedar servidor público administrado pelo projeto;
- instalar serviço persistente automaticamente.

### Áreas provavelmente afetadas

- manifestos de perfil;
- `hub` e `host`;
- documentação e site.

### Critérios de aceite

- [ ] cada perfil resolve dependências sem duplicação;
- [ ] indisponibilidade do QWFWD reduz opções, não quebra host mínimo;
- [ ] Hub mantém todas as funções atuais;
- [ ] endpoints e processos locais são verificáveis;
- [ ] encerramento coordenado respeita ordem inversa de dependências.

### Testes necessários

- resolução de perfis;
- lifecycle conjunto;
- regressão do Hub atual;
- plataforma parcialmente suportada.

### Riscos e decisões

- perfis de execução não substituem o conjunto `host` de instalação; são
  presets operacionais sobre componentes já instalados.

# Fase 5 — Demos e estatísticas

## [DEMO-01] Descoberta, listagem e reprodução de demos

**Estado:** planejado\
**Prioridade:** P1\
**Complexidade:** média\
**Depende de:** PLAY-01, ARCH-05\
**Bloqueia:** DEMO-02, DEMO-03, DEMO-04

### Objetivo

Tornar demos pessoais descobríveis e reproduzíveis sem movê-las.

### Estado atual

`cleanup` conhece alguns diretórios de demos e os preserva, mas não existe
central de demos nem associação declarativa de formatos.

### Escopo

- implementar contratos para `demos list`, `play` e `open-folder`;
- descobrir demos do ezQuake e MVDSV;
- reconhecer MVD, QWD e formatos adicionais somente após confirmação no cliente;
- normalizar caminho, formato, tamanho, data e origem;
- associar formato a runtime compatível;
- permitir stable/nightly e escolha manual;
- nunca mover, renomear ou inventariar arquivos pessoais sem confirmação.

### Fora do escopo

- análise estatística e conversão, tratadas nos épicos seguintes;
- upload automático.

### Áreas provavelmente afetadas

- CLI e manifestos de formato;
- diretórios pessoais declarados por runtime;
- testes de demos.

### Critérios de aceite

- [ ] demos de cliente e servidor são listadas sem duplicação;
- [ ] `demos play <arquivo>` escolhe apenas runtime compatível instalado;
- [ ] stable/nightly podem ser selecionados;
- [ ] arquivo fora dos diretórios conhecidos exige caminho explícito;
- [ ] nenhuma operação altera o original.

### Testes necessários

- descoberta em múltiplos diretórios;
- symlink/path traversal;
- formato desconhecido;
- reprodução por cliente e canal.

### Riscos e decisões

- metadados rápidos não devem exigir parse completo;
- associação de arquivo do sistema operacional fica para etapa posterior e
  opt-in.

## [DEMO-02] Integração independente do MVDParser

**Estado:** planejado\
**Prioridade:** P1\
**Complexidade:** alta\
**Depende de:** ARCH-04, DEMO-01, TEST-01\
**Bloqueia:** DEMO-04

### Objetivo

Entregar `demos inspect` e `demos stats` com saída textual e JSON normalizada.

### Estado atual

MVDParser 1.20 não está no projeto. Seus assets oficiais cobrem Linux e
Windows; macOS exige build e validação próprios ou deve aparecer indisponível.

### Escopo

- fixar fonte, artefatos, `fragfile.dat` e template consumido;
- criar recibo/inventário por plataforma;
- adaptar a saída para modelo x86QW sem perder o output bruto;
- extrair jogadores, mapa, modo, duração, frags, mortes, eficiência, armas,
  powerups e eventos realmente disponíveis;
- produzir JSON versionado e texto legível;
- tratar demos inválidas, abortadas e incompletas;
- preservar o arquivo original.

### Fora do escopo

- inventar métricas ausentes no parser;
- exigir backend remoto.

### Áreas provavelmente afetadas

- futuro `dist/tools/mvdparser/`;
- CLI, catálogo e schemas de saída;
- testes/fixtures de MVD.

### Critérios de aceite

- [ ] ferramenta possui origem, versão, tamanho e SHA-256;
- [ ] JSON é estável, versionado e validado por schema;
- [ ] texto e JSON identificam a mesma partida;
- [ ] plataforma sem suporte gera mensagem clara;
- [ ] arquivo pessoal não recebe recibo nem é modificado.

### Testes necessários

- MVD válida, truncada e abortada;
- golden JSON/texto;
- smoke por plataforma;
- update/remove da ferramenta.

### Riscos e decisões

- a saída upstream pode depender de templates; fixar e testar os templates como
  payload da ferramenta;
- campos não fornecidos devem ser `null`/ausentes conforme schema, nunca zero
  inventado.

## [DEMO-03] Integração independente do QWDTools

**Estado:** planejado\
**Prioridade:** P1\
**Complexidade:** média/alta\
**Depende de:** ARCH-04, DEMO-01, TEST-01\
**Bloqueia:** DEMO-04

### Objetivo

Entregar `demos convert <arquivo>` com validação, destino explícito e original
preservado.

### Estado atual

QWDTools 0.34 não está no projeto. A release não anexa assets ao GitHub; builds
e CI upstream cobrem Linux e Windows, sem macOS declarado.

### Escopo

- fixar fonte e artefatos validados;
- declarar formatos de entrada/saída realmente suportados;
- validar entrada antes de executar;
- exigir destino ou sugerir arquivo sem sobrescrever;
- executar conversão, validar resultado e preservar original;
- registrar recibo da ferramenta, nunca dos arquivos convertidos;
- tratar colisão, falta de espaço e erro parcial.

### Fora do escopo

- conversão silenciosa em lote;
- apagar original depois da conversão.

### Áreas provavelmente afetadas

- futuro `dist/tools/qwdtools/`;
- CLI e manifestos de formato;
- fixtures QWD/MVD.

### Critérios de aceite

- [ ] entrada inválida falha antes de criar saída final;
- [ ] saída é criada por troca atômica após validação;
- [ ] original permanece byte a byte igual;
- [ ] plataforma sem artefato é indicada;
- [ ] logs não expõem caminhos além do necessário.

### Testes necessários

- conversão válida;
- entrada truncada;
- destino existente;
- falha após arquivo temporário;
- smoke por plataforma.

### Riscos e decisões

- builds externos precisam de espelhamento imutável após validação;
- suporte macOS é investigação, não requisito falso do primeiro pacote.

## [DEMO-04] Relatório HTML local de partida

**Estado:** planejado, pós-MVP de demos\
**Prioridade:** P2\
**Complexidade:** média\
**Depende de:** DEMO-02, DEMO-03\
**Bloqueia:** nenhuma entrega MVP

### Objetivo

Gerar relatório HTML autocontido com resumo, jogadores, placar, estatísticas,
linha do tempo, armas, powerups, caminho e comando de reprodução.

### Estado atual

Não existe relatório local nem backend de estatísticas.

### Escopo

- renderizar exclusivamente a partir do JSON normalizado;
- manter assets locais e página utilizável offline;
- escapar nomes e texto da demo;
- indicar campos ausentes;
- oferecer comando de reprodução copiável;
- respeitar acessibilidade e movimento reduzido.

### Fora do escopo

- backend, conta, ranking global ou upload automático.

### Áreas provavelmente afetadas

- templates locais de relatório;
- CLI `demos stats`;
- testes de snapshot e acessibilidade.

### Critérios de aceite

- [ ] relatório abre offline sem recursos remotos;
- [ ] conteúdo não confiável é escapado;
- [ ] resumo corresponde ao JSON;
- [ ] demo original não é alterada;
- [ ] relatório funciona com dados parciais.

### Testes necessários

- golden HTML;
- injeção de markup;
- campos ausentes;
- navegação por teclado e contraste.

### Riscos e decisões

- linha do tempo depende dos eventos realmente produzidos pelo parser;
- o HTML é derivado pessoal e não recebe recibo de componente.

# Fase 6 — Quake clássico

## [VKQ-01] Runtime vkQuake

**Estado:** planejado\
**Prioridade:** P0\
**Complexidade:** alta\
**Depende de:** ARCH-01, ARCH-02, ARCH-04, TEST-01\
**Bloqueia:** CLASSIC-01, CLASSIC-02

### Objetivo

Incorporar vkQuake como runtime estável e isolado de Quake clássico.

### Estado atual

Não há cliente NetQuake. A release upstream observada é 1.35.0, com assets
Linux x86-64 e Windows x64/arm64. macOS possui CI e receita upstream, mas exige
escolher e validar um artefato reproduzível.

### Escopo

- fixar release estável e fonte;
- validar Windows, Linux e macOS somente onde houver artefato reproduzível;
- declarar requisitos gráficos e falha acionável;
- criar recibos, inventários, update, verify e remove;
- separar configuração, saves, screenshots e logs do ezQuake;
- declarar argumentos e diretório-base do runtime;
- preservar camada upstream e ajustes x86QW separados.

### Fora do escopo

- usar vkQuake para QuakeWorld;
- compartilhar automaticamente configurações ou saves com ezQuake.

### Áreas provavelmente afetadas

- futuro `dist/clients/vkquake/`;
- catálogo, recipes e gerenciador;
- testes gráficos por plataforma.

### Critérios de aceite

- [ ] cada plataforma anunciada executa smoke real;
- [ ] config e saves usam diretórios exclusivos;
- [ ] update preserva saves e preferências;
- [ ] remoção normal preserva dados pessoais;
- [ ] ausência de Vulkan/artefato produz diagnóstico claro.

### Testes necessários

- binário/hash;
- inicialização sem escrever em diretório ezQuake;
- configuração/saves;
- update/rollback/remove;
- smoke por SO.

### Riscos e decisões

- macOS é bloqueado até definir se o x86QW compila da fonte ou valida um
  artefato externo;
- a detecção de requisitos gráficos não deve prometer compatibilidade total.

## [CLASSIC-01] Componentes de dados e dependências de campanha

**Estado:** planejado\
**Prioridade:** P0\
**Complexidade:** média/alta\
**Depende de:** ARCH-02, ARCH-05, VKQ-01\
**Bloqueia:** CLASSIC-02

### Objetivo

Organizar dados clássicos como componentes independentes e verificáveis em
`dist/game-data/id1`, `dist/game-data/hipnotic` e `dist/game-data/rogue`.

### Estado atual

`dist/game-data/id1` já contém os dois PAKs usados pelo QuakeWorld. Não existem
componentes `quake-id1`, `quake-hipnotic` e `quake-rogue`, nem diretórios das
missões.

### Escopo

- declarar `quake-id1`, `quake-hipnotic`, `quake-rogue` e `vkquake`;
- validar cabeçalho, nome esperado, tamanho e SHA-256 de cada PAK;
- declarar `quake-id1 → vkquake`, `quake-hipnotic → quake-id1 + vkquake` e
  `quake-rogue → quake-id1 + vkquake` para execução;
- permitir que dados-base compartilhados tenham um único dono por arquivo;
- incluir os componentes na capacidade `classic`;
- testar instalação, update, verificação e remoção independente.

### Fora do escopo

- misturar os PAKs de campanha em pacotes ezQuake;
- mover automaticamente dados pessoais.

### Áreas provavelmente afetadas

- `dist/game-data/`;
- inventários, recipes, catálogo e perfis;
- testes de pacotes.

### Critérios de aceite

- [ ] cada PAK possui dono, tamanho e SHA-256 únicos;
- [ ] dependências impedem campanha sem base/runtime;
- [ ] `id1` compartilhado não é duplicado em artefatos sem justificativa;
- [ ] verificação funciona offline;
- [ ] remoção respeita dependências e preserva saves/configs.

### Testes necessários

- schema/dependências;
- PAK válido, ausente e alterado;
- instalação parcial;
- update/remove/reinstall.

### Riscos e decisões

- a reutilização de `id1` exige separar propriedade de dados e runtime;
- nomes de diretório e arquivos devem ser normalizados para sistemas
  case-sensitive.

## [CLASSIC-02] Launcher, campanhas, saves e música

**Estado:** planejado\
**Prioridade:** P1\
**Complexidade:** alta\
**Depende de:** PLAY-01, VKQ-01, CLASSIC-01\
**Bloqueia:** CLASSIC-03, SITE-01

### Objetivo

Entregar `play quake`, `play hipnotic` e `play rogue` com isolamento completo
da trilha QuakeWorld.

### Estado atual

O launcher só conhece mods QuakeWorld e sempre procura ezQuake.

### Escopo

- declarar runtime, gamedir, PAK, comando/mapa inicial, argumentos e confirmação;
- exibir Quake, Scourge of Armagon e Dissolution of Eternity;
- usar diretórios distintos de save/config por campanha quando necessário;
- validar carregamento da primeira fase de cada campanha;
- suportar música opcional em `id1/music`, `hipnotic/music` e `rogue/music`;
- declarar formatos de áudio confirmados pelo vkQuake fixado;
- tratar música como componente/capacidade opcional, nunca dependência de jogo.

### Fora do escopo

- iniciar Hipnotic ou Rogue pelo ezQuake;
- copiar configuração visual QuakeWorld para vkQuake.

### Áreas provavelmente afetadas

- manifestos de jogos clássicos;
- launcher;
- configs x86QW do vkQuake;
- documentação e testes de campanha.

### Critérios de aceite

- [ ] cada comando seleciona somente vkQuake;
- [ ] menu mostra os três nomes corretos;
- [ ] primeira fase de cada campanha carrega em smoke;
- [ ] saves permanecem separados e sobrevivem a update;
- [ ] ausência de música não impede jogo;
- [ ] argumentos QuakeWorld não são enviados ao vkQuake.

### Testes necessários

- resolução runtime/jogo;
- primeira fase por campanha;
- save/config isolation;
- música presente/ausente;
- plataforma sem vkQuake.

### Riscos e decisões

- o comando exato de início deve ser confirmado no runtime fixado;
- smoke gráfico pode exigir harness por plataforma.

## [CLASSIC-03] Conteúdo do relançamento de 2021

**Estado:** planejado, futuro\
**Prioridade:** P2\
**Complexidade:** alta\
**Depende de:** CLASSIC-02 estabilizado\
**Bloqueia:** nenhuma entrega MVP

### Objetivo

Adicionar suporte isolado ao conteúdo enhanced somente após as três campanhas
clássicas estarem estáveis.

### Estado atual

vkQuake declara suporte a modelos classic/enhanced e ao conteúdo de 2021, mas
o x86QW não detecta nem empacota essa estrutura.

### Escopo

- detectar estrutura do relançamento;
- declarar conteúdo e modelos enhanced separadamente;
- declarar addons compatíveis após curadoria;
- permitir escolha classic/enhanced;
- isolar ordem de carregamento e configuração;
- testar convivência com campanhas clássicas.

### Fora do escopo

- misturar essa entrega no MVP de Quake/Hipnotic/Rogue;
- habilitar aparência enhanced silenciosamente.

### Áreas provavelmente afetadas

- `dist/game-data/` e manifestos de conteúdo;
- launcher e vkQuake config;
- documentação e testes.

### Critérios de aceite

- [ ] detecção não altera a instalação encontrada;
- [ ] classic/enhanced são escolhas explícitas;
- [ ] addons possuem componente, dependências e ordem;
- [ ] campanhas clássicas continuam byte a byte verificáveis;
- [ ] configuração e saves não colidem.

### Testes necessários

- estrutura presente/ausente/parcial;
- classic versus enhanced;
- ordem de pacotes;
- regressão das três campanhas.

### Riscos e decisões

- detalhes de estrutura e assets precisam ser levantados na versão realmente
  suportada antes de definir componentes.

# Fase 7 — Cliente experimental

## [UNEZ-01] unezQuake isolado e explicitamente experimental

**Estado:** planejado\
**Prioridade:** P2\
**Complexidade:** média/alta\
**Depende de:** ARCH-01, ARCH-02, ARCH-04, PLAY-01\
**Bloqueia:** SITE-01, RELEASE-01

### Objetivo

Oferecer `unezquake-experimental` sem substituir ezQuake stable nem contaminar
a configuração competitiva padrão.

### Estado atual

unezQuake não está no projeto. A release observada 2.0.4 possui assets macOS
universal, Linux x86-64 e Windows x64 e adiciona recursos experimentais sobre a
base ezQuake.

### Escopo

- fixar versão, fonte, artefatos e hashes;
- criar recibos/inventários e configuração isolada;
- declarar capacidades como `extended-prediction`, `sprays`, `raw-accel`,
  `experimental-hud` e `experimental-scoreboard` somente após validação;
- testar conexão, demo e KTX;
- exibir identificação experimental no menu, logs e site;
- permitir seleção entre ezQuake e unezQuake quando ambos estiverem presentes;
- listar recursos com impacto competitivo antes de iniciar.

### Fora do escopo

- instalar pelo perfil `essential`;
- compartilhar automaticamente todo `config.cfg` do ezQuake;
- habilitar recurso experimental silenciosamente.

### Áreas provavelmente afetadas

- futuro `dist/clients/unezquake/`;
- catálogo e capacidade `experimental`;
- launcher, site, docs e testes.

### Critérios de aceite

- [ ] ezQuake continua runtime padrão;
- [ ] configurações são separadas por padrão;
- [ ] capacidades ativas são visíveis ao usuário;
- [ ] KTX, conexão e demo passam smoke por plataforma publicada;
- [ ] remover unezQuake não altera ezQuake;
- [ ] perfil `essential` permanece inalterado.

### Testes necessários

- instalação/update/remove;
- seleção com dois clientes;
- config isolation;
- conexão, KTX e demo;
- capacidades experimentais visíveis.

### Riscos e decisões

- recursos competitivos exigem comunicação explícita, não julgamento silencioso;
- compatibilidade de configuração deve usar apenas uma base comum declarada.

# Fase 8 — Conteúdo curado e experiência pública

## [CONTENT-01] Política e contrato de conteúdo comunitário curado

**Estado:** planejado\
**Prioridade:** P2\
**Complexidade:** média\
**Depende de:** ARCH-01, ARCH-02, ARCH-03\
**Bloqueia:** expansão pública de conteúdo

### Objetivo

Expandir mapas, LOCs, rotas, HUDs, miras, configs, texturas, modelos, campanhas
e mods sem download em massa nem catálogo aberto.

### Estado atual

O projeto preserva apenas conteúdo consumido e o conjunto de mapas nQuake; a
política já rejeita coleções arbitrárias.

### Escopo

- exigir ID, nome, autor, versão/commit, origem, tamanho, SHA-256 e destino;
- exigir runtime, protocolo, dependências, conflitos e prioridade;
- exigir teste de instalação, verificação e remoção;
- classificar conteúdo por modalidade e trilha;
- definir processo de proposta, revisão, curadoria, descontinuação e substituição;
- limitar cada mudança a itens explicitamente consumidos por perfil/preset.

### Fora do escopo

- marketplace aberto;
- ingestão automática de sites inteiros;
- download global de mapas, LOCs ou gráficos.

### Áreas provavelmente afetadas

- `maintenance/inventory/`;
- `dist/` por contexto;
- `maintenance/manage.py add` e testes;
- site/documentação.

### Critérios de aceite

- [ ] item sem consumidor declarado é rejeitado;
- [ ] conflitos e prioridade são determinísticos;
- [ ] remoção não apaga arquivo pessoal/modificado;
- [ ] catálogo identifica runtime/protocolo;
- [ ] conteúdo descontinuado permanece reproduzível nas releases imutáveis.

### Testes necessários

- schema e conflitos;
- colisão case-sensitive;
- ordem de carregamento;
- install/verify/remove;
- update de conteúdo com arquivo pessoal.

### Riscos e decisões

- grandes coleções devem ser divididas por uso real;
- popularidade não substitui revisão técnica e teste.

## [SITE-01] Site por trilhas, capacidades e plataformas

**Estado:** planejado\
**Prioridade:** P1\
**Complexidade:** média/alta\
**Depende de:** HOST-03, CLASSIC-02, UNEZ-01 e catálogo expandido\
**Bloqueia:** RELEASE-01

### Objetivo

Apresentar claramente jogar QuakeWorld, treinar, hospedar, assistir, analisar
demos, jogar as campanhas clássicas e usar recursos experimentais.

### Estado atual

O site apresenta a distribuição QuakeWorld e o catálogo atual, com princípios
visuais e WCAG 2.2 AA definidos, mas não conhece runtimes/capacidades.

### Escopo

- criar seções/páginas por trilha e ação;
- projetar matrizes de plataforma, componente, versão, hash e estado;
- distinguir stable, nightly e experimental;
- consumir a projeção declarativa do catálogo;
- incluir exemplos de CLI e troubleshooting;
- preservar o sistema visual, teclado, foco, contraste e reduced motion;
- não prometer recurso/plataforma sem artefato publicado e testado.

### Fora do escopo

- backend obrigatório;
- conta de usuário ou telemetria obrigatória.

### Áreas provavelmente afetadas

- `site/public/`;
- `site/PRODUCT.md`, `site/DESIGN.md`, `site/docs/`;
- catálogo público e `site/tests/`.

### Critérios de aceite

- [ ] cada ação principal possui caminho de descoberta e comando verificável;
- [ ] matrizes vêm do catálogo, não de contagem manual;
- [ ] indisponibilidade por plataforma é explícita;
- [ ] navegação e contraste atendem os critérios atuais;
- [ ] conteúdo funciona sem JavaScript essencial quando aplicável.

### Testes necessários

- schema/projeção de catálogo;
- links e exemplos;
- acessibilidade automatizada e revisão manual;
- viewport móvel/desktop;
- fallback de catálogo indisponível.

### Riscos e decisões

- manter linguagem de produto separada de detalhes administrativos;
- estado experimental nunca é comunicado apenas por cor.

## [DOCS-01] Documentação operacional por domínio

**Estado:** planejado\
**Prioridade:** P1\
**Complexidade:** média\
**Depende de:** entregas funcionais das fases 2–7\
**Bloqueia:** RELEASE-01

### Objetivo

Documentar CLI, migração, servidor, bots, demos, vkQuake, campanhas e cliente
experimental com exemplos testados.

### Estado atual

Há README, arquitetura, manual do instalador e documentação do site, todos
focados na distribuição QuakeWorld atual.

### Escopo

- referência de comandos e flags;
- guia de migração de instalações antigas;
- administração MVDSV/QTV/QWFWD;
- treino, bots, rotas e mapas suportados;
- demos, parser, conversão e relatório;
- vkQuake, Quake, Hipnotic, Rogue e música;
- diferenças e riscos do unezQuake;
- troubleshooting por plataforma;
- exemplos executados em testes sempre que possível.

### Fora do escopo

- copiar integralmente documentação upstream;
- documentar suporte ainda não validado.

### Áreas provavelmente afetadas

- `docs/`;
- `dist/installer/docs/`;
- `site/docs/`;
- testes de links/comandos.

### Critérios de aceite

- [ ] todo comando público possui referência e exemplo;
- [ ] cada runtime possui instalação, verificação, update e remoção;
- [ ] limitações de plataforma são explícitas;
- [ ] migração e recuperação de falha são reproduzíveis;
- [ ] exemplos não exigem downloads durante gameplay.

### Testes necessários

- links internos/externos;
- snippets de CLI;
- consistência com catálogo e `--help`;
- revisão de português e terminologia.

### Riscos e decisões

- documentação gerada não substitui guias narrativos;
- versões em texto devem vir do catálogo ou ser marcadas como baseline datado.

# Fase 9 — Estabilização para 1.0

## [TEST-02] CI e matriz integral de regressão

**Estado:** planejado\
**Prioridade:** P0\
**Complexidade:** alta\
**Depende de:** TEST-01 e runtimes incorporados\
**Bloqueia:** RELEASE-01

### Objetivo

Transformar os contratos de teste em gates executáveis para catálogo,
instalação, runtime, jogo, plataforma e migração.

### Estado atual

`maintenance/manage.py verify` executa suites locais e valida a distribuição,
mas a matriz completa de CI ainda é pendência do roadmap geral.

### Escopo

- validar schema, dependências, ciclos, IDs, versões, plataformas, hashes,
  destinos, compatibilidade e conflitos;
- testar instalação nova/parcial, update, upgrade, reparo, remoção e reinstalação;
- testar preservação de arquivos pessoais e modificados;
- executar smoke de ezQuake stable/nightly, unezQuake, MVDSV, vkQuake, QTV,
  QWFWD, MVDParser e QWDTools;
- testar KTX, bot, cliente-servidor, MVD, demo e três campanhas;
- manter matriz macOS/Linux/Windows com indisponibilidade explícita;
- testar fixtures de instalações antigas;
- publicar relatório de processos, tempos, skips e artefatos.

### Fora do escopo

- marcar plataforma não testada como suportada;
- depender somente de mocks para release.

### Áreas provavelmente afetadas

- `.github/workflows/`;
- `maintenance/tests/` e fixtures;
- scripts de smoke e relatórios.

### Critérios de aceite

- [ ] cada runtime valida binário, inicia, confirma estado, encerra e verifica exit;
- [ ] nenhum smoke deixa processo órfão ou altera config pessoal;
- [ ] skips possuem motivo e impedem anúncio indevido de plataforma;
- [ ] migrações de todas as versões públicas suportadas passam;
- [ ] falha de gate bloqueia build/publicação;
- [ ] `verify` offline continua funcionando para componentes instalados.

### Testes necessários

- autoteste do pipeline;
- falhas deliberadas de hash/schema/runtime;
- matriz completa;
- recuperação após job interrompido.

### Riscos e decisões

- binários gráficos e rede podem exigir runners dedicados;
- testes externos instáveis ficam separados dos gates determinísticos, mas os
  smokes de release continuam obrigatórios em ambiente controlado.

## [RELEASE-01] Gate formal de estabilização 1.0

**Estado:** planejado\
**Prioridade:** P0\
**Complexidade:** alta\
**Depende de:** ARCH-04, PLAY-01, TRAIN-02, HOST-03, DEMO-03, CLASSIC-02, UNEZ-01, SITE-01, DOCS-01, TEST-02\
**Bloqueia:** release 1.0 do ecossistema

### Objetivo

Concluir migrações, builds reproduzíveis, recuperação de falhas e documentação
antes de declarar o ecossistema estável.

### Estado atual

O projeto está na linha pública pré-1.0 do instalador. Este roadmap não define
qual release intermediária carregará cada fase.

### Escopo

- congelar schemas públicos e política de compatibilidade;
- concluir migrações e a janela de aliases legados;
- remover compatibilidade obsoleta apenas após fixtures e aviso documentado;
- garantir builds reproduzíveis e artefatos imutáveis;
- executar verificação offline e recuperação de falhas;
- validar update/upgrade a partir de todas as versões suportadas;
- concluir site, manuais e matrizes de plataforma;
- publicar checklist formal de release e rollback;
- executar instalação limpa e atualização real em cada plataforma suportada.

### Fora do escopo

- incluir recurso incompleto apenas para preencher a versão 1.0;
- exigir que todo runtime exista em todo sistema operacional.

### Áreas provavelmente afetadas

- toda a cadeia `dist/ → catálogo → artefatos → bootstrap`;
- CI, docs e site;
- fixtures e procedimentos de release.

### Critérios de aceite

- [ ] zero migração pendente sem plano/fixture;
- [ ] todos os artefatos publicados correspondem a `dist/` e ao catálogo;
- [ ] instalação, update, upgrade, reparo e remoção passam por plataforma;
- [ ] recuperação de download, hash, troca e processo interrompido foi testada;
- [ ] stable/nightly/experimental e as duas trilhas permanecem isolados;
- [ ] nenhum comando de execução baixa componente;
- [ ] limitações e plataformas são documentadas no site e na CLI;
- [ ] definição global de pronto está integralmente atendida.

### Testes necessários

- matriz de release completa;
- upgrade encadeado desde fixtures antigas;
- rollback transacional;
- verificação offline;
- instalação pública em ambiente limpo;
- remoção normal e purge.

### Riscos e decisões

- 1.0 é um gate de estabilidade, não uma data;
- uma plataforma ou runtime pode permanecer experimental/indisponível sem
  bloquear o núcleo, desde que catálogo e interface sejam honestos.

## Investigações técnicas ainda abertas

Estas investigações possuem resultado observável e devem ser concluídas nos
épicos correspondentes antes da implementação dependente:

1. localizar e validar o artefato macOS Apple Silicon anunciado pelo MVDSV ou
   definir build reproduzível a partir da release fixada;
2. decidir pin e versionamento do QTV, que atualmente não possui tag/release;
3. verificar se QTV, QWFWD, MVDParser e QWDTools podem receber builds macOS
   sustentáveis; até lá, a plataforma permanece indisponível para esses itens;
4. comprovar no artefato KTX distribuído que `BOT_SUPPORT` está ativo e mapear
   comandos/skills/equipes da versão fixada;
5. classificar `practice` como modo upstream ou preset composto x86QW e definir
   smoke sem alterar o gamecode;
6. cruzar mapas dos presets de treino com os arquivos `.bot` realmente
   presentes e rejeitar mapas sem navegação;
7. validar o servidor local do ezQuake com bots somente como caminho secundário,
   mantendo MVDSV + KTX como principal;
8. escolher a origem reproduzível do vkQuake macOS e executar smoke real em
   Apple Silicon;
9. confirmar os formatos de demo adicionais reconhecidos pelos clientes antes
   de declará-los no catálogo;
10. mapear exatamente quais campos MVDParser 1.20 fornece e quais exigem apenas
    normalização, sem inventar estatísticas;
11. confirmar comandos de início e diretórios de save/config do vkQuake para as
    três campanhas;
12. levantar a estrutura do relançamento de 2021 somente após o MVP clássico;
13. classificar capacidades competitivas do unezQuake 2.0.4 e definir como a
    CLI as torna visíveis antes da execução.

## Fora do escopo

- desenvolvimento de engine própria;
- reescrita completa do instalador;
- instalação arbitrária de qualquer mod encontrado na internet;
- marketplace aberto;
- backend obrigatório para jogar;
- conta obrigatória;
- telemetria obrigatória;
- servidor público hospedado automaticamente pelo x86QW;
- alteração automática do firewall do sistema;
- abertura automática de portas no roteador;
- alteração silenciosa de configurações pessoais;
- substituição do ezQuake como cliente principal;
- mistura de configurações QuakeWorld e Quake clássico;
- implementação de código nesta tarefa;
- análise de licenciamento;
- qualquer runtime não aprovado nesta especificação.
