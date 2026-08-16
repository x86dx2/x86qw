# Release train pós-1.0 — M3-first

## Ordem canônica

| Estação | Conteúdo | Gate de saída | Estado |
| --- | --- | --- | --- |
| S0.1 | main e contrato DNS determinístico | sete contextos protegidos verdes | PASS |
| S0.2 | candidato exato em instalação single-user M3 | install/lifecycle/verify/repair/cleanup/uninstall | PASS |
| S0.3 | TUF técnico e projeção live | timestamp saudável; bootstraps/product/release-truth convergentes | IN_PROGRESS |
| S0.4 | observação owner-only | incidentes e recibos do mantenedor | RUN IN PARALLEL |
| F1 | doctor e diagnostics | contratos JSON, sanitização e testes M3 | NEXT |
| F2 | perfis, favoritos e descoberta local | dados pessoais preservados e fallback | NEXT |
| F3 | hosting presets e health | readiness, logs e rollback | LATER |
| EP-0 | decisão de audiência | ADR external-public explícito | DEFERRED |
| EP-1 | migração histórica | somente se upgrade antigo for prometido | CONDITIONAL |
| EP-2 | soak do candidato exato | sete dias completos | DEFERRED |
| EP-3 | TUF com custódia independente | RTO e recovery de produção | EXTERNAL_ONLY |
| EP-4 | aceite por usuário externo | máquina limpa e suporte | DEFERRED |
| EP-5 | plataformas adicionais | evidência nativa por plataforma | DEFERRED |

## Regra de avanço

S0 é o único gate que bloqueia funcionalidades. Depois de S0, F1, F2 e F3 avançam em PRs pequenas no M3. EP-0 a EP-5 não são pré-requisitos para trabalhar no produto owner-only; só reabrem quando a audiência mudar.

Qualquer mudança em bytes de produto cria candidato e repete S0. Mudança apenas em teste, documentação ou projeção não cria release artificial. Metadata TUF permanece última quando houver publicação autorizada.

## Estado da carga

A carga é `x86qw-installer-1.0.0`, commit de produto `e12ed081...`, installer SHA-256 `d3274e...`, candidate `0bde05...`, mirror E2 e evidência nativa M3 E3 25/25. Ela é válida para o mantenedor e não é external-public.

## Migração e baseline nova

A primeira publicação aberta pode declarar uma baseline nova e começar com instalação limpa. Nesse caso EP-1 não existe para a primeira abertura. Se o produto prometer upgrade de 0.7.13 ou outra baseline, EP-1 volta ao trem antes da promoção.

## Handoff

Maker registra estado, commit, digest, ambiente e rollback. Checker tenta refutar. O mantenedor decide avanço, hold ou rollback. Nenhum handoff contém secrets, cookies ou claims sem evidência.

## Referências

- [master plan](MASTER-PLAN.md)
- [issue backlog](ISSUE-BACKLOG.md)
- [dependency graph](DEPENDENCY-GRAPH.md)
- [release truth](RELEASE-TRUTH.md)
