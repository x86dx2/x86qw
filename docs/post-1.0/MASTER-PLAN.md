# Master plan pós-1.0

MAIN: GREEN
TUF: HEALTHY
1.0.0 OWNER-ONLY: AT-RISK
EXTERNAL-PUBLIC: NO-GO
FEATURE WORK: BLOCKED

**Veredito atual:** MAIN `GREEN`; TUF técnico `HEALTHY`, mas custódia e
recuperação de produção ausentes; `1.0.0 owner-only` `AT-RISK` na fotografia;
`external-public` `NO-GO`; feature work `BLOCKED`.

## Decisão de impacto da 1.0.0

**PROPOSAL:** não criar `1.0.1` para mudança exclusivamente de teste,
documentação ou projeção. A falha Windows/Python 3.10 está classificada como
`INFERENCE test-only` de alta confiança, mas o Checker ainda deve fechar os
dois branches temporais com relógio controlado e matriz Windows 3.10/3.13.
Se a correção exigir mudança em runtime, pacote ou bytes, criar candidato
`1.0.1-owner-only` com novo digest, evidência e observação; não reutilizar a
aceitação RC1.

## Objetivo

Transformar a publicação owner-only em uma linha de release verdadeira,
observável e governável antes de reabrir trabalho de produto ou audiência
externa. O plano trata documentação, evidência, gates e decisões. Não cria
issues remotas, não publica metadata, não muda workflows e não altera bytes de
produto.

## Invariantes

- `origin/main` continua a única autoridade de integração;
- o candidato é build-once: o instalador e `candidate.json` publicados não
  podem ser reconstruídos silenciosamente;
- release, audiência, suporte e validação são dimensões separadas;
- `E3` significa candidato exato executado no M3; não representa outras
  plataformas;
- `E4` só pode ser declarado com rebuild independente comprovado;
- qualquer dúvida de segurança, trust, privacidade ou bytes falha fechada;
- backlog e RFCs desta entrega são propostas locais e documentais.

## Gates ordenados

| Gate | Entrada | Saída exigida | Estado |
| --- | --- | --- | --- |
| 0A — main verde | run `31888249914` | Windows Python 3.10/3.13 e matriz portátil verdes, relógio/protocolo determinísticos | PASS |
| 0B — TUF sustentável | TUF v2/v18 saudável | custódia, renovação, recuperação e SLO observados em produção | BLOCKED |
| 0C — release truth/audience | hashes E2/E3 e receipt | autoridades reconciliadas, receipt aponta evidência final, audiência explícita | BLOCKED |
| 0D — backlog/governance | registers e Maker/Checker | issues locais completas, decisão e rollback registrados | PROPOSAL |
| 0E — owner-only observation | publicação final | período de observação do mantenedor sem regressão e com recibos | PROPOSAL |
| EP-0 — decisão de abertura | 0A–0E | mantenedor registra `external-public` ou mantém owner-only | BLOCKED |
| EP-1 — migração exata | decisão external | fixtures reais `0.7.0–0.7.13`, rollback e preservação verificados | BLOCKED |
| EP-2 — soak exato | EP-1 | sete dias consecutivos, hardware e referências diárias | BLOCKED |
| EP-3 — TUF/recovery | EP-2 | custódia independente, drill e lease sustentável | BLOCKED |
| EP-4 — usuário externo | EP-3 | aceitação pública do candidato exato por usuário externo | BLOCKED |
| EP-5 — decisão de plataforma | EP-4 | suporte por plataforma só com evidência nativa correspondente | BLOCKED |
| external-public | EP-0–EP-5 | declaração e promoção separadas, com receipt coerente | BLOCKED |

Após os gates de disponibilidade, a sequência de produto é deliberadamente
estreita: `1.1 diagnostics/discovery/profiles`, `1.2 hosting/ops`, `1.3
ecosystem`, e `2.0` somente após ADR e evidência de uso. Nenhuma dessas fases
desbloqueia feature work enquanto 0A–0E permanecerem abertas.

## Trilha de execução

1. Reconstruir a verdade a partir da [baseline](AUDIT-BASELINE.md), do
   [registro de release](RELEASE-TRUTH.md) e do [health de CI](CI-HEALTH.md).
2. Fechar 0A com uma mudança mínima de teste/protocolo, se a investigação
   confirmar a inferência; não abrir `1.0.1` por documentação/teste apenas.
3. Fechar 0B com custódia e recovery aprovados, sem colocar chaves ou secrets
   no repositório.
4. Fechar 0C–0D com um único conjunto de autoridades e backlog local completo.
5. Observar owner-only em 0E; só então pedir a decisão EP-0.
6. Se EP-0 for external, executar EP-1→EP-5 na ordem. Se permanecer
   owner-only, estacionar os gates externos e manter `NO-GO` para qualquer claim
   público.

## Critério de parada

Parar e devolver para decisão humana quando houver mudança de produto,
workflow, dependência, chave, contrato de plataforma, audiência ou endpoint.
Parar também quando uma evidência contradisser bytes ou autoridade sem um
responsável definido. O plano não converte `INFERENCE` em `VERIFIED FACT` por
repetição documental.

## Disposição das issues históricas

Nenhuma mutação remota foi executada por esta frente.

- `#143`: tratar como soak histórico/superseded e abrir soak do candidato
  external-public exato;
- `#146`: retarget para migração real `0.7.13` → candidato exato;
- `#148`: manter como operação estrutural, com custódia, owner, backup, SLO
  e RTO;
- `#150`: executar somente preservando as referências de evidência;
- `#151`: retarget para uma decisão futura de hosting;
- `#152`: manter como incidente/SLO e acrescentar transição de recuperação.

## Itens bloqueados e próxima ação

**BLOCKED:** main verde; matriz Windows; custódia/recovery TUF de produção;
receipt final ligado à aceitação final; ownership/SBOM; drift de audiência;
observação owner-only; migração/soak/aceitação externa; plataformas sem E3;
contrato QWLeague; revisão humana independente.

**Próxima ação recomendada:** fechar Gate 0A com relógio controlado e
protocolo de processo explícito, executar Windows 3.10/3.13 e a matriz sem
usar rerun como prova; em paralelo somente como operação crítica, confirmar a
cadeia TUF por segundo vantage e registrar a lease. Nenhuma feature começa.

## Artefatos deste pacote

O índice completo está no [ROADMAP](../ROADMAP.md). Os registers machine-readable
são [release-truth.json](release-truth.json), [backlog.json](backlog.json) e
[risk-register.json](risk-register.json). A operação de qualidade está em
[GAUNTLET-OPERATING-MODEL.md](GAUNTLET-OPERATING-MODEL.md).
