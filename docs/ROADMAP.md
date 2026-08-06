# Roadmaps do x86QW

O rascunho de notas da futura 1.0.0 está em
[docs/releases/1.0.0-draft.md](releases/1.0.0-draft.md); ele não autoriza
publicação.

Este arquivo é o índice e o roadmap do núcleo de distribuição. O planejamento
detalhado do ecossistema está em
[ROADMAP-QUAKE-ECOSYSTEM.md](ROADMAP-QUAKE-ECOSYSTEM.md).

## Baseline atual

Baseline publicada: tag `x86qw-installer-0.7.3`, commit
`3bbc7a01faf8d472c5ccbab9233e05e9abadc379`. O estado funcional é descrito pela
própria tag imutável; `0.7.1` e `0.7.2` permanecem inalteradas no histórico.

Baseline inicial da issue #45: merge
`afb4f666095e37fe262b87b49339e18d25738522`.
A correção `0.7.1` passou em 7/7 jobs, 393 testes de manutenção e quatro testes
do site. A `0.7.0` permanece imutável no histórico.

Baseline corretiva consolidada após as PRs 2–5: merge
`3bbc7a01faf8d472c5ccbab9233e05e9abadc379` (tag `x86qw-installer-0.7.3`).
Downloader limitado, fronteira única ZIP/PK3/PYZ, DACL privada Windows e
preservação do bundle stable macOS estão integrados no código publicado. As
plataformas nativas continuam declaradas no catálogo, mas não há smokes nativos
neste fluxo Mac/local e eles não são implícitos pela matriz portável.

Baseline inicial da PR 6: merge
`00098330e5833ba2c83c7121272d644c2a204a7b`. O HEAD histórico da PR 6 é
`29d76a48721190aad1203d0986a31d839d62070e`. Downloader, archive, filesystem
privado, catálogos, estado/recibos/migração, transações compostas, UI,
gameplay, plataforma e supervisor possuem ownership canônico no runtime. O
recorte histórico da PR 6 tinha 56 membros no zipapp; o manifesto do HEAD atual
possui 63 membros e continua sendo a fonte derivada conferida pelo builder. A
issue #52 foi encerrada pela PR 62; a revisão final de 1.0 permanece nos gates
P0/P1 atuais. Esse código foi integrado à linha pública pela PR 64; a correção
de entitlements do stable foi publicada na `0.7.3`.

- instalador público `0.7.3`; as linhas anteriores permanecem imutáveis no histórico;
- 63 pacotes e 21 componentes;
- cinco jogos: KTX, Final Arena, Pro-X, Team Fortress e Total Destruction 2;
- ezQuake stable e nightly para macOS universal, Linux x86-64 e Windows x64;
- MVDSV, QTV e QWFWD para macOS arm64, Linux amd64 e Windows x64;
- comandos públicos `play`, `host`, `proxy`, `qtv`, `status`, `hub`, `update`, `upgrade`,
  `verify`, `repair`, `cleanup`, `uninstall` e `version`;
- navegador de terminal por tarefas, com busca, teclado, multisseleção, linhas
  alinhadas e coloridas e fallback numerado;
- catálogos declarativos de capacidades, runtimes, jogos e compatibilidade;
- validação de qualidade diretamente no Mac; publicação remota opcional e
  contratos de outras plataformas permanecem preservados.

## Estado do núcleo

| Frente | Entrega funcional | Validação |
|---|---|---|
| Distribuição e instalador | completa para o escopo atual; clientes macOS condicionais | regressão local no Mac; validação operacional somente macOS; smokes nativos fora do escopo |
| KTX e modos | completa para o catálogo atual | unitária; os 24 modos exercitados no macOS em janela |
| Frogbots | MVP entregue | unitária; os 22 modos compatíveis exercitados no macOS |
| Jogos legados atuais | MVP entregue | unitária; smokes gráficos nativos fora do fluxo |
| MVDSV e `host` | MVP entregue | unitária; execução operacional fora do fluxo |
| QTV HTTP/upstream | MVP entregue | unitária; execução operacional fora do fluxo |
| QWFWD | MVP entregue | unitária; forwarding de rede separado |
| Catálogo declarativo | completa para runtimes e jogos atuais | unitária e validação estrutural |
| Lifecycle e recuperação | completa para exclusão entre stack e manutenção por instalação; correção de ownership efêmera KTX preparada no checkout corretivo | unitária em lock concorrente, journal sem lock, PID reutilizado, árvore órfã, temporário sensível, sinais, crash e rollback; a transferência de ownership KTX continua separada |
| Site e documentação | completa para o baseline `0.7.3`; avisos e ownership explícito preparados para bundles `>=1.0.0` | regressão local do checkout corretivo; dry-run; mirrors e metadata pública ficam em operação remota opcional |
| Layout de instalação | bootstrap limpo com plano de controle `.x86qw/` e serviços contextuais | unitária; bootstrap completo no macOS; contratos Windows permanecem compatibilidade |
| Downloads remotos | completa e publicada na `0.7.3` | regressão local no Mac; compatibilidade Linux/Windows preservada sem runner externo |
| Arquivos ZIP/PK3/PYZ | completa e publicada na `0.7.3` | regressão local no Mac; smokes nativos dos runtimes fora do fluxo |
| DACL privada Windows | implementada e publicada na `0.7.3` | contrato Windows preservado; validação nativa não é requisito operacional |
| Confiança do stable macOS | preservação upstream publicada na `0.7.3` | assinatura, sandbox e hashes auditados; abertura e Gatekeeper não são gates de smoke |
| Fronteiras `x86qw_runtime` | integradas e publicadas na `0.7.3`: ownership canônico de I/O, filesystem privado, catálogos, estado/recibos/migração, transações, UI, gameplay, plataforma, sessão e supervisor | regressão local no Mac aprovada; validação nativa fora do fluxo atual |

“MVP entregue” não significa que o runtime foi executado em todas as
plataformas. A coluna de validação é sempre a autoridade para essa distinção.

## Próximos marcos do núcleo

1. Manter a PR 6 pausada como trilha auditável; as fronteiras validadas foram
   integradas pela PR 64 e publicadas na `0.7.3`.
2. Manter o stable macOS upstream sem mutação e validar o candidato completo
   localmente no Mac; a disponibilidade continua condicionada aos contratos do
   bundle, não a smokes nativos ausentes.
3. Preservar Linux-X64, Windows-X64, macOS-ARM64 e macOS-X64 nos catálogos e
   schemas sem transformar compatibilidade em requisito operacional.
4. Formalizar o teste de MVD gerado pelo MVDSV e o teste de encaminhamento do
   QWFWD em suíte de rede isolada.
5. Transformar a validação de release em candidato publicado somente após
   aprovação humana do fluxo Mac, trust de catálogo, mirrors e P0/P1.
6. Reduzir hardcodings residuais de apresentação da CLI sem alterar contratos
   ou gameplay.
7. Manter bundles publicados imutáveis e preparar uma versão nova somente em
   etapa de release separada.
8. Concluir a revisão e a cerimônia de trust metadata da issue #48; o verificador
   local já falha fechado contra rollback, freeze, equivocation e root não
   ancorado, mas a chave de produção e a promoção pública ainda não existem.

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
