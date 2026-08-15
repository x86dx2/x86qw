# Decision log

| ID | Decisão | Estado | Evidência/autoridade |
| --- | --- | --- | --- |
| D-01 | `1.0.0` existe como release `owner-only` | decidido | release page, receipt, PROJECT-STATUS |
| D-02 | `external-public` não está autorizado | decidido | release audience contract |
| D-03 | main vermelha bloqueia feature work | decidido | Validate `31853649373` |
| D-04 | falha DNS é test-only como hipótese de alta confiança | provisório | reprodução determinística; matriz Windows pendente |
| D-05 | lease TUF estava tecnicamente `HEALTHY` no instante observado | observado | probe autenticado `2026-08-15T01:45:39Z` |
| D-06 | custódia/recovery TUF de produção são gaps | aberto | #148/#152 e auditoria |
| D-07 | aceitação final no M3 é E3 `25/25` | decidido | release-evidence do candidato exato |
| D-08 | igualdade GitHub/GitLab é E2, não E4 | decidido | comparação de bytes; sem rebuild independente |
| D-09 | receipt final não deve reutilizar aceitação RC1 | aberto | `release-receipt.json` versus evidência final |
| D-10 | `cleanup --personal-data` precisa decidir cobertura de `qw/demos` | aberto | `maintenance/.../manager.py:1513-1522` |
| D-11 | QWLeague permanece `BLOCKED_EXTERNAL` | decidido | sem contrato/autorização oficial verificados |
| D-12 | nenhuma capacidade 1.1 começa antes de 0A–0E | decidido | Master Plan e stop conditions |
| D-13 | issue #164 precede a branch documental | concluído | política CONTRIBUTING/ROADMAP |
| D-14 | esta frente altera apenas documentos aprovados | concluído | issue #164 e allowlist |

## Decisões humanas pendentes

- nomear owner e backup de TUF, custódia e RTO;
- decidir o comportamento público de GitHub Latest para `owner-only`;
- decidir a semântica final de cleanup/purge de dados pessoais;
- revisar e aceitar exceções de SBOM/ownership e mirror;
- aprovar ou manter `external-public=NO-GO` após 0A–0E;
- escolher plataformas da primeira audiência externa;
- decidir se qualquer correção de cleanup altera bytes e exige `1.0.1`;
- fornecer contrato e autorização oficial antes de qualquer adapter QWLeague.
