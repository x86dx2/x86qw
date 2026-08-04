# Roadmaps do x86QW

Este arquivo é o índice e o roadmap do núcleo de distribuição. O planejamento
detalhado do ecossistema está em
[ROADMAP-QUAKE-ECOSYSTEM.md](ROADMAP-QUAKE-ECOSYSTEM.md).

## Baseline atual

Baseline publicada: tag `x86qw-installer-0.7.2`. O estado funcional é descrito
pela própria tag imutável, publicada em 4 de agosto de 2026. A `0.7.1`
permanece inalterada no histórico.

Baseline inicial da issue #45: merge
`afb4f666095e37fe262b87b49339e18d25738522`.
A correção `0.7.1` passou em 7/7 jobs, 393 testes de manutenção e quatro testes
do site. A `0.7.0` permanece imutável no histórico.

Baseline corretiva consolidada após a PR 4: merge
`206adc46df6aced49eee7ac1fcae3cf331f07a63`. Downloader limitado, fronteira
única ZIP/PK3/PYZ e DACL privada Windows estão integrados, mas não foram
publicados nem promovidos no catálogo. O candidato da PR 5 parte desse commit,
preserva o stable macOS upstream e também permanece não publicado.

Baseline inicial da PR 6: merge
`00098330e5833ba2c83c7121272d644c2a204a7b`. O HEAD documental atual da PR 6 é
`29d76a48721190aad1203d0986a31d839d62070e`. Downloader, archive, filesystem
privado, catálogos, estado/recibos/migração, transações compostas, UI,
gameplay, plataforma e supervisor possuem ownership canônico no runtime. O
zipapp tem 56 membros e sua projeção é derivada de um manifesto declarativo,
também conferido contra o arquivo produzido. A regressão local integral passou;
a issue #52 permanece como trilha auditável pausada. Esse código foi integrado
à linha `0.7.2` pela PR 64.

- instalador público `0.7.2`; as linhas anteriores permanecem imutáveis no histórico;
- 62 pacotes e 21 componentes;
- cinco jogos: KTX, Final Arena, Pro-X, Team Fortress e Total Destruction 2;
- ezQuake stable e nightly para macOS universal, Linux x86-64 e Windows x64;
- MVDSV, QTV e QWFWD para macOS arm64, Linux amd64 e Windows x64;
- comandos públicos `play`, `host`, `proxy`, `qtv`, `status`, `hub`, `update`, `upgrade`,
  `verify`, `repair`, `cleanup`, `uninstall` e `version`;
- navegador de terminal por tarefas, com busca, teclado, multisseleção, linhas
  alinhadas e coloridas e fallback numerado;
- catálogos declarativos de capacidades, runtimes, jogos e compatibilidade;
- CI de qualidade multiplataforma e publicação separada por gate.

## Estado do núcleo

| Frente | Entrega funcional | Validação |
|---|---|---|
| Distribuição e instalador | completa para o escopo atual; clientes macOS condicionais | unitária; CI portável macOS/Linux/Windows concluída; smokes nativos pendentes |
| KTX e modos | completa para o catálogo atual | unitária; os 24 modos exercitados no macOS em janela |
| Frogbots | MVP entregue | unitária; os 22 modos compatíveis exercitados no macOS |
| Jogos legados atuais | MVP entregue | unitária; smoke gráfico manual permanece |
| MVDSV e `host` | MVP entregue | unitária; smoke macOS existente |
| QTV HTTP/upstream | MVP entregue | unitária; smoke macOS existente |
| QWFWD | MVP entregue | unitária; forwarding de rede separado |
| Catálogo declarativo | completa para runtimes e jogos atuais | unitária e validação estrutural |
| Lifecycle e recuperação | completa para exclusão entre stack e manutenção por instalação | unitária em lock concorrente, journal sem lock, PID reutilizado, árvore órfã, temporário sensível, sinais, crash e rollback; smokes nativos ainda parciais |
| Site e documentação | completa para o baseline atual | unitária, dry-run e deploy público do Worker para a `0.7.2` |
| Layout de instalação | bootstrap limpo com plano de controle `.x86qw/` e serviços contextuais | unitária; bootstrap completo macOS arm64; contrato nativo Windows validado no CI; bootstrap completo sob conta padrão e runtimes reais pendentes no PR 11 |
| Downloads remotos | completa e publicada na `0.7.2` | evidência da PR 2: 565 testes de manutenção e cinco do site aprovados; oito skips explícitos (sete Windows e um smoke de rede); matriz concluída em Ubuntu, macOS e Windows com Python 3.10 e 3.13 |
| Arquivos ZIP/PK3/PYZ | completa e publicada na `0.7.2` | regressão local em Python 3.14 e 3.10: `Ran 695 tests` e `OK (skipped=15)` na manutenção, mais `Ran 5 tests` e `OK` no site; matriz da PR 3 concluída em 7/7 jobs no Ubuntu, macOS e Windows com Python 3.10 e 3.13, incluindo identidade e reparse point nativos Windows; smokes nativos dos runtimes separados |
| DACL privada Windows | implementada e publicada na `0.7.2` | 745 testes de manutenção e cinco do site na matriz; DACL validada nativamente no runner Windows com Python 3.10 e 3.13; smoke de runtime sob conta padrão pendente |
| Confiança do stable macOS | preservação upstream publicada na `0.7.2` | matriz 7/7 no [run 30871046055](https://github.com/x86dx2/x86qw/actions/runs/30871046055); assinatura, sandbox e hashes auditados; primeira/segunda abertura, Gatekeeper, arm64 e Intel pendentes no PR 11 |
| Fronteiras `x86qw_runtime` | integradas e publicadas na `0.7.2`: ownership canônico de I/O, filesystem privado, catálogos, estado/recibos/migração, transações, UI, gameplay, plataforma, sessão e supervisor | 1.197 testes de manutenção aprovados localmente, com 37 skips explícitos, e 5 testes do site; matriz integrada da PR 64 em 7/7 jobs; smokes nativos reservados ao PR 11 |

“MVP entregue” não significa que o runtime foi executado em todas as
plataformas. A coluna de validação é sempre a autoridade para essa distinção.

## Próximos marcos do núcleo

1. Manter a PR 6 pausada como trilha auditável; as fronteiras validadas foram
   integradas pela PR 64 e publicadas na `0.7.2`.
2. Manter o stable macOS upstream sem mutação e executar os smokes nativos do
   candidato exato; a disponibilidade segue condicional até o PR 11.
3. Executar e registrar smokes nativos dos clientes e serviços em Linux amd64 e
   Windows x64, além do cliente em macOS Intel.
4. Formalizar o smoke de MVD gerado pelo MVDSV e o teste de encaminhamento do
   QWFWD em suíte de rede isolada.
5. Transformar a validação de release em evidência publicada somente após
   aprovação humana dos checks das três plataformas.
6. Reduzir hardcodings residuais de apresentação da CLI sem alterar contratos
   ou gameplay.
7. Manter bundles publicados imutáveis e preparar uma versão nova somente em
   etapa de release separada.
8. Autenticar e versionar metadados contra rollback e freeze na issue #48, sem
   confundir esse contrato com os limites de transporte já implementados.

## Decisões históricas superadas

- A decisão antiga de não expor modos KTX foi superada: os modos são
  declarativos em `modes.json` e aparecem em `play` e `host`.
- A decisão antiga de adiar runtimes de servidor foi superada: MVDSV, QTV e
  QWFWD já são componentes independentes e verificáveis.
- A contagem histórica de 18 componentes foi superada pelo catálogo atual de
  21 componentes.
- A dependência rígida MVDSV → KTX foi superada: o jogo escolhido define o
  gamecode necessário.
- A dependência rígida QTV → MVDSV local foi superada: QTV pode usar upstream
  remoto.
- A materialização de `play-support` durante gameplay foi superada: instalação,
  atualização, upgrade ou reparo preparam o conteúdo; execução apenas valida.

Os detalhes históricos anteriores continuam recuperáveis pelo histórico Git.
Este documento mantém somente decisões ainda úteis para orientar a implementação.

## Fora deste ciclo

Novos runtimes, novos jogos, novos mods, novos mapas externos, central de demos,
treinamento como comando próprio e serviços persistentes permanecem fora da
consolidação atual. Qualquer expansão depende primeiro dos gates, smokes e
contratos acima e precisa de uma proposta separada.
