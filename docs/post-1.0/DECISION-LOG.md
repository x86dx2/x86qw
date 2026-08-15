# Decision log

| ID | Decisão | Estado | Evidência/autoridade |
| --- | --- | --- | --- |
| D-01 | `1.0.0` existe como release `owner-only` | decidido | release page, receipt, PROJECT-STATUS |
| D-02 | `external-public` não está autorizado | decidido | release audience contract |
| D-03 | main verde é pré-condição; o vermelho histórico permanece registrado | decidido | Validate `31890091394`, SHA `7d5eb94a` |
| D-04 | falha DNS foi test-only; não há impacto de bytes/runtime observado e não há `1.0.1` por este finding | decidido | testes determinísticos, matriz protegida `31889777779` e main `31890091394` |
| D-05 | lease TUF está tecnicamente `HEALTHY` no instante observado | observado | monitor autenticado `2026-08-15T14:38:25Z`, warning 6h |
| D-06 | custódia/recovery TUF de produção são gaps | aberto | #148/#152 e auditoria |
| D-07 | aceitação final no M3 é E3 `25/25` | decidido | release-evidence do candidato exato |
| D-08 | igualdade GitHub/GitLab é E2, não E4 | decidido | comparação de bytes; sem rebuild independente |
| D-09 | receipt final não deve reutilizar aceitação RC1 | aberto | `release-receipt.json` versus evidência final |
| D-10 | `cleanup --personal-data` precisa decidir cobertura de `qw/demos` | aberto | `maintenance/.../manager.py:1513-1522` |
| D-11 | QWLeague permanece `BLOCKED_EXTERNAL` | decidido | sem contrato/autorização oficial verificados |
| D-12 | nenhuma capacidade 1.1 começa antes de 0A–0E | decidido | Master Plan e stop conditions |
| D-13 | issue #164 precede a branch documental | concluído | política CONTRIBUTING/ROADMAP |
| D-14 | esta frente altera apenas documentos aprovados e comentários de evidência; não altera produto/publicação | concluído | issue #164, PRs #169/#170 e allowlist |
| D-15 | source release-truth foi mesclada; deployment live continua em drift | aberto | PR #169; observação live `2026-08-15T14:43:32Z`; sem deploy |
| D-16 | Gate 0A está PASS; Gate 0B operacional e 0C deployment permanecem abertos | decidido | main Validate `31890091394`, #148, #152 e ledger corrente |

## Decisões humanas pendentes

- nomear owner e backup de TUF, custódia e RTO;
- decidir o comportamento público de GitHub Latest para `owner-only`;
- decidir a semântica final de cleanup/purge de dados pessoais;
- revisar e aceitar exceções de SBOM/ownership e mirror;
- aprovar ou manter `external-public=NO-GO` após 0A–0E;
- escolher plataformas da primeira audiência externa;
- decidir se qualquer correção de cleanup altera bytes e exige `1.0.1`;
- fornecer contrato e autorização oficial antes de qualquer adapter QWLeague.
