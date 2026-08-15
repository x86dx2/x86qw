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
[JSON desta verdade](release-truth.json) é um índice, não um novo catálogo TUF.

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
| audiência owner-only | AT-RISK na fotografia | receipt e P1s precisam ser reconciliados |
| audiência external-public | NO-GO | exige EP-1–EP-5 |
| TUF técnico | HEALTHY no intervalo auditado | lease e recovery continuam pendentes |
| release operacional | BLOCKED | Gate 0A e release truth não fechados |

## Contradições abertas

| ID | Contradição | Como resolver | Estado |
| --- | --- | --- | --- |
| RT-01 | receipt final `public_acceptance` aponta para RC1; evidência final está em arquivo separado | gerar/validar receipt que referencia o candidato final e o recibo final exatos | BLOCKED |
| RT-02 | source/release/deployment/development e audiência apresentam drift | escolher uma matriz de autoridade e atualizar consumidores em uma mudança revisada | BLOCKED |
| RT-03 | ownership/SBOM `87/87` `unclassified`/`NOASSERTION` | classificar cada item, ou declarar exceção aprovada com expiry | BLOCKED |
| RT-04 | mirror de pacote único como redundância | definir segunda fonte e teste de igualdade, ou registrar decisão de risco | BLOCKED |
| RT-05 | release histórica e estado atual divergem em alguns documentos | reauditar contra a ref exata; não apagar a fotografia histórica | INFERENCE |

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
