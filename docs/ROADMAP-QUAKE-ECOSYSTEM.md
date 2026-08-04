# Roadmap técnico do ecossistema QuakeWorld no x86QW

Este roadmap usa o estado real do produto como baseline e separa entrega de
validação. Ele complementa o [índice geral](ROADMAP.md); não autoriza release,
publicação ou incorporação automática de conteúdo.

Baseline publicada: tag `x86qw-installer-0.7.1`. O estado funcional é descrito
pela própria tag imutável no commit
`78dc30b58f9ba2a2ec8aeb31879d9b8072ab576b`, publicada em 3 de agosto de 2026.

Baseline inicial da issue #45: merge
`afb4f666095e37fe262b87b49339e18d25738522`.
A implementação `0.7.1` passou nos sete jobs obrigatórios e em 393 testes de
manutenção mais quatro testes do site. A `0.7.0` permanece imutável no
histórico.

Baseline corretiva consolidada após a PR 4: merge
`206adc46df6aced49eee7ac1fcae3cf331f07a63`. Downloader, fronteira
ZIP/PK3/PYZ e DACL privada Windows foram integrados sem alterar a release
pública, o catálogo `current` nem os bootstraps implantados da `0.7.1`. O
candidato da PR 5 parte desse commit, preserva o stable macOS upstream e
permanece não publicado; smokes de runtime e de conta padrão continuam
separados.

## Escala de estado

**Entrega funcional:** não iniciada · parcial · MVP entregue · completa
**Validação:** não validada · unitária · macOS · Linux · Windows ·
multiplataforma completa

A validação lista somente execução comprovada. Presença de artefato, parsing de
catálogo ou teste portável não equivale a smoke real do runtime naquela
plataforma.

## Baseline do produto

- instalador público `0.7.1`; as linhas anteriores permanecem imutáveis;
- 61 pacotes e 21 componentes no catálogo;
- ezQuake stable `3.6.9` e nightly `20260616-101233_a86996a`;
- cinco jogos atuais: KTX `1.47`, Final Arena `1.20`, Pro-X `1.1`, Team
  Fortress `2.9` e Total Destruction 2 `2.22`;
- MVDSV `1.11+x86qw.3`, QTV `0+025ca949aca0+x86qw.2` e QWFWD
  `1.30+x86qw.3`;
- cliente macOS universal, Linux x86-64 e Windows x64;
- serviços macOS arm64, Linux amd64 e Windows x64; macOS Intel não é anunciado
  para os serviços;
- clientes macOS catalogados com suporte condicional até Gatekeeper, primeira e
  segunda abertura, arm64 e Intel serem provados com o candidato imutável;
- CLI com `play`, `host`, `proxy`, `qtv`, `status`, `hub`, `update`, `upgrade`, `verify`,
  `repair`, `cleanup`, `uninstall` e `version`;
- navegador de terminal por tarefas, com busca, teclado, multisseleção, linhas
  alinhadas e coloridas e fallback numerado;
- perfis `essential`, `recommended`, `complete` e `custom` preservados.

## Arquitetura e distribuição

### ARCH-01 — Catálogo declarativo

**Entrega funcional:** completa para o escopo atual
**Validação:** unitária; validação estrutural no `verify`

Capacidades, runtimes, plataformas, jogos e compatibilidade são fontes
canônicas em `maintenance/inventory/`. O zipapp recebe somente projeções
mínimas, e o site recebe `api/v1/product.json`, gerado das mesmas fontes.

Próximos passos:

- continuar removendo hardcodings apenas quando já houver campo tipado;
- manter argumentos como listas, sem shell ou linguagem arbitrária;
- impedir plataforma anunciada sem artefato e teste requerido.

### ARCH-02 — Estado e bootstrap limpo

**Entrega funcional:** completa para formato 2
**Validação:** unitária

O estado registra perfil, seleção customizada, componentes, capacidades e
fingerprint em `.x86qw/state.json`. A linha atual começa em uma árvore nova:
não converte `.install/` nem o antigo depósito `_x86qw/`. MVDSV, QTV e QWFWD
recebem somente a variante da plataforma escolhida em seus contextos
operacionais.

### ARCH-03 — Execução sem mutação

**Entrega funcional:** completa
**Validação:** unitária

`play`, `host`, `proxy`, `qtv` e `hub` validam e executam conteúdo instalado;
`status` consulta a stack ativa sem adquirir lock nem alterar processos.
`play-support` é preparado por instalação, `update`, `upgrade` ou `repair`.
Materialização necessária ao servidor é efêmera, journalizada e reconciliada.

### ARCH-04 — CI e publicação

**Entrega funcional:** completa para o gate portável atual
**Validação:** unitária e matriz real macOS/Linux/Windows; smokes nativos dos runtimes pendentes

Pull requests executam LFS, validação integral, testes portáveis, parsing da
CLI e dry-run do Worker. O workflow de release é separado, protegido e depende
do workflow de validação. Publicação continua manual e não faz parte desta
consolidação.

Os checks portáveis Python 3.10 e recente já executam nos três sistemas. Para
chegar à validação nativa completa dos runtimes:

- registrar skips explícitos com motivo;
- executar smokes nativos dos runtimes suportados;
- guardar a evidência de release sem expor segredos.

## Gameplay atual

### PLAY-01 — Cinco jogos locais

**Entrega funcional:** MVP entregue
**Validação:** unitária; smoke gráfico manual pendente por plataforma

Os cinco jogos usam contratos declarativos de gamedir, marcador, gamecode,
mapa, configurações e runtimes compatíveis. Golden tests preservam a geração de
comandos anterior à refatoração.

### KTX-01 — Modos KTX

**Entrega funcional:** completa para os modos publicados
**Validação:** unitária; 24 modos exercitados no macOS em janela

`modes.json` continua como fonte de verdade. Duel, 2on2, 4on4, CTF, Race,
Midair, Practice e os demais modos publicados preservam argumentos e ajuda.

### BOT-01 — Frogbots

**Entrega funcional:** MVP entregue
**Validação:** unitária; 22 modos compatíveis exercitados no macOS em janela

Quantidade, preenchimento, habilidade, equipe, arma e vida são validados. CTF e
Race rejeitam combinações de bot incompatíveis. Falta formalizar smokes reais
por plataforma e mapa representativo.

## Servidor e serviços

### MVDSV-01 — Servidor dedicado

**Entrega funcional:** MVP entregue
**Validação:** unitária; macOS arm64; Linux e Windows pendentes

MVDSV hospeda qualquer um dos cinco gamecodes atuais. Readiness confirma
`status`, gamecode e mapa, aplica configuração pós-map por RCON e restaura a
senha final. O componente não depende permanentemente de KTX.

Pendente:

- smoke nativo em Linux amd64 e Windows x64;
- validação formal do MVD produzido;
- smoke dedicado de cada gamecode nas plataformas suportadas.

### HOST-01 — Comando `host`

**Entrega funcional:** MVP entregue
**Validação:** unitária; macOS arm64

O comando oferece os jogos instalados de `play`, executa apenas MVDSV por
padrão e pode compor QTV e QWFWD. Senhas podem vir de prompt sem eco ou arquivo
privado. Bind externo sem senha gera alerta explícito.

### QTV-01 — Relay HTTP/MVD

**Entrega funcional:** MVP entregue para HTTP e upstream
**Validação:** unitária; macOS arm64; Linux e Windows pendentes

QTV opera isoladamente com upstream remoto ou depois de um MVDSV local. O
preflight valida endpoint; readiness confirma processo, HTTP e registro do
upstream.

Pendente:

- smoke de espectador real e stream MVD;
- execução nativa em Linux e Windows.

### QWFWD-01 — Proxy UDP

**Entrega funcional:** MVP entregue
**Validação:** unitária; macOS arm64; forwarding real pendente

QWFWD opera isoladamente ou junto ao host. Readiness exige processo vivo e
porta ocupada. O teste cliente → proxy → servidor permanece em suíte de rede
separada.

### LIFE-01 — Lifecycle e crash recovery

**Entrega funcional:** completa para exclusão entre uma stack e manutenção por instalação
**Validação:** unitária em lock concorrente, lock ausente com controlador vivo,
journal legado, PID reutilizado, árvore órfã POSIX, configuração sensível,
SIGINT, SIGTERM e crash; casos Win32 nativos de DACL, mutex e Job Object
aprovados; smokes nativos dos runtimes ainda pendentes

O lock atômico compartilhado é adquirido antes da recuperação e impede tanto
uma segunda stack quanto manutenção concorrente. O controlador do próprio
journal também é validado, portanto a ausência do lock não torna uma sessão
viva recuperável. O preflight termina antes de iniciar filhos. A ordem
composta é MVDSV, RCON, QTV e QWFWD; o encerramento ocorre na ordem inversa.
Controlador e filhos têm identidade por PID, token de criação e executável. O
journal preserva material não sensível modificado e remove configurações
efêmeras sensíveis por unlink, sem registrar seus hashes. Diretórios ou arquivos
especiais encontrados no lugar do temporário são preservados com erro.

Serviços persistentes do sistema permanecem trabalho futuro e exigem proposta
separada.

## Segurança

### SEC-01 — ZIP/PK3/PYZ

**Entrega funcional:** completa no código corretivo da issue #49; ainda não
publicada
**Validação:** regressão local concluída em Python 3.14 e 3.10: `Ran 695 tests`
e `OK (skipped=15)` na manutenção, mais `Ran 5 tests` e `OK` no site; matriz da
PR 3 concluída em 7/7 jobs no Ubuntu, macOS e Windows com Python 3.10 e 3.13,
incluindo identidade e reparse point nativos Windows; smokes nativos dos
runtimes permanecem pendentes

O candidato centraliza ZIP, PK3 e PYZ em `scan_archive`, `ArchivePlan` e
`extract_archive`. O preflight integral usa semântica POSIX e rejeita
traversal, drives, barras invertidas, controles, caracteres Win32 proibidos,
reservados Windows, links,
membros especiais, colisões exatas, de caixa, Unicode e de prefixo. Limites
cobrem fonte compactada, metadados centrais, quantidade, membro, total,
profundidade, caminho em unidades UTF-16 e razão de compressão. Um pre-scan
estrutural conta os registros centrais antes de `zipfile`; ambas as etapas usam
o mesmo snapshot privado e limitado, imune à troca concorrente da fonte. Reads
validam o arquivo inteiro; extrações usam staging privado, modos canônicos
`0644`/`0755`, `fsync` e promoção exclusiva. O destino confirmado é commit
irreversível; falhas anteriores limpam somente o staging comprovado, promoções
inconclusivas são preservadas e falhas posteriores preservam o destino.

Os bootstraps candidatos projetam a mesma fonte canônica byte a byte e não usam
`unzip` ou `Expand-Archive`. A release pública `0.7.1` ainda não contém essa
fronteira. A validação do contrato de arquivos é multiplataforma completa após
a matriz verde da PR 3; esses testes não são smokes nativos de ezQuake, MVDSV,
QTV ou QWFWD. Consulte o
[ADR 0002](adr/0002-fronteira-unica-de-arquivos.md).

### SEC-02 — Segredos e endpoints

**Entrega funcional:** completa para a CLI atual
**Validação:** unitária

Prompts usam entrada sem eco; arquivos precisam ser regulares e privados. As
opções legadas permanecem por compatibilidade com alerta. Segredos nunca entram
nos argumentos dos filhos nem em mensagens de erro. Endpoints IPv4, hostname e
IPv6 entre colchetes têm parser próprio.

### SEC-03 — Downloads remotos limitados

**Entrega funcional:** completa no código da PR 2, mesclado no baseline
`b833ba45e08a9de644dc7368f82c905522a0a558`; ainda não publicada em uma versão
do instalador

**Validação:** evidência da PR 2: 565 testes de manutenção e cinco do site
aprovados localmente; oito skips explícitos (sete Windows e um smoke de rede);
matriz concluída em Ubuntu, macOS e Windows com Python 3.10 e 3.13

Artefatos persistentes exigem HTTPS, tamanho, SHA-256, limite e deadline.
Metadados dinâmicos são efêmeros e limitados. Retries são restritos a falhas
transitórias; temporários recebem `0600` no POSIX e só são promovidos
atomicamente depois da validação. A DACL privada dos temporários Windows passa
pela fronteira definida no [ADR 0003](adr/0003-dacl-privada-windows.md), validada
nos jobs Windows da PR 4 com Python 3.10 e 3.13.
Autenticação, expiração e proteção contra rollback ou freeze permanecem na
[issue #48](https://github.com/x86dx2/x86qw/issues/48).

### SEC-04 — DACL privada no Windows

**Entrega funcional:** implementada no código corretivo da PR 4; ainda não publicada

**Validação:** unitária e nativa Windows com Python 3.10 e 3.13; smoke de runtime sob conta padrão pendente

Objetos privados gerenciados nascem com DACL protegida e somente duas ACEs de
controle total: usuário atual e `LOCAL SYSTEM`. A política abrange o plano de
controle `.x86qw/`, sessões, locks, journals, logs, pedidos de parada,
configurações sensíveis, staging, downloads e o diretório temporário do
bootstrap PowerShell. Arquivos externos de senha são somente validados e nunca
reescritos. A operação falha fechada quando a ACL persistente não pode ser
comprovada. A matriz da PR 4 comprovou criação sob herança hostil, os dois
principals permitidos, rejeição de arquivos externos inseguros, proteção do
bootstrap e leases contra substituição. O código não solicita elevação;
executá-lo como conta padrão em um smoke nativo ainda pertence ao PR 11.

### SEC-05 — Confiança do ezQuake stable no macOS

**Entrega funcional:** preservação upstream implementada no candidato da PR 5;
ainda não publicada

**Validação:** unitária, auditoria de assinatura/hashes no macOS e matriz 7/7
em Ubuntu/macOS/Windows; smokes de primeira e segunda abertura, Gatekeeper,
arm64 e Intel pendentes no PR 11

O stable 3.6.9 é extraído e promovido sem alteração de `Info.plist`, sandbox,
entitlements ou assinatura. A transformação local foi limitada ao nightly e
falha fechada se receber o canal stable. Instalações stable transformadas pela
0.7.1 são reconhecidas somente pelo artefato, identidade binária e marcador
conhecidos; a restauração usa o mesmo payload upstream validado e troca
transacional de runtime e recibo. Artifact desconhecido, executável upstream,
runtime sem recibo e identidade inconclusiva são preservados.

O upstream continua ad hoc, sem Team ID ou ticket stapled e rejeitado por
`spctl`; `codesign --verify` não autentica o publicador. Stable e nightly macOS
são projetados como `conditional`. Consulte o
[ADR 0004](adr/0004-preservar-bundle-upstream-ezquake-stable-macos.md).
A matriz canônica da PR 5 é o
[run 30871046055](https://github.com/x86dx2/x86qw/actions/runs/30871046055),
aprovado em 7/7 jobs.

## Instalador e perfis

### INST-01 — Ciclo de vida instalado

**Entrega funcional:** completa para o escopo atual
**Validação:** unitária e CI portável macOS/Linux/Windows; smokes nativos permanecem parciais

Instalação nova, stable, nightly, coexistência, perfis, custom, update, upgrade,
verify, cleanup e uninstall permanecem cobertos. `repair` recompõe somente o
payload gerenciado necessário; a CLI pública orienta a reexecução do bootstrap
quando precisa obter pacotes.

### PROFILE-01 — Capacidades futuras

**Entrega funcional:** parcial
**Validação:** unitária da migração do estado

O formato 2 distingue capacidades técnicas do runtime das capacidades
selecionáveis da instalação. Como nenhum perfil operacional adicional está
habilitado, somente a lista vazia é aceita. Perfis atuais permanecem intactos;
capacidades adicionais exigem componentes e migrações aprovados em trabalho
separado.

### INST-02 — Contrato do runtime Python

**Entrega funcional:** completa e publicada na linha corretiva `0.7.1`
**Validação:** 393 testes de manutenção e quatro do site; 7/7 jobs verdes em macOS/Linux/Windows com Python 3.10 e 3.13

Na implementação publicada, bootstrap e launchers exigem Python 3.10 ou mais
recente por `sys.version_info`, antes de rede ou mutação. A instalação gera os
launchers com o executável efetivamente validado e mantém fallback
determinístico quando esse runtime desaparece. O bootstrap Unix calcula o
SHA-256 em streaming. A issue
[#44](https://github.com/x86dx2/x86qw/issues/44) registra critérios, contratos e
evidência da correção.

## Site e documentação

### DOC-01 — Fatos públicos

**Entrega funcional:** completa
**Validação:** unitária e dry-run do Worker

Versão, pacotes, componentes, comandos, jogos, runtimes, plataformas e estado de suporte são
projetados no catálogo público do produto. README, manual e site são testados
contra essa fonte; divergência bloqueia `verify` e CI.

## Backlog posterior à consolidação

Os itens abaixo permanecem deliberadamente futuros e não fazem parte da
implementação atual:

- central de demos e análise formal de MVD/QWD;
- comando específico de treinamento;
- cliente de campanha clássica e expansões;
- novos clientes ou engines;
- novos mods e mapas externos;
- serviços persistentes do sistema;
- perfis operacionais adicionais sobre capacidades já instaladas.

Cada item futuro precisa de catálogo, artefato por plataforma, origem fixada,
hash, migração, testes, smoke real e aprovação de release próprios. Nenhum deles
deve ser incorporado como efeito colateral de `play`, `host`, `update` ou
`upgrade`.

## Gates para a próxima fase

- [x] checks reais do GitHub Actions verdes nos três sistemas;
- [ ] smokes nativos de serviços em Linux e Windows;
- [ ] smoke do cliente em macOS Intel;
- [ ] primeira e segunda abertura do stable macOS preservado em arm64;
- [ ] Gatekeeper e bookmark sandbox do stable macOS documentados com o candidato exato;
- [ ] MVD produzido e validado formalmente;
- [ ] forwarding QWFWD validado em suíte de rede;
- [x] revisão humana do diff e das migrações;
- [x] versão `0.2.1` escolhida sem sobrescrever `0.2.0`;
- [x] publicação `0.2.0` executada e verificada em etapa explícita aprovada;
- [x] publicação `0.2.1` executada e verificada em etapa explícita aprovada;
- [x] versão `0.2.2` preparada sem sobrescrever `0.2.1`;
- [x] publicação `0.2.2` executada e verificada em etapa explícita aprovada;
- [x] versão `0.2.3` corrige a leitura de journals limpos criados antes da `0.2.1`;
- [x] publicação `0.2.3` executada e verificada em etapa explícita aprovada;
- [x] versão `0.3.0` adota bootstrap limpo e layout contextual para os serviços;
- [x] publicação `0.3.0` executada e verificada em etapa explícita aprovada;
- [x] versão `0.4.0` moderniza os menus preservando comandos e flags;
- [x] publicação `0.4.0` executada e verificada em etapa explícita aprovada;
- [x] versão `0.4.1` corrige sequências de setas e organiza as linhas dos menus;
- [x] publicação `0.4.1` executada e verificada em etapa explícita aprovada;
- [x] versão `0.4.2` corrige a entrada dos 24 modos KTX e dos Frogbots;
- [x] publicação `0.4.2` executada e verificada em etapa explícita aprovada;
- [x] versão `0.5.0` reformula menus, perfis Frogbot e controles contextuais;
- [x] publicação `0.5.0` executada e verificada em etapa explícita aprovada;
- [x] versão `0.5.1` corrige a detecção e a orientação do Python no Windows;
- [x] publicação `0.5.1` executada e verificada em etapa explícita aprovada;
- [x] versão `0.6.0` adiciona lifecycle de serviços em segundo plano e status
  com encerramento coordenado;
- [x] publicação `0.6.0` executada e verificada em etapa explícita aprovada;
- [x] contrato Python da `0.7.1` validado em 7/7 jobs e 393 + 4 testes;
- [x] asset imutável `0.7.1` publicado, verificado e promovido a `current`;
- [x] matriz da PR 2 verde nos três sistemas;
- [x] matriz da PR 3 verde nos três sistemas, inclusive os casos nativos Windows;
- [x] matriz da PR 4 comprova DACL privada em Windows com Python 3.10 e 3.13;
- [ ] smoke Windows sob usuário padrão e sem elevação no PR 11;
- [ ] publicação de qualquer versão posterior exige nova aprovação explícita.
