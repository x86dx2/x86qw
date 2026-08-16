# Decision log

| ID | Decisão | Estado | Evidência/autoridade |
| --- | --- | --- | --- |
| D-01 | 1.0.0 existe como release owner-only | decidido | release page, receipt, PROJECT-STATUS |
| D-02 | external-public não está autorizado | decidido | release audience contract |
| D-03 | main verde é pré-condição; Gate 0A está fechado | decidido | Validate 31891985767, SHA fdd5a726…, 7/7 contexts |
| D-04 | falha DNS foi test-only; não há impacto de bytes/runtime observado | decidido | testes determinísticos, 87 testes, 30.000 repetições, matriz verde |
| D-05 | lease TUF autentica tecnicamente, mas está em WARNING operacional | aberto | monitor 6h falha por limiar; monitor 1h saudável; expiry 2026-08-15T21:09:01Z |
| D-06 | custódia/recovery TUF de produção são gaps | aberto | #148/#152, drill local apenas |
| D-07 | aceitação final no M3 é E3 25/25 | decidido | release-evidence do candidato exato |
| D-08 | igualdade GitHub/GitLab é E2, não E4 | decidido | comparação de bytes; sem rebuild independente |
| D-09 | receipt final não deve reutilizar aceitação RC1 | aberto | release-receipt.json versus evidência final |
| D-10 | cleanup --personal-data precisa decidir cobertura de qw/demos | aberto | contrato versus cobertura observada |
| D-11 | QWLeague permanece BLOCKED_EXTERNAL | decidido | sem contrato/autorização oficial verificados |
| D-12 | nenhuma capacidade 1.1 começa antes de 0A–0E | decidido | Master Plan e stop conditions |
| D-13 | issue #164 precede a branch documental | concluído | política CONTRIBUTING/ROADMAP |
| D-14 | esta frente altera apenas documentos aprovados; não altera produto/publicação | concluído | issue #164 e allowlist documental |
| D-15 | source release-truth foi mesclada; deployment live continua em drift | aberto | PR #169; observação live; sem deploy |
| D-16 | Gate 0A passou; Gate 0B e 0C continuam abertos | decidido | main verde; #148/#152; drift live |
| D-17 | migração pública 0.7.13 exata não é aprovada por inferência do harness | decidido | instalador SHA 11460440… bloqueado; harness injeta metadado histórico |
| D-18 | timestamp dentro da warning window bloqueia feature work | decidido | stop condition; monitor 6h em 2026-08-15T15:38:22Z |
| D-19 | fonte e deployment não podem responder versões/audiência incompatíveis | decidido | product/catalog 1.0.0; site root 0.7.13; release-truth endpoint 404 |
| D-20 | timestamp owner-only expira no máximo em 7 dias | decidido | emenda ADR 0006; teto 24 h volta antes de catálogo público |

## Decisões humanas pendentes

- nomear owner e backup de TUF, custódia e RTO;
- decidir renovar a lease agora ou retirar explicitamente o install claim antes
  do expiry;
- decidir o comportamento público de GitHub Latest para owner-only;
- reconciliar o receipt RC1 com a aceitação do candidato final;
- resolver o contrato de migração do instalador 0.7.13 sem fixture privilegiado;
- decidir a semântica final de cleanup/purge de dados pessoais;
- revisar e aceitar exceções de SBOM/ownership e mirror;
- aprovar ou manter external-public=NO-GO após 0A–0E;
- escolher plataformas da primeira audiência externa;
- fornecer contrato e autorização oficial antes de qualquer adapter QWLeague.
