# Risk register — M3-first

Riscos owner-only e riscos condicionais de uma futura audiência external-public são separados. Nenhum risco externo vira bloqueio artificial para a funcionalidade no M3.

| ID | Risco | P | I | Score | Estado | Escopo | Mitigação |
| --- | --- | ---: | ---: | --- | --- | --- | --- |
| R-001 | CI vermelho mascara regressão no contrato Windows | alta | alta | high | MITIGATED | owner-only | protocolo determinístico e flake zero |
| R-002 | autoridade comunica bytes ou audiência errados | média | crítica | high | CONTROLLED | owner-only | projeção única, hashes e verificação live; receipt 33136179763 |
| R-003 | lease TUF expira sem renovação | média | crítica | high | CONTROLLED | owner-only | monitor técnico, alerta e renovação antes da janela |
| R-004 | reachability transitória interrompe update | média | alta | high | ACCEPTED_WITH_FALLBACK | owner-only | retry, cache, mirror e diagnóstico |
| R-005 | SBOM/ownership sem classificação | alta | alta | high | DEFERRED | external-public | classificar antes de redistribuição ampla |
| R-006 | cleanup remove ou abandona dados pessoais | média | alta | high | CONTROLLED | owner-only | contrato, dry-run, backup e purge explícito |
| R-007 | self-review falha em release/trust | média | crítica | high | ACCEPTED_OWNER_ONLY | external-public | custodian e reviewer antes da abertura |
| R-008 | mirror único vira ponto de falha | média | alta | high | CONTROLLED | owner-only | fallback e verificação após deploy |
| R-009 | claim de suporte excede evidência M3 | alta | média | high | MITIGATED | owner-only | M3 como único suporte nativo; demais preview |
| R-010 | migração/soak externo falha após abertura | média | crítica | high | DEFERRED | external-public | EP-1/EP-2 somente quando audiência mudar |
| R-011 | QWLeague usa protocolo não autorizado | baixa | alta | medium | BLOCKED_EXTERNAL | opcional | sem scraping, auth ou dependência obrigatória |
| R-012 | processo grande demais congela funcionalidades | alta | alta | high | MITIGATED_BY_S0 | owner-only | S0 curto; gates externos condicionais |
| R-013 | reset destrutivo perde evidência ou dados | baixa | crítica | high | DEFERRED | futura publicação | backup/export/rollback antes de apagar histórico |

## Política de tratamento

- **Aceitar:** risco explícito no escopo owner-only, sem afirmar suporte externo.
- **Mitigar:** issue pequena com Maker, Checker, teste e rollback.
- **Transferir:** somente com custódia ou contrato real.
- **Evitar:** não iniciar integração externa sem autorização.
- **Encerrar:** apenas com evidência correspondente ao acceptance.

Histórico Git não é apagado como parte da estabilização. Uma eventual publicação com baseline nova deve ser planejada como operação destrutiva separada, com cópia verificável das evidências.

Os dados machine-readable estão em [risk-register.json](risk-register.json).
