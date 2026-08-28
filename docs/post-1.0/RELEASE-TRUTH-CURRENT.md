# Release truth — escopo corrente M3-first

O estado corrente de deployment é exclusivamente
`https://qw.x86.com.br/api/v1/release-truth.json`.
[`release-truth-current.json`](release-truth-current.json) contém
somente esse ponteiro e os invariantes de audiência. Verifique a observação
viva com:

```sh
python3 maintenance/tools/verify_live_release_truth.py
```

[`release-truth-projection-seed.json`](release-truth-projection-seed.json) é a
semente offline versionada usada pela projeção estática e pelos testes. Ela não
é autoridade do deployment corrente. O restante deste documento registra a
última fotografia auditada, sem convertê-la em verdade viva permanente.

## Última fotografia verificada em 2026-08-28T02:32:21Z

- `MAIN=GREEN`: a linha `main` passou o Validate no run
  [33135951867](https://github.com/x86dx2/x86qw/actions/runs/33135951867), no
  commit `962fb2b2cc27560e982c2255d9299a55f16acdd1`.
- `TUF=HEALTHY`: root v1, timestamp v30 e snapshot/targets v29 autenticam o
  catálogo público de 75 pacotes. O timestamp expira em
  `2026-09-27T02:15:12Z`, fora da janela de alerta de seis horas no momento da
  observação.
- `1.0.0 owner-only=VALID_FOR_SINGLE_USER_M3`: o candidato exato permanece
  válido para um mantenedor no Apple M3, com instalador SHA-256
  `d3274e6aa2f1e3078ac5000ffae8b97c9efd329f3c2a87499bf1c57e5f388cb8`.
- `external-public=NO-GO`: migração histórica, soak externo, custódia
  independente/RTO e aceite por usuário externo continuam condicionais a uma
  decisão explícita de abrir a audiência.
- `FEATURE WORK=ALLOWED` enquanto S0-M3 permanecer verde.

## Autoridades

| Autoridade | Valor na fotografia de 2026-08-28 |
| --- | --- |
| source | baseline current `1.0.5` em `dist/installer/VERSION` |
| candidate/release | `x86qw-installer-1.0.0`, commit `e12ed081b968f820f47200e4be954a4f444056a1`, audiência `owner-only` |
| deployment | site, bootstraps, product, catálogo, trust e release-truth convergentes |
| development | `main` verde no Validate `33135951867`; candidato exato preservado pelo receipt e pela evidência M3 |
| scope | um usuário, um mantenedor e laboratório nativo Apple M3 |

## Deployment público

O run [33136179763](https://github.com/x86dx2/x86qw/actions/runs/33136179763)
reparou e verificou a projeção owner-only. O candidato carregado é o mesmo
no endpoint canônico `https://qw.x86.com.br/`: product e catálogo reportam `1.0.0`, a raiz HTTP
200 mostra `owner-only`, e `/api/v1/release-truth.json` responde 200.

O catálogo público tem SHA-256
`a03a8b0e3dcd97a66d338891dacd6ca80befdbee907ed9b83007a538bb97646a`; a
projeção de release-truth tinha SHA-256
`601e30eb9025a76782e75fe417723fda404539d065d5c1bd338a4a4b382a6cf7` nessa
observação pública.
GitHub e GitLab continuam byte-equal para o instalador owner-only. A release
final tem 600825 bytes e candidate SHA-256
`0bde0550895cab24abf8a3ee974da011e031fea11279148a41635e173cbdcc21`.

O receipt da projeção registra o mesmo estado: `CONVERGED_CANDIDATE_DEPLOYMENT`,
root probe `200_OWNER_ONLY`, repair artifact `9672118367` e publicação de site
`projection-only`. A execução usou o commit observado
`962fb2b2cc27560e982c2255d9299a55f16acdd1`; ela não reconstruiu o candidato nem
alterou seus bytes imutáveis.

## TUF técnico e operação

Na fotografia, o TUF público era autenticado pela root Ed25519 v1. O timestamp v30 foi
renovado no run `33135314707`, com artifact `9671800710` e relatório SHA-256
`fe90b29ca4aa49f3b3c5a33897edd67b7069685d1622f3ae8d85f348a172e7cb`.
Snapshot e targets estão em v29 e expiram depois do timestamp. O monitor
público passou nos limiares de seis e uma hora nos dois domínios.

O drill técnico de recuperação está registrado no run `31900793093`; ele prova
o contrato operacional com chaves efêmeras, mas não prova custódia humana
independente. A custódia, backup, RTO e sucessão continuam gates de
`external-public`. A correção histórica da versão da root está em
[`ERRATA-TUF-ROOT-VERSION.md`](ERRATA-TUF-ROOT-VERSION.md).

## Gates e limites

### Concluído para `owner-only`

- candidato construído uma vez, com digest e instalador imutáveis;
- evidência M3 do candidato exato e lifecycle descartável;
- aceitação pública `single-user`, incluindo instalação limpa, verify, update,
  repair, changes, uninstall e purge;
- mirrors, bootstraps, product, catálogo, TUF, site e release-truth verificados;
- trabalho funcional liberado enquanto `S0-M3` permanecer verde.

### Estacionado até `external-public`

- migração real da instalação pública `0.7.13` e preservação de upgrades;
- soak protegido de sete dias e aceite por usuário externo;
- custódia independente, backup e RTO TUF observados em produção;
- evidência nativa fora do Apple M3 e qualquer claim de suporte adicional;
- QWLeague ou outra integração externa sem contato e contrato explícitos.

As issues históricas e seus artifacts continuam preservados; issue fechada não é
sinônimo de autorização `external-public`. A decisão vigente permanece a do
[`ADR 0008`](../adr/0008-owner-only-release-gates.md), e a release histórica
detalhada está em
[`1.0.0-owner-only-publication-2026-08-15.md`](../releases/1.0.0-owner-only-publication-2026-08-15.md).
