# Estado atual do projeto

## Baseline real

A `main` pública observada neste checkpoint é
`origin/main@d4a92c0fe29786fdc6ec5c7d978813cb634be62c`. A implementação em
execução fica na branch `codex/integrate-1.0`; o SHA exato do candidato é
registrado em seu `candidate.json` e deve ser tratado como a identidade dos
artefatos.

## Versão pública

O último release público confirmado no GitHub é `0.7.13`: instalador de
581883 bytes, SHA-256
`114604400e1fd18c4180624314d4bc8ca9b6d4559ed26cfe8d0a767287f2aa32`.
A versão-fonte de `dist/installer/VERSION` acompanha essa geração pública.
O candidato local `1.0.0-rc.1` é uma geração separada e não promove a versão
pública por si só.

## Estado de confiança

A root Ed25519 incorporada é validada localmente. A última cadeia pública
observada tinha root v1 e targets/snapshot/timestamp v11; o timestamp expirou em
`2026-08-11T23:10:15Z`. Os endpoints do portal não responderam no checkpoint
atual. Portanto a confiança técnica do snapshot histórico não é evidência de
renovação operacional, custódia humana independente ou recuperação após
expiração.

## Estado local

- downloader, archives, SemVer, launchers, `changes` e `migrate` compartilham
  contratos de runtime;
- fixtures de migração cobrem os instaladores públicos `0.7.0`–`0.7.13`;
- o publisher é build-once e falha fechado para bytes ausentes, mirrors
  divergentes e metadata TUF fora de ordem;
- o candidato carrega o site renderizado e os binários de `dist`, sem depender
  de uma instalação pessoal em `quake-world/`;
- o harness Mac M3 executa plano candidato-owned e registra handoff, smoke
  normalizado e agregado unsigned pendente;
- o catálogo separa `supported`, `conditional` e `preview`: stable macOS
  permanece condicional, nightly e Linux/Windows/macOS Intel permanecem preview
  quando não há evidência nativa do candidato exato;
- a instalação pessoal temporária não é usada pelos testes de release.

## Bloqueios atuais

1. o candidato precisa ser reconstruído após o último alinhamento de contratos e
   passar novamente pelos gates portáteis e pelos 18 casos nativos no M3;
2. a evidência nativa é unsigned/pending até uma cerimônia externa de assinatura;
3. o timestamp TUF público está expirado e o portal está indisponível; não há
   signer/custódia de produção demonstrados neste ambiente;
4. a publicação final, mirrors e metadata-last permanecem bloqueados até o
   candidato exato, a evidência assinada e a operação TUF estarem disponíveis;
5. Linux, Windows e macOS Intel continuam preview; nenhum resultado portátil é
   apresentado como smoke nativo dessas plataformas.

## Veredito

A implementação local pode avançar para um `1.0.0-rc.1` candidato, mas não há
autorização técnica para declarar RC publicado ou promover `1.0.0`. O release
público `0.7.13` não foi alterado por este checkpoint.

## Próxima ação

Reconstruir o candidato a partir do commit integrado, executar novamente os gates
portáteis e o harness M3 sobre os mesmos bytes, anexar o agregado unsigned ao PR
e manter a promoção bloqueada até a disponibilidade do portal TUF e da
custódia de assinatura.
