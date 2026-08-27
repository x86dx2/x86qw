# Métricas e critérios de observação

As métricas abaixo medem gates, não vaidade de produto. Cada valor precisa de
timestamp UTC, commit, run/artefacto, origem e Checker. Ausência de medição é
`BLOCKED`, não zero.

## Snapshot atual — 2026-08-27T23:18:37Z

| Métrica | Valor na auditoria | Nível | Próximo limiar |
| --- | --- | --- | --- |
| main CI | `GREEN`; Validate `33124929611`, 5/5 jobs exigidos | E2 | manter zero vermelhos em 0A |
| Windows portable-contract | Python 3.10 e 3.13 verdes | E2 | manter matriz protegida |
| installer mirror equality | `E2`, GitHub/GitLab iguais | E2 | manter e adicionar redundância |
| exact native candidate | `25/25` Apple M3 | E3 | não extrapolar para não-M3 |
| independent rebuild | não executado | E0 | E4 explícito ou exceção |
| TUF root | v1 | E2 | rotação/recovery observável |
| TUF timestamp/snapshot/targets | v28/v27/v27 | E2 | lease sem alerta crítico |
| timestamp expiry | `2026-09-03T19:15:09Z` | E2 | alerta 6 h e renovação comprovada |
| SBOM/ownership classified | `0/87` classificados; `87/87` unclassified/NOASSERTION | E1 | 87/87 classificados ou exceções |
| package mirrors | GitHub/GitLab byte-equal para o instalador | E2 | manter verificação após cada projeção |
| QWLeague contract | nenhum API/OAuth/webhook verificado | E1 | contrato oficial |
| site headers/CWV | não medidos | E0 | medição independente antes de claim |

## Métricas de gate

### 0A — CI

`ci_red_jobs = 0`, com matriz Windows 3.10/3.13 explícita. Registrar duração
do fake por relógio virtual; não usar sleeps reais como prova de timeout.

### 0B — TUF

Registrar sucessos/falhas de fetch autenticado, horas até expiry, divergência
de metadata, tempo até alerta e tempo até recovery. Uma consulta saudável não
substitui a janela de operação.

### 0C — release truth

Medir igualdade de tamanho/digest entre cada mirror, referências do receipt ao
candidato final, completude de SBOM/ownership e presença de rebuild E4. O
resultado é booleano apenas quando todos os campos têm fonte.

### EP-1/EP-2 — externo

Contar versões de fixture reais migradas, preservação/rollback, dias
consecutivos de soak e referências HTTPS observadas. Qualquer lacuna reinicia
o contador de sete dias.

### EP-5 — plataforma

Contar plataformas com E3 do candidato exato. Até então, a matriz pública deve
mostrar `preview`/`conditional`; não inventar sucesso para o restante.

## O que não medir nesta entrega

Não executar teste nativo adicional, monitor público, browser, carga, deploy,
alteração TUF ou integração QWLeague. Este pacote não possui evidência de
headers de site, CWV, OAuth ou webhook.

## Formato mínimo de observação

```text
metric=<name>
observed_at=<UTC>
commit=<SHA>
run=<run-or-local>
artifact=<id-or-none>
value=<value>
evidence_level=E0|E1|E2|E3|E4
checker=<role>
```

Ver [risk-register.json](risk-register.json) e [release-truth.json](release-truth.json)
para os valores machine-readable.
