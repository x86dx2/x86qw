# Decision log

| ID | Decisão | Estado | Evidência/autoridade |
| --- | --- | --- | --- |
| D-01 | 1.0.0 existe como release owner-only | decidido | release page, receipt, PROJECT-STATUS |
| D-02 | external-public não está autorizado | decidido | release audience contract |
| D-03 | main verde é pré-condição; Gate 0A está fechado | decidido | Validate 33124929611, SHA adf83f9…, 5/5 jobs exigidos |
| D-04 | falha DNS foi test-only; não há impacto de bytes/runtime observado | decidido | testes determinísticos, 87 testes, 30.000 repetições, matriz verde |
| D-05 | lease TUF autentica tecnicamente; monitor 6 h saudável na política owner-only de 7 dias | decidido | timestamp v28 expira 2026-09-03T19:15:09Z; monitor atual healthy |
| D-06 | custódia/recovery TUF de produção são gaps external-public | deferred | drill protegido registrado; custódia independente/RTO ainda não provados |
| D-07 | aceitação final no M3 é E3 25/25 | decidido | release-evidence do candidato exato |
| D-08 | igualdade GitHub/GitLab é E2, não E4 | decidido | comparação de bytes; sem rebuild independente |
| D-09 | receipt final não deve reutilizar aceitação RC1 | accepted_owner_only | receipt histórico separado da evidência final; reabrir somente para external-public |
| D-10 | cleanup --personal-data precisa decidir cobertura de qw/demos | deferred | contrato versus cobertura observada; reabrir somente se upgrade histórico for prometido |
| D-11 | QWLeague permanece BLOCKED_EXTERNAL | decidido | sem contrato/autorização oficial verificados |
| D-12 | nenhuma capacidade 1.1 começa antes de 0A–0E | decidido | Master Plan e stop conditions |
| D-13 | issue #164 precede a branch documental | concluído | política CONTRIBUTING/ROADMAP |
| D-14 | esta frente altera apenas documentos aprovados; não altera produto/publicação | concluído | issue #164 e allowlist documental |
| D-15 | source release-truth foi mesclada; deployment live converge no candidato | concluído | receipt 33125534974; root 200 owner-only; TUF público v28/v27 |
| D-16 | Gate 0A passou; Gate 0B continua external-only; 0C owner-only convergiu | decidido | main verde; receipt 33125534974; monitor TUF saudável |
| D-17 | migração pública 0.7.13 exata não é aprovada por inferência do harness | decidido | instalador SHA 11460440… bloqueado; harness injeta metadado histórico |
| D-18 | timestamp dentro da warning window bloqueia feature work | decidido | stop condition; monitor 6h em 2026-08-15T15:38:22Z |
| D-19 | fonte e deployment não podem responder versões/audiência incompatíveis | resolvido | product/catalog/site/release-truth convergentes; receipt 33125534974 |
| D-20 | timestamp owner-only expira no máximo em 7 dias | decidido | emenda ADR 0006; teto 24 h volta antes de catálogo público |

## Decisões humanas pendentes

- nomear owner e backup independentes de TUF, custódia e RTO antes de
  `external-public`;
- decidir uma audiência externa e as plataformas que ela abrangerá;
- resolver o contrato de migração do instalador 0.7.13 sem fixture privilegiado
  se um upgrade histórico for prometido;
- decidir a semântica final de cleanup/purge para instalações históricas;
- revisar e classificar exceções de SBOM/ownership antes de redistribuição ampla;
- fornecer contrato e autorização oficial antes de qualquer adapter QWLeague;
- manter `external-public=NO-GO` até EP-0–EP-5 possuírem evidência protegida.
