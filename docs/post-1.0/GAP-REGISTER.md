# Gap register

Registro consolidado da fotografia corrente. P1 indica impacto de
release/governança; P2 indica risco controlável que ainda impede um claim
mais amplo. Nenhuma linha é uma issue remota criada por esta entrega.

| ID | Prioridade | Gap | Estado | Evidência | Gate | Próxima prova |
| --- | --- | --- | --- | --- | --- | --- |
| G-001 | P1 | contrato Windows 3.10 vermelho; fake/protocolo e 10 ms não determinísticos | RESOLVED — Gate 0A PASS | Validate 31891985767, SHA fdd5a726… | 0A | manter zero-flake |
| G-002 | P1 | source/release/deployment/development e audiência em drift | BLOCKED | product/catalog/site live | 0C | matriz reconciliada e deploy verificado |
| G-003 | P1 | receipt final aponta public acceptance para RC1 | BLOCKED | release-receipt final | 0C | receipt coerente com tag/candidato |
| G-004 | P1 | ownership/SBOM 87/87 unclassified/NOASSERTION | BLOCKED | relatório de release | 0C | classificação ou exceção aprovada |
| G-005 | P1 | cleanup personal-data não cobre qw/demos prometidos | BLOCKED | promessa versus cobertura | 0C/EP-1 | escopo, teste, docs e rollback |
| G-006 | P1 | single maintainer/self-review | BLOCKED | auditoria | 0D/0B | Checker e custodian separado |
| G-007 | P1 | custódia e recovery TUF de produção ausentes | BLOCKED | #148/#152; drill local somente | 0B/EP-3 | cerimônia e drill reprodutível |
| G-008 | P0_OPERATIONAL | lease TUF autenticada dentro do alerta de 6 h | AT-RISK | monitor 6h falha; expiry 21:09Z | 0B | renovação autorizada ou retirar claim |
| G-009 | P2 | convergência verificada só para installer GitHub/GitLab | BLOCKED | comparação E2 | 0C/EP-3 | todas as superfícies/mirrors |
| G-010 | P1 | plataformas não-M3 apenas preview; native não demonstrado | BLOCKED | E3 M3 25/25 | EP-5 | evidência nativa por plataforma |
| G-011 | P1 | external-public sem soak/aceitação | BLOCKED | gates condicionais | EP-1/EP-2 | candidato exato e sete dias |
| G-012 | P1 | QWLeague sem API/OAuth/webhook oficial verificado | BLOCKED_EXTERNAL | discovery público | 1.3 | contrato escrito e autorização |
| G-013 | P2 | controles de segurança GitHub precisam confirmação independente | BLOCKED | estado anônimo | 0D | evidência autenticada |
| G-014 | P1 | feature work bloqueado enquanto 0B/0C estão abertos | BLOCKED | stop conditions | 0A–EP-0 | gate assinado |
| G-015 | P1 | migração do instalador público 0.7.13 exato bloqueada por versão histórica não autenticada | BLOCKED | installer SHA 11460440…; CLI 1.0.0; harness fixture injeta installation_version | EP-1 | corrigir contrato/receipt ou aceitar decisão de produto |
| G-016 | P1 | release-truth source/projection live ausente e site root mantém 0.7.13 | BLOCKED | /api/v1/release-truth.json 404; live hero | 0C | deploy verificável ou manter claim claramente owner-only |

## Ordem de tratamento

G-001 fechou 0A. G-008 e G-007 têm precedência operacional imediata; G-002,
G-003, G-004 e G-016 formam 0C/0D. G-015 é pré-condição de EP-1; G-010 e G-011
formam EP-1–EP-5. G-012 pertence a 1.3 e permanece BLOCKED_EXTERNAL. Nenhum
gap autoriza feature work enquanto os gates de parada estiverem abertos.

## Limite desta entrega

Não há alteração de código, workflow, chave, endpoint, release ou issue
remota. O registro é documentação vinculada à #164; os hashes e observações são
datados para impedir que uma fotografia seja apresentada como estado permanente.
