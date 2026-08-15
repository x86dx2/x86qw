# Risk register

Riscos são consolidados a partir da baseline; score é qualitativo
`probabilidade × impacto`. Um risco `high` sem exceção escrita mantém o gate
fechado.

| ID | Risco | P | I | Score | Estado | Gate | Mitigação |
| --- | --- | ---: | ---: | --- | --- | --- | --- |
| R-001 | CI vermelho mascara regressão no contrato Windows | alta | alta | high | MITIGATED | 0A | protocolo determinístico, matriz verde e orçamento de flake zero |
| R-002 | receipt/autoridade errada promove ou comunica bytes incorretos | média | crítica | high | BLOCKED | 0C | reconciliação candidate/receipt/evidência final |
| R-003 | lease TUF expira sem renovação ou recovery | média | crítica | high | BLOCKED | 0B | alerta 6 h, custódia independente, drill |
| R-004 | reachability transitória interrompe update público | média | alta | high | AT-RISK | 0B | observação independente, freeze e mirror |
| R-005 | SBOM/ownership sem classificação impede atribuição de responsabilidade | alta | alta | high | BLOCKED | 0C | classificar 87 itens ou aprovar exceções |
| R-006 | cleanup deixa dados pessoais fora da promessa ou remove demais | média | alta | high | BLOCKED | EP-1 | contrato explícito, fixture real e rollback |
| R-007 | self-review permite erro não detectado em release/trust | média | crítica | high | BLOCKED | 0D/0B | Checker e custodian independentes |
| R-008 | mirror único vira ponto único de falha | média | alta | high | BLOCKED | 0C/EP-3 | redundância ou risco aceito pelo mantenedor |
| R-009 | claim de suporte excede evidência M3 | alta | média | high | BLOCKED | EP-5 | matriz `preview`/`conditional` por prova |
| R-010 | migração ou soak externo falha depois da abertura | média | crítica | high | BLOCKED | EP-1/EP-2 | executar antes do EP-0 final |
| R-011 | integração QWLeague usa protocolo não autorizado | baixa | alta | medium | BLOCKED_EXTERNAL | 1.3 | read-only discovery e contrato oficial |
| R-012 | drift de audiência faz feature work avançar no modo errado | média | alta | high | BLOCKED | 0D/EP-0 | decisão explícita e gate automático |

## Política de tratamento

- **Aceitar:** somente o mantenedor, com prazo e compensação escritos;
- **Mitigar:** item de backlog com Maker, Checker, teste e rollback;
- **Transferir:** custódia ou plataforma com contrato assinado;
- **Evitar:** não publicar, não abrir audiência ou não iniciar feature;
- **Encerrar:** apenas com evidência que satisfaça o acceptance do item.

Os dados máquina-legíveis estão em [risk-register.json](risk-register.json).
Esse arquivo não substitui secrets, chaves, logs protegidos ou a autoridade
TUF.
