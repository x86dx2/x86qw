# Roadmap técnico do ecossistema QuakeWorld no x86QW

Este roadmap usa o estado real do produto como baseline e separa entrega de
validação. Ele complementa o [índice geral](ROADMAP.md); não autoriza release,
publicação ou incorporação automática de conteúdo.

Baseline da release anterior: tag `x86qw-installer-0.2.1`, commit
`527d0d1006`. Baseline consolidado do código corretivo: branch `main`, commit
`081657c349f3d4c111334bff49e9db9a6ee17f3c`, em 31 de julho de 2026.

## Escala de estado

**Entrega funcional:** não iniciada · parcial · MVP entregue · completa
**Validação:** não validada · unitária · macOS · Linux · Windows ·
multiplataforma completa

A validação lista somente execução comprovada. Presença de artefato, parsing de
catálogo ou teste portável não equivale a smoke real do runtime naquela
plataforma.

## Baseline do produto

- instalador público `0.2.3`; `0.2.2` e anteriores permanecem imutáveis;
- 52 pacotes e 21 componentes no catálogo;
- ezQuake stable `3.6.9` e nightly `20260616-101233_a86996a`;
- cinco jogos atuais: KTX `1.47`, Final Arena `1.20`, Pro-X `1.1`, Team
  Fortress `2.9` e Total Destruction 2 `2.22`;
- MVDSV `1.11+x86qw.2`, QTV `0+025ca949aca0+x86qw.1` e QWFWD
  `1.30+x86qw.2`;
- cliente macOS universal, Linux x86-64 e Windows x64;
- serviços macOS arm64, Linux amd64 e Windows x64; macOS Intel não é anunciado
  para os serviços;
- CLI com `play`, `host`, `proxy`, `qtv`, `hub`, `update`, `upgrade`, `verify`,
  `repair`, `cleanup`, `uninstall` e `version`;
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

### ARCH-02 — Estado e migração

**Entrega funcional:** completa para formato 2
**Validação:** unitária

O formato 2 preserva perfil, seleção customizada, componentes registrados e
histórico, acrescentando capacidades explícitas e fingerprint. A migração é
unilateral, aparece no plano e não remove nem acrescenta componentes por
inferência.

### ARCH-03 — Execução sem mutação

**Entrega funcional:** completa
**Validação:** unitária

`play`, `host`, `proxy`, `qtv` e `hub` validam e executam conteúdo instalado.
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
**Validação:** unitária; macOS para o fluxo já exercitado

`modes.json` continua como fonte de verdade. Duel, 2on2, 4on4, CTF, Race,
Midair, Practice e os demais modos publicados preservam argumentos e ajuda.

### BOT-01 — Frogbots

**Entrega funcional:** MVP entregue
**Validação:** unitária; smoke real ainda parcial

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
journal legado, PID reutilizado, árvore órfã POSIX, Job Object Windows,
configuração sensível, SIGINT, SIGTERM e crash; smokes nativos dos runtimes
ainda pendentes

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

### SEC-01 — PK3/ZIP

**Entrega funcional:** completa para materialização dedicada
**Validação:** unitária em macOS; matriz portável configurada

Membros são interpretados com semântica POSIX. Traversal, drives, barras
invertidas, controles, nomes reservados, symlinks, membros especiais e colisões
de caixa/Unicode são rejeitados. Limites cobrem quantidade, membro, total,
profundidade, caminho e taxa de compressão.

### SEC-02 — Segredos e endpoints

**Entrega funcional:** completa para a CLI atual
**Validação:** unitária

Prompts usam entrada sem eco; arquivos precisam ser regulares e privados. As
opções legadas permanecem por compatibilidade com alerta. Segredos nunca entram
nos argumentos dos filhos nem em mensagens de erro. Endpoints IPv4, hostname e
IPv6 entre colchetes têm parser próprio.

## Instalador e perfis

### INST-01 — Ciclo de vida instalado

**Entrega funcional:** completa para o escopo atual
**Validação:** unitária; matriz de CI pendente

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

## Site e documentação

### DOC-01 — Fatos públicos

**Entrega funcional:** completa
**Validação:** unitária e dry-run do Worker

Versão, pacotes, componentes, comandos, jogos, runtimes e plataformas são
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
- [ ] publicação de qualquer versão posterior exige nova aprovação explícita.
