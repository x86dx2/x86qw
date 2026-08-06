# Roadmap técnico do ecossistema QuakeWorld no x86QW

Este roadmap usa o estado real do produto como baseline e separa entrega de
validação. Ele complementa o [índice geral](ROADMAP.md); não autoriza release,
publicação ou incorporação automática de conteúdo.

Baseline publicada: tag `x86qw-installer-0.7.3`, commit
`3bbc7a01faf8d472c5ccbab9233e05e9abadc379`. `0.7.1` e `0.7.2` permanecem
inalteradas no histórico.

Baseline inicial da issue #45: merge
`afb4f666095e37fe262b87b49339e18d25738522`.
A implementação `0.7.1` passou nos sete jobs obrigatórios e em 393 testes de
manutenção mais quatro testes do site. A `0.7.0` permanece imutável no
histórico.

Baseline corretiva consolidada após as PRs 2–5: merge
`3bbc7a01faf8d472c5ccbab9233e05e9abadc379` (tag
`x86qw-installer-0.7.3`). Downloader limitado, fronteira ZIP/PK3/PYZ, DACL
privada Windows e preservação do stable macOS upstream estão publicados nessa
linha. O fluxo atual é Mac/local e não executa smokes nativos de runtime ou de
conta padrão.

Baseline inicial da PR 6: merge
`00098330e5833ba2c83c7121272d644c2a204a7b`. Esse é o marco histórico da
extração incremental de ownership de I/O, catálogos, estado, transações, UI,
gameplay, plataforma, sessão e supervisor para `x86qw_runtime`. O recorte
histórico da PR 6 possuía 56 membros; o HEAD documental desta consolidação
possui 63 membros e sua projeção continua derivada do manifesto declarativo.
Essa implementação foi integrada pela PR 64 e publicada na `0.7.3`; a issue
`#52` foi encerrada pela PR 62, enquanto a frente da PR 6 permanece pausada
como trilha auditável. O HEAD documental desta
consolidação é `3bbc7a01faf8d472c5ccbab9233e05e9abadc379`.

## Escala de estado

**Entrega funcional:** não iniciada · parcial · MVP entregue · completa
**Validação:** não validada · unitária · Mac/local · registro histórico de
compatibilidade multiplataforma

A validação lista somente execução comprovada. Presença de artefato, parsing de
catálogo ou teste portável não declara execução nativa de runtime em outra
plataforma.

## Baseline do produto

- instalador público `0.7.3`; as linhas anteriores permanecem imutáveis;
- 63 pacotes e 21 componentes no catálogo;
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

### ARCH-04 — Validação local e publicação

**Entrega funcional:** completa para o gate operacional atual
**Validação:** unitária e Mac/local; smokes nativos não fazem parte deste fluxo

O mantenedor executa no Mac `git lfs pull`, `git lfs fsck`, validação integral,
testes portáveis, parsing da CLI e o dry-run do Worker quando aplicável. Não há
workflow, runner ou artifact remoto no caminho operacional. Publicação continua
manual e separada desta consolidação.

Os nomes de Linux e Windows continuam nos contratos, catálogos e ferramentas
de compatibilidade. Não há smokes nativos nem evidência nativa obrigatória para
este checkout; a promoção usa a validação Mac/local e as fronteiras separadas
de aprovação, trust metadata e publicação.

### ARCH-05 — Fronteiras incrementais do runtime

**Entrega funcional:** implementada no código da PR 6; revisão arquitetural histórica registrada
**Validação:** regressão do snapshot público 0.7.3 preservada; o checkout
corretivo tem regressão Mac/local registrada, sem smokes nativos do candidato
como requisito

O runtime é a fonte canônica para downloader, archive, persistência atômica,
filesystem privado e arquivos gerenciados, catálogos, estado, recibos,
migrações, transações, navegação, gameplay, adapters de plataforma, lock da
instalação e supervisor de processos/sessões. Navegação, console e argumentos
são compartilhados pelos três entrypoints; o manager permanece como raiz de
composição do grafo de comandos. Manutenção e entrypoints consomem as demais
fronteiras; `x86qw_runtime` não pode importar `maintenance`, `dist` nem as
fachadas instaladas.

O recorte histórico observado na PR 6 tinha 56 membros: 44 módulos de
`x86qw_runtime`, quatro entrypoints/fachadas no topo, duas projeções KTX e seis
membros gerados. `maintenance/inventory/installer-runtime-members.json`
declara origem, consumidor e contrato de cada membro e gera a projeção do
builder; testes exigem igualdade com o ZIP produzido e proíbem módulos de
manutenção no artefato.

Na evidência histórica do baseline público 0.7.3, a regressão integral executou
1.198 testes de manutenção, com 37 skips explícitos de plataforma/rede, e os 5
testes do site. O snapshot corretivo local usa regressão Mac/local e não
executa smokes nativos; os números da última execução são registrados na nota
de estabilização, não usados como promessa de plataforma.
Console,
parser e navegação são canônicos no runtime; o manager permanece como raiz de
composição do grafo de comandos. Cleanup, uninstall e purge usam quarantine
reversível até o commit e tiveram rollback adversarial validado. A finalização
remove arquivos regulares e diretórios vazios por identidade; links e tipos
especiais são preservados no quarantine com erro explícito. Os smokes nativos do
PR 11 não fazem parte deste snapshot; os contratos nativos permanecem
disponíveis para compatibilidade e uso explícito.

## Gameplay atual

### PLAY-01 — Cinco jogos locais

**Entrega funcional:** MVP entregue
**Validação:** unitária; execução gráfica nativa fora do fluxo atual

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
Race rejeitam combinações de bot incompatíveis. Smokes nativos por plataforma e
mapa não são requisito deste fluxo.

## Servidor e serviços

### MVDSV-01 — Servidor dedicado

**Entrega funcional:** MVP entregue
**Validação:** unitária; Mac/local; contratos Linux e Windows preservados sem
execução nativa neste fluxo

MVDSV hospeda qualquer um dos cinco gamecodes atuais. Readiness confirma
`status`, gamecode e mapa, aplica configuração pós-map por RCON e restaura a
senha final. O componente não depende permanentemente de KTX.

Pontos separados:

- validação formal do MVD produzido;
- teste dedicado de cada gamecode em suíte funcional, sem gate nativo de
  plataforma.

### HOST-01 — Comando `host`

**Entrega funcional:** MVP entregue
**Validação:** unitária; macOS arm64

O comando oferece os jogos instalados de `play`, executa apenas MVDSV por
padrão e pode compor QTV e QWFWD. Senhas podem vir de prompt sem eco ou arquivo
privado. Bind externo sem senha gera alerta explícito.

### QTV-01 — Relay HTTP/MVD

**Entrega funcional:** MVP entregue para HTTP e upstream
**Validação:** unitária; Mac/local; contratos Linux e Windows preservados sem
execução nativa neste fluxo

QTV opera isoladamente com upstream remoto ou depois de um MVDSV local. O
preflight valida endpoint; readiness confirma processo, HTTP e registro do
upstream.

Ponto separado:

- teste de protocolo de espectador e stream MVD, sem runner ou smoke nativo.

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
preservados como compatibilidade; smokes nativos dos runtimes não fazem parte
do fluxo atual

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

**Entrega funcional:** completa e publicada na `0.7.3`
**Validação:** regressão local e matriz da PR 3 concluídas em 7/7 jobs no
Ubuntu, macOS e Windows com Python 3.10 e 3.13, incluindo identidade e reparse
point nativos Windows; os números de testes são evidência do check do commit e
não ficam duplicados neste roadmap; smokes nativos dos runtimes não fazem parte
do fluxo atual

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
`unzip` ou `Expand-Archive`. A validação do contrato de arquivos é
multiplataforma completa após a matriz verde da PR 3; esses testes não são
smokes nativos de ezQuake, MVDSV,
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

**Entrega funcional:** completa e publicada na `0.7.3` (código originado na
PR 2; merge `b833ba45e08a9de644dc7368f82c905522a0a558`)

**Validação:** registro histórico da PR 2; o checkout corretivo tem regressão
Mac/local com Python 3.10 e 3.13. Casos nativos não são inferidos pela suíte
portável nem exigidos para a promoção atual.

Artefatos persistentes exigem HTTPS, tamanho, SHA-256, limite e deadline.
Metadados dinâmicos são efêmeros e limitados. Retries são restritos a falhas
transitórias; temporários recebem `0600` no POSIX e só são promovidos
atomicamente depois da validação. A DACL privada dos temporários Windows passa
pela fronteira definida no [ADR 0003](adr/0003-dacl-privada-windows.md), validada
nos jobs Windows da PR 4 com Python 3.10 e 3.13.
Autenticação, expiração e proteção contra rollback ou freeze permanecem na
[issue #48](https://github.com/x86dx2/x86qw/issues/48).

### SEC-04 — DACL privada no Windows

**Entrega funcional:** implementada e publicada na `0.7.3`

**Validação:** unitária; contrato Windows preservado, sem runner Windows ou
smoke de runtime no fluxo atual

Objetos privados gerenciados nascem com DACL protegida e somente duas ACEs de
controle total: usuário atual e `LOCAL SYSTEM`. A política abrange o plano de
controle `.x86qw/`, sessões, locks, journals, logs, pedidos de parada,
configurações sensíveis, staging, downloads e o diretório temporário do
bootstrap PowerShell. Arquivos externos de senha são somente validados e nunca
reescritos. A operação falha fechada quando a ACL persistente não pode ser
comprovada. A matriz da PR 4 comprovou criação sob herança hostil, os dois
principals permitidos, rejeição de arquivos externos inseguros, proteção do
bootstrap e leases contra substituição. O código não solicita elevação; o
contrato Windows permanece disponível, mas a operação atual no Mac não executa
smoke nativo nem depende do PR 11.

### SEC-05 — Confiança do ezQuake stable no macOS

**Entrega funcional:** preservação upstream implementada e publicada na `0.7.3`

**Validação:** unitária, auditoria de assinatura/hashes no macOS e registro
histórico da matriz 7/7; smokes de abertura, Gatekeeper, arm64 e Intel não fazem
parte do fluxo operacional atual

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
**Validação:** unitária e regressão portável Mac/local; smokes nativos não fazem
parte do fluxo atual

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
hash, migração, verificação apropriada e aprovação de release próprios. Nenhum deles
deve ser incorporado como efeito colateral de `play`, `host`, `update` ou
`upgrade`.

## Gates para a próxima fase

### Estabilização 1.0

O trabalho corretivo atual parte do HEAD `3bbc7a01faf8d472c5ccbab9233e05e9abadc379`
e está descrito em [stabilization-1.0.md](implementation/stabilization-1.0.md).
As frentes abaixo são código de preparação local; nenhuma delas autoriza
publicação da `1.0.0` por si só. A publicação remota opcional permanece
separada e sujeita a revisão humana e trust metadata, sem gates nativos,
runners ou environments como pré-requisito deste fluxo Mac.

- [~] SemVer, schemas de estado/recibo, envelopes JSON e códigos estáveis
  implementados no checkout corretivo com testes portáveis; ainda não publicados
  nem aprovados em PR;
- [~] fixtures e migração unilateral 0.7.x → 1.0 com preservação de ownership;
  0.8.x/0.9.x permanecem somente contratos prospectivos até existirem releases
  públicas reais;
- [~] verificador de trust root/current/snapshot e papel `evidence` com
  rollback, freeze, expiração, rotação e assinatura RSA-PSS fail-closed;
  custódia e revisão independente da chave continuam abertas;
- [~] candidato imutável com checksums, ownership explícito, SBOM, provenance e promoção sem rebuild;
  a promoção local no Mac não depende de evidência nativa;
- [ ] chave de produção, revisão criptográfica independente e rotação cerimonial;
- [ ] aprovação humana e publicação de metadata por último, mantendo a
  fronteira separada de trust metadata;
- [x] ownership de runtime, inventários, ferramentas e site registrado em
  `CODEOWNERS`, sem regra de workflow externo;
- [ ] release `1.0.0` preparada e publicada em etapa separada.

- [x] validação local reproduzível no Mac; Linux e Windows permanecem
  representados nos contratos, sem runners ou smokes nativos;
- [ ] comportamento de distribuição do stable macOS documentado com o
  candidato exato, sem transformar essa documentação em smoke obrigatório;
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
- [x] contratos nativos preservados sem gate operacional de smoke no PR 11;
- [ ] publicação de qualquer versão posterior exige nova aprovação explícita.
