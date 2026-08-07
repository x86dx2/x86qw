## Baseline atual

O estado remoto atual de `main` inclui o merge da PR #74 no commit
`adf6f158b24a3f576884013a2a12b20cafeb94c0`. A raiz de trabalho
`/Users/x86/git-projects/x86qw` permanece separada e dirty; este status não
autoriza limpar nem sobrescrever aquela árvore.

## Versão pública

`0.7.4` é a release corretiva pública (PR #71, merge
`b2b18f0341ef7f7b56faa79ebdbb9fe2ea396c8b`). O bundle público tem 286223
bytes e SHA-256
`37f1372d2252a72ebdacb489ac15aacb45d45cebc5ee537ef158e43ed4e23e7f`.
`0.7.3` permanece imutável. Nenhuma release `1.0.0` foi publicada.

## Estado da jornada

- PRs A–F e G estão mescladas; PR #73 corrigiu a leitura do campo de chip do
  Apple M3 e PR #74 tornou a instalação do smoke nativo determinística.
- Um candidato local `1.0.0` foi preparado e verificado em área isolada a
  partir de `b09143d0c2514760d8c1ac36664c9113dd7372f8`. Seu
  `candidate.json` tem SHA-256
  `970919d35461f3c7cf681d3a9f25692d69d764186d2b961ce1df520477acaa46`; o
  bundle do instalador tem SHA-256
  `93d9a59d7c485880062643b3f8eb143a3e10009292c77ad2096438770398c145`.
- Esse candidato é evidência de preparação/verificação, não uma release, RC,
  promoção ou autorização de suporte.

## Evidência atual

- PR #74: CI `Validate #172` verde em 7/7 jobs.
- Gate local do candidato: 1.395 testes de manutenção, 37 skips explícitos e
  5 testes do site aprovados.
- H foi executado no host macOS Apple M3 Pro. O harness chegou à CLI com
  seleção explícita de plataforma/canal/release, mas o primeiro caso falhou
  fechado antes da instalação porque a ZIPAPP não contém
  `_x86qw/trust/root.json`. Não há recibo nativo de instalação aprovado.
- Os endpoints públicos canônicos para a root e o timestamp TUF respondem
  `404`; o catálogo legado continua respondendo, mas não é autoridade TUF.

## Gates pendentes

| Gate | Estado atual |
|---|---|
| trust de produção | pendente: não há root, metadata assinada nem publisher público |
| E2 operacional | pendente: faltam cerimônia, fingerprints, metadata e mirrors |
| evidência nativa M3 | pendente; H falhou antes do lifecycle por ausência da root |
| `1.0.0-rc.1` | não criada/publicada; período de uso não iniciado |
| H / promoção `1.0.0` | não aberta; bloqueada pelos gates acima |

O ADR 0007 registra o waiver solo-maintainer e a aprovação do proprietário; ele
não é revisão independente e não substitui chaves geradas fora do workspace,
metadata assinada, evidência M3, RC, período de uso ou mirrors convergentes.

## Próxima ação

Executar a cerimônia E2 fora do workspace, registrar fingerprints e gerar a
cadeia TUF assinada com bytes aprovados. Depois disso, preparar o RC exato,
executar novamente o H no host M3, validar mirrors e só então abrir a promoção
`1.0.0`. Não usar catálogo legado, root de teste ou evidência sintética como
atalho.
