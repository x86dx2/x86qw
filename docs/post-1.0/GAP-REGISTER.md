# Gap register — M3-first

Registro do que impede estabilidade no único ambiente suportado agora e do que só impede uma futura audiência external-public.

| ID | Prioridade | Gap | Estado | Evidência | Escopo | Próxima prova |
| --- | --- | --- | --- | --- | --- | --- |
| G-001 | P0 | contrato DNS Windows/Python 3.10 dependia de relógio real e fake incompleto | RESOLVED | Gate 0A, 30.000 repetições focais sem falha | owner-only | manter zero-flake |
| G-002 | P1 | projeção live ainda precisa confirmar árvore assembled | RESOLVED | receipt 31965901520; root 200 owner-only | owner-only | manter convergência após cada overlay |
| G-003 | P1 | receipt público histórico referencia RC1 | ACCEPTED_RESIDUAL | receipt M3 final separado e bytes bound ao candidato | external-public | substituir apenas se audiência mudar |
| G-004 | P1 | ownership/SBOM contém itens sem classificação upstream | DEFERRED | relatório de release | external-public/licensing | classificar antes de redistribuição ampla |
| G-005 | P1 | cleanup de dados pessoais não cobre toda promessa histórica | DEFERRED | contrato atual single-user | condicional a upgrade | ampliar somente se migração for prometida |
| G-006 | P1 | single maintainer e self-review | ACCEPTED_OWNER_ONLY | limitação conhecida | external-public | reviewer/custodian antes de abrir |
| G-007 | P1 | custódia independente e RTO de produção TUF | DEFERRED | drill técnico local existe | external-public | custódia, backup e drill de produção |
| G-008 | P0_OPERATIONAL | lease TUF precisa continuar renovável | HEALTHY_WITH_OPERATION | timestamp autenticado e monitor técnico | owner-only | manter alerta e renovar antes da janela |
| G-009 | P2 | mirrors fora do installer exigem verificação contínua | IN_PROGRESS | installer GitHub/GitLab E2 | owner-only/external | convergência após cada projeção |
| G-010 | P1 | somente M3 possui evidência nativa E3 | ACCEPTED_SCOPE | 25/25 no Apple M3 | owner-only | não promover outras plataformas por inferência |
| G-011 | P1 | soak e aceite externo inexistentes | DEFERRED | audiência atual owner-only | external-public | reabrir em EP-2/EP-4 |
| G-012 | P1 | QWLeague sem contrato oficial | BLOCKED_EXTERNAL | nenhuma API/OAuth/webhook autorizado | opcional | contato e documentação oficiais |
| G-013 | P2 | flags remotas de segurança não verificadas anonimamente | DEFERRED | acesso autenticado necessário | external-public | revisão autenticada |
| G-014 | P1 | processo antigo bloqueava funcionalidade por gates externos | RESOLVED_BY_REBASELINE | S0 M3 é o único bloqueio funcional | owner-only | manter S0 verde |
| G-015 | P1 | migração histórica de 0.7.13 não reproduzida | NOT_REQUIRED_FOR_FRESH_BASELINE | baseline nova planejada | external-public condicional | reabrir se upgrade antigo for prometido |
| G-016 | P1 | source/projection live não confirmada no novo caminho assembled | RESOLVED | workflow #199; receipt 31965901520 artifact 9268487003 | owner-only | overlay seguinte deve preservar TUF v22 e 3/3 contexts |

## Ordem de tratamento

1. G-001 já está resolvido.
2. G-002 e G-016 fecharam a convergência da projeção owner-only no receipt 31965901520.
3. G-008 permanece operacional e recorrente, mas não bloqueia funcionalidades quando saudável.
4. S0 está PASS no M3; F1–F3 e FUNC-008 já estão na main.
5. G-004, G-006, G-007, G-011, G-013 e G-015 só viram bloqueadores quando a audiência mudar.
6. G-012 permanece `BLOCKED_EXTERNAL` em qualquer cenário.

## Regra de honestidade

Um gap pode estar aceito no escopo owner-only sem ser resolvido para external-public. O status e a audiência devem aparecer juntos; nenhum preview ou artifact availability vira suporte por inferência.
