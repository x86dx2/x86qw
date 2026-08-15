# Gap register

Registro consolidado da fotografia de auditoria. `P1` indica impacto de
release/governança; `P2` indica risco controlável que ainda impede um claim
mais amplo. Nenhuma linha é uma issue remota criada por esta entrega.

| ID | Prioridade | Gap | Estado | Evidência | Gate | Próxima prova |
| --- | --- | --- | --- | --- | --- | --- |
| G-001 | P1 | contrato Windows 3.10 vermelho; fake/protocolo e 10 ms não determinísticos | BLOCKED / INFERENCE | run `31853649373` | 0A | reproduzir com clock controlado e matriz 3.10/3.13 |
| G-002 | P1 | source/release/deployment/development e audiência em drift | BLOCKED | auditoria | 0C | matriz de autoridade reconciliada |
| G-003 | P1 | receipt final aponta public acceptance para RC1 | BLOCKED | receipt versus evidência final | 0C | receipt final coerente com tag/candidato |
| G-004 | P1 | ownership/SBOM `87/87` `unclassified`/`NOASSERTION` | BLOCKED | relatório de release | 0C | classificação ou exceção aprovada por item |
| G-005 | P1 | cleanup personal-data não cobre `qw`/demos prometidos | BLOCKED | promessa versus cobertura observada | 0C/EP-1 | escopo, teste, docs e rollback coerentes |
| G-006 | P1 | single maintainer/self-review | BLOCKED | auditoria | 0D/0B | Checker independente e custodian separado |
| G-007 | P1 | custódia e recovery TUF de produção ausentes | BLOCKED | auditoria | 0B/EP-3 | cerimônia e drill reprodutível |
| G-008 | P1 | lease TUF com alerta de 6 h e reachability transitória | AT-RISK | root v2/roles v18 | 0B | SLO, alertas e incidente fechado |
| G-009 | P2 | mirror de pacote único | BLOCKED | auditoria | 0C/EP-3 | segunda fonte ou exceção de risco |
| G-010 | P1 | plataformas não-M3 apenas preview; native não demonstrado | BLOCKED | E3 M3 25/25 | EP-5 | evidência nativa por plataforma |
| G-011 | P1 | external-public sem migração e soak | BLOCKED | gates condicionais | EP-1/EP-2 | fixtures reais e sete dias verdes |
| G-012 | P1 | QWLeague sem API/OAuth/webhook oficial verificado | BLOCKED_EXTERNAL | home/sitemap apenas | 1.3 | contrato escrito e autorização |
| G-013 | P2 | controles de segurança GitHub precisam de confirmação independente | BLOCKED | estado registrado na auditoria | 0D | evidência datada de settings e revisão |
| G-014 | P2 | feature work bloqueado até fechar disponibilidade | BLOCKED | decisão da auditoria | 0A–EP-0 | gate de desbloqueio assinado |

## Ordem de tratamento

`G-001` desbloqueia 0A; `G-002`–`G-006` formam 0C/0D; `G-007`–`G-009`
formam 0B; `G-010` e `G-011` formam EP-1–EP-5; `G-012` pertence a 1.3 e não
deve ser usado para justificar a abertura externa; `G-013` e `G-014` são
transversais. Cada gap deve apontar para um item de
[ISSUE-BACKLOG](ISSUE-BACKLOG.md), uma decisão e uma evidência.

## Limite desta entrega

Não há alteração de código, workflow, chave, endpoint, release ou issue
remota. O registro é o contrato de trabalho para uma futura execução
autorizada.
