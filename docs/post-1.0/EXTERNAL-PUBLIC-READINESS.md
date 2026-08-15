# Readiness para external-public

**Veredito:** NO-GO.

A tag x86qw-installer-1.0.0 e o smoke M3 não removem os pré-requisitos de
disponibilidade, release truth, migração, soak, trust/recovery, aceitação e
plataforma.

## Checklist de entrada

| Gate | Exigência | Evidência atual | Estado |
| --- | --- | --- | --- |
| 0A | main verde e matriz Windows 3.10/3.13 | Validate 31891985767, fdd5a726…, 7/7 contexts | PASS |
| 0B | TUF sustentável, custódia e recovery | cadeia v2/v18 autenticada, timestamp dentro da janela de 6h; custódia/recovery ausentes | WARNING/BLOCKED |
| 0C | receipt, candidate, audiência e digests coerentes | receipt public_acceptance aponta RC1; live site/product/catalog divergem | BLOCKED |
| 0D | backlog, Maker/Checker e rollback completos | documentação vinculada à #164; decisões humanas pendentes | PROPOSAL |
| 0E | observação owner-only | período fechado não demonstrado | BLOCKED |
| EP-1 | migração real da baseline publicada | instalador 0.7.13 exato bloqueou por versão histórica não autenticada; harness M3 usa fixture com metadado injetado | BLOCKED |
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
