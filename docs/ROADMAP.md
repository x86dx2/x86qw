# Roadmaps do x86QW

Este arquivo é o índice e o roadmap do núcleo de distribuição. O planejamento
detalhado do ecossistema está em
[ROADMAP-QUAKE-ECOSYSTEM.md](ROADMAP-QUAKE-ECOSYSTEM.md).

## Baseline atual

Baseline publicada: tag `x86qw-installer-0.7.1`. O estado funcional é descrito
pela própria tag imutável no commit
`78dc30b58f9ba2a2ec8aeb31879d9b8072ab576b`, publicada em 3 de agosto de 2026.

Baseline inicial da issue #45: merge
`afb4f666095e37fe262b87b49339e18d25738522`.
A correção `0.7.1` passou em 7/7 jobs, 393 testes de manutenção e quatro testes
do site. A `0.7.0` permanece imutável no histórico.

- instalador público `0.7.1`; as linhas anteriores permanecem imutáveis no histórico;
- 61 pacotes e 21 componentes;
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
| Distribuição e instalador | completa para o escopo atual | unitária; CI portável macOS/Linux/Windows concluída; smokes nativos pendentes |
| KTX e modos | completa para o catálogo atual | unitária; os 24 modos exercitados no macOS em janela |
| Frogbots | MVP entregue | unitária; os 22 modos compatíveis exercitados no macOS |
| Jogos legados atuais | MVP entregue | unitária; smoke gráfico manual permanece |
| MVDSV e `host` | MVP entregue | unitária; smoke macOS existente |
| QTV HTTP/upstream | MVP entregue | unitária; smoke macOS existente |
| QWFWD | MVP entregue | unitária; forwarding de rede separado |
| Catálogo declarativo | completa para runtimes e jogos atuais | unitária e validação estrutural |
| Lifecycle e recuperação | completa para exclusão entre stack e manutenção por instalação | unitária em lock concorrente, journal sem lock, PID reutilizado, árvore órfã, temporário sensível, sinais, crash e rollback; smokes nativos ainda parciais |
| Site e documentação | completa para o baseline atual | unitária, dry-run e deploy público do Worker para a `0.7.1` |
| Layout de instalação | bootstrap limpo com plano de controle `.x86qw/` e serviços contextuais | unitária; bootstrap completo macOS arm64; Linux e Windows dependem da matriz |
| Downloads remotos | completa no código corretivo da issue #45; ainda não publicada | 507 testes de manutenção e cinco do site aprovados localmente no macOS; dez skips dependentes de plataforma/rede; matriz do PR pendente |

“MVP entregue” não significa que o runtime foi executado em todas as
plataformas. A coluna de validação é sempre a autoridade para essa distinção.

## Próximos marcos do núcleo

1. Executar e registrar smokes nativos dos clientes e serviços em Linux amd64 e
   Windows x64, além do cliente em macOS Intel.
2. Formalizar o smoke de MVD gerado pelo MVDSV e o teste de encaminhamento do
   QWFWD em suíte de rede isolada.
3. Transformar a validação de release em evidência publicada somente após
   aprovação humana dos checks das três plataformas.
4. Reduzir hardcodings residuais de apresentação da CLI sem alterar contratos
   ou gameplay.
5. Manter bundles publicados imutáveis e preparar uma versão nova somente em
   etapa de release separada.
6. Autenticar e versionar metadados contra rollback e freeze na issue #48, sem
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
