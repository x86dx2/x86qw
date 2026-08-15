# Release train pós-1.0

## Ordem canônica

| Estação | Conteúdo | Gate de saída | Estado |
| --- | --- | --- | --- |
| T0 | `origin/main`, baseline e audit snapshot | baseline datada | VERIFIED FACT |
| T1 | CI portátil e contrato Windows | 0A verde | PASS |
| T2 | TUF operação/recovery | 0B sustentável | BLOCKED |
| T3 | release truth, receipt e audiência | 0C coerente | BLOCKED |
| T4 | backlog, Maker/Checker e riscos | 0D fechado | PROPOSAL |
| T5 | observação owner-only | 0E concluído | BLOCKED |
| T6 | decisão EP-0 | audiência registrada | BLOCKED |
| T7 | migração externa | EP-1 | BLOCKED |
| T8 | soak de sete dias | EP-2 | BLOCKED |
| T9 | TUF/recovery externo | EP-3 | BLOCKED |
| T10 | usuário externo | EP-4 | BLOCKED |
| T11 | plataforma | EP-5 | BLOCKED |
| T12 | `external-public` | todos os receipts coerentes | BLOCKED |

## Regras da train

- uma estação por vez; não pular gate por release note;
- cada candidato tem commit, tamanho, digest, run e Checker;
- qualquer byte novo reinicia T1 e exige candidato novo;
- documentação/fixture/teste não é release corretiva por si só;
- RC1 e release final permanecem objetos distintos;
- metadata TUF é sempre última e verificada depois do deploy;
- uma falha mantém a estação anterior como referência e não sobrescreve
  evidências.

## Estado da carga

A carga observada é a tag `x86qw-installer-1.0.0`, commit de produto
`e12ed081...`, instalador `d3274e...`, candidate `0bde05...`, E2 GitHub/GitLab
e E3 M3 `25/25`; não há E4. Ela não pode ser tratada como uma carga externa
pronta porque T2–T6 estão incompletos na fotografia corrente.

## Handoff entre estações

O Maker publica uma ficha com o estado, evidência e rollback. O Checker repete
a verificação a partir do commit/digest. O release owner decide `advance`,
`hold` ou `rollback`. O handoff não pode conter chaves privadas, cookies,
tokens, claims de CWV ou links que não foram observados.

## Referências

- [master plan](MASTER-PLAN.md);
- [dependency graph](DEPENDENCY-GRAPH.md);
- [gauntlet](GAUNTLET-OPERATING-MODEL.md);
- [release truth](RELEASE-TRUTH.md).
