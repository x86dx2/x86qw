# Release truth

Este documento define qual autoridade responde a cada pergunta sobre a
release. Quando duas fontes divergem, o estado é `BLOCKED`; a última fonte
consultada não ganha precedência por ser mais recente.

## Hierarquia de autoridade

1. bytes publicados e digest conferido em mais de uma fonte (`E2`);
2. candidato e commit de produto vinculados pelo workflow;
3. receipt final e evidência M3 do candidato exato (`E3`);
4. metadata TUF autenticada pela root incorporada;
5. run/artefacto de CI e documentação derivada.

Nenhuma release note ou issue substitui uma autoridade de bytes. O
[JSON desta fotografia histórica](release-truth.json) é um índice, não um novo
catálogo TUF. A autoridade corrente é
[`release-truth-current.json`](release-truth-current.json), observado em
2026-08-28T02:32:21Z.

## Identidade observada

| Campo | Valor | Evidência |
| --- | --- | --- |
| tag | `x86qw-installer-1.0.0` | release auditada |
| commit de produto | `e12ed081b968f820f47200e4be954a4f444056a1` | candidate/promoção |
| instalador | `600825` bytes; `d3274e6aa2f1e3078ac5000ffae8b97c9efd329f3c2a87499bf1c57e5f388cb8` | E2 GitHub/GitLab |
| candidate | `17405` bytes; `0bde0550895cab24abf8a3ee974da011e031fea11279148a41635e173cbdcc21` | candidato exato |
| native | `25/25` no Apple M3 | E3 |
| rebuild | não observado | E4 ausente |
| mirror | GitHub/GitLab iguais para o instalador | E2; redundância operacional ainda não provada |

## Estado por dimensão

| Dimensão | Estado | Regra |
| --- | --- | --- |
| artefato | VERIFIED FACT | usar tag, commit, bytes e digests acima |
| validação | VERIFIED FACT no M3; BLOCKED nas plataformas não-M3 | não extrapolar E3 |
| audiência owner-only | VALID_FOR_SINGLE_USER_M3 | candidato exato, M3 e lifecycle single-user verificados |
| audiência external-public | NO-GO | exige EP-0–EP-5 e autorização explícita |
| TUF técnico | HEALTHY | root v1, timestamp v30, snapshot/targets v29 e monitor saudável |
| release operacional | CONVERGED_CANDIDATE_DEPLOYMENT | projeção 33136179763, bootstraps/product/catalog/release-truth verificados |

## Contradições abertas

| ID | Contradição | Como resolver | Estado |
| --- | --- | --- | --- |
| RT-01 | receipt final `public_acceptance` aponta para RC1; evidência final está em arquivo separado | manter separado no owner-only; reconciliar somente em uma promoção external-public | ACCEPTED_OWNER_ONLY |
| RT-02 | source/release/deployment/development e audiência apresentam drift | projeção corrente e site foram atualizados e verificados | RESOLVED_DEPLOYMENT_VERIFIED |
| RT-03 | ownership/SBOM `87/87` `unclassified`/`NOASSERTION` | classificar cada item, ou declarar exceção aprovada com expiry | DEFERRED_EXTERNAL |
| RT-04 | mirror de pacote único não prova redundância operacional | manter igualdade GitHub/GitLab e registrar risco | DEFERRED_CONDITIONAL_UPGRADE |
| RT-05 | fotografias históricas divergem deliberadamente do estado atual | reauditar documentos current sem apagar snapshots datados | DOCUMENTED_HISTORICAL |

## Regras para consumidores

- `latest` não é uma autoridade suficiente: resolve via catálogo TUF e
  receipt/candidato verificados;
- `owner-only`, `external-public`, `supported`, `conditional` e `preview` não
  são sinônimos;
- não informar headers HTTP, Core Web Vitals, OAuth, webhook ou suporte nativo
  sem uma evidência específica;
- mudanças nos bytes exigem candidato novo e invalidam a cadeia anterior;
- uma mudança somente documental não cria `1.0.1`.

## Evidências relacionadas

- [baseline](AUDIT-BASELINE.md);
- [release final existente](../releases/1.0.0-owner-only-publication-2026-08-15.md);
- [aceitação M3](../releases/1.0.0-owner-only-public-acceptance-m3-2026-08-15.json);
- [runbook de release](../runbooks/release.md);
- [JSON](release-truth.json).
