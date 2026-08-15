# Readiness para `external-public`

**Veredito:** `NO-GO`.

O produto só pode aceitar usuários externos depois que os gates de
disponibilidade, release truth, migração, soak, trust/recovery, aceitação e
plataforma forem fechados. A existência da tag `x86qw-installer-1.0.0` e do
smoke M3 não remove esses pré-requisitos.

## Checklist de entrada

| Gate | Exigência | Evidência atual | Estado |
| --- | --- | --- | --- |
| 0A | main verde e matriz Windows 3.10/3.13 | `31853649373` vermelho em Windows/Python 3.10 | BLOCKED |
| 0B | TUF sustentável, custódia e recovery | TUF v2/v18 saudável; custódia/recovery ausentes | BLOCKED |
| 0C | receipt, candidate, audiência e digests coerentes | `public_acceptance` aponta RC1; evidência final separada | BLOCKED |
| 0D | backlog, Maker/Checker e rollback completos | este pacote propõe os registros; aprovação pendente | PROPOSAL |
| 0E | observação owner-only | não há período fechado neste snapshot | BLOCKED |
| EP-1 | migração real `0.7.0–0.7.13` | capacidade preservada, execução externa não demonstrada | BLOCKED |
| EP-2 | soak de sete dias | não iniciado para audiência externa | BLOCKED |
| EP-3 | operação/recovery TUF de produção | falta custódia e recovery | BLOCKED |
| EP-4 | usuário externo | não autorizado/executado | BLOCKED |
| EP-5 | decisão por plataforma | só E3 M3 do candidato | BLOCKED |

## Critérios de promoção

Uma promoção só é elegível quando cada linha acima tem um receipt verificável,
Checker independente, referência do commit e dos bytes, e nenhum risco
`high` sem exceção aprovada. O candidato exato deve permanecer imutável entre
EP-1 e EP-5; qualquer alteração exige voltar ao início da train.

## Migração

Usar apenas versões efetivamente publicadas (`0.7.0–0.7.13`). A migração deve
preservar arquivos pessoais, produzir estado e receipt compatíveis, evitar
versões inventadas e permitir rollback diagnosticável. `owner-only` não exige
que ela seja executada agora; `external-public` exige.

## Soak

O soak deve ser consecutivo por sete dias, com o candidato exato, hardware
identificado, referência HTTPS e observação de TUF por dia. Uma lacuna,
expiração, divergência de bytes ou incidente não classificado reinicia a
contagem. Não há soak concluído para declarar.

## TUF e recuperação

O intervalo auditado mostrou root v2 e timestamp/snapshot/targets v18 saudáveis,
mas isso é um ponto no tempo. Para readiness externa, registrar custódia
independente, rotação, recovery offline/online, limites de expiração, alertas e
responsável substituto. Ver [TUF-SLO-AND-RECOVERY](TUF-SLO-AND-RECOVERY.md).

O Gate 0B fecha antes de 0C com os receipts de SLO, custódia e recovery
(POST-011, POST-009 e POST-010). O EP-3, depois do soak EP-2, revalida esses
receipts contra o candidato external-public exato e pode exigir um novo drill;
não é uma autorização para deixar 0B aberto até depois do soak.

## Plataformas

O candidato exato tem E3 `25/25` no Apple M3. Isso não promove Linux, Windows,
macOS Intel, nightly ou o bundle stable original a `supported`; eles ficam
`preview`/`conditional` até a evidência correspondente. Nenhuma execução
nativa não-M3 deve ser insinuada neste pacote.

## Evidência que falta

- receipt final que aponte para a aceitação final, não RC1;
- classificação dos 87 itens de ownership/SBOM;
- matriz de source/release/deployment/development e audiência sem drift;
- segundo mirror ou exceção de risco explícita;
- evidência dos controles de segurança do GitHub;
- correção/documentação da promessa `cleanup --personal-data` para `qw`/demos;
- contrato oficial QWLeague, caso essa integração seja priorizada.
