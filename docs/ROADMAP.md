# Roadmaps do x86QW

Este arquivo é o índice e o roadmap do núcleo de distribuição. O planejamento
detalhado do ecossistema está em
[ROADMAP-QUAKE-ECOSYSTEM.md](ROADMAP-QUAKE-ECOSYSTEM.md).

## Baseline atual

Baseline consolidado: `018e61f09b1f542f873d91be37dc8c1ebf3db589`, branch
`main`, em 31 de julho de 2026.

- instalador público `0.2.1`; `0.2.0` permanece imutável no histórico;
- 50 pacotes e 21 componentes;
- cinco jogos: KTX, Final Arena, Pro-X, Team Fortress e Total Destruction 2;
- ezQuake stable e nightly para macOS universal, Linux x86-64 e Windows x64;
- MVDSV, QTV e QWFWD para macOS arm64, Linux amd64 e Windows x64;
- comandos públicos `play`, `host`, `proxy`, `qtv`, `hub`, `update`, `upgrade`,
  `verify`, `repair`, `cleanup`, `uninstall` e `version`;
- catálogos declarativos de capacidades, runtimes, jogos e compatibilidade;
- CI de qualidade multiplataforma e publicação separada por gate.

## Estado do núcleo

| Frente | Entrega funcional | Validação |
|---|---|---|
| Distribuição e instalador | completa para o escopo atual | unitária; matriz macOS/Linux/Windows no CI |
| KTX e modos | completa para o catálogo atual | unitária; smoke macOS existente |
| Frogbots | MVP entregue | unitária; combinações suportadas cobertas |
| Jogos legados atuais | MVP entregue | unitária; smoke gráfico manual permanece |
| MVDSV e `host` | MVP entregue | unitária; smoke macOS existente |
| QTV HTTP/upstream | MVP entregue | unitária; smoke macOS existente |
| QWFWD | MVP entregue | unitária; forwarding de rede separado |
| Catálogo declarativo | completa para runtimes e jogos atuais | unitária e validação estrutural |
| Lifecycle e recuperação | completa para uma stack em primeiro plano por instalação | unitária em lock concorrente, PID reutilizado, órfão, sinais, crash e rollback; smokes nativos ainda parciais |
| Site e documentação | completa para o baseline atual | unitária, dry-run e deploy público do Worker verificado na `0.2.1` |

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
