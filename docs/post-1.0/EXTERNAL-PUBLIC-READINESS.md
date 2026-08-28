# Readiness para external-public

**Veredito:** NO-GO.

Snapshot vivo: `2026-08-28T02:32:21Z`; `MAIN=GREEN`, `TUF=HEALTHY` e
`1.0.0 owner-only=VALID_FOR_SINGLE_USER_M3`. A projeção pública convergente
foi verificada no run `33136179763`; o Validate da linha `main` passou no run
`33135951867`. Esses fatos fecham o escopo owner-only, mas não substituem os
gates condicionais de abertura para terceiros.

A tag x86qw-installer-1.0.0 e o smoke M3 não removem os pré-requisitos de
disponibilidade, release truth, migração, soak, trust/recovery, aceitação e
plataforma.

## Checklist de entrada

| Gate | Exigência | Evidência atual | Estado |
| --- | --- | --- | --- |
| 0A | main verde e matriz Windows 3.10/3.13 | Validate 33135951867, 5/5 jobs exigidos | PASS |
| 0B | TUF sustentável, custódia e recovery | root v1, timestamp v30, snapshot/targets v29; custódia/RTO independentes ausentes | BLOCKED_EXTERNAL |
| 0C | receipt, candidate, audiência e digests coerentes | projeção 33136179763; site/product/catalog/release-truth convergentes | PASS_OWNER_ONLY |
| 0D | backlog, Maker/Checker e rollback completos | documentação vinculada à #164; limites externos registrados | PASS_OWNER_ONLY |
| 0E | observação owner-only | aceitação single-user e lifecycle completo no M3 | PASS_OWNER_ONLY |
| EP-1 | migração real da baseline publicada | instalador 0.7.13 exato não autentica o estado histórico; somente condicional se upgrade for prometido | CONDITIONAL |
| EP-2 | soak consecutivo de sete dias | não iniciado para audiência externa | BLOCKED |
| EP-3 | operação/recovery TUF de produção | falta custódia e recovery | BLOCKED |
| EP-4 | usuário externo | não autorizado/executado | BLOCKED |
| EP-5 | decisão de plataforma | só E3 M3 do candidato | BLOCKED |

## Migração real

O baseline público usado no EP-1 deve ser o instalador efetivamente publicado
0.7.13 (SHA-256 114604400e1fd18c4180624314d4bc8ca9b6d4559ed26cfe8d0a767287f2aa32).
Nesta auditoria, esse instalador foi instalado em destino descartável e o CLI
final 1.0.0 executou migrate; o comando terminou bloqueado porque o estado
histórico não tinha uma versão autenticada.

A evidência nativa assinada declara o caso migration-0.7.13-real como
passado, porém o código público do harness escreve installation_version no
fixture antes da chamada de migração (maintenance/native_case_entrypoint.py).
Isso prova o contrato do fixture, não o caminho de um usuário que só possui o
instalador 0.7.13 publicado. O EP-1 deve resolver essa diferença com uma
decisão/reprodução independente, sem fabricar estado autenticado.

## Critérios de promoção

Uma promoção só é elegível quando cada linha acima tem receipt verificável,
Checker independente, referência do commit e dos bytes, e nenhum risco high sem
exceção aprovada. O candidato exato deve permanecer imutável entre EP-1 e EP-5;
qualquer alteração reinicia a train.

QWLeague permanece BLOCKED_EXTERNAL: nenhum adapter, autenticação, scraping,
cookie, deep link ou alegação de parceria é permitido sem contrato e autorização
oficiais.
