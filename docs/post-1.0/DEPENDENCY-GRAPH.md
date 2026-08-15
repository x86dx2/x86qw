# Dependency graph

O trabalho pós-1.0 é uma DAG. A seta significa “precisa estar fechado antes
de”; não significa que o item seguinte já está autorizado.

```text
POST-001 deterministic protocol
        │
        ▼
POST-002 main matrix ───────────────┐
        │                            │
        ├── POST-017 source/deploy   │
        │       │                    │
        │       ▼                    │
        │   POST-003 release/audience│
        │       │                    │
        │       ├── POST-004 receipt │
        │       ├── POST-005 SBOM    │
        │       ├── POST-006 cleanup │
        │       ├── POST-008 mirror  │
        │       └── POST-020 govern. │
        │                 │          │
        │                 ├── POST-007 security
        │                 └── POST-018 observation (0E)
        │                            │
        └────────────────────────────┘
                     │
                     ▼
             EP-0 decision / external?
                     │
             external-public path
                     │
               POST-012 migration
                     │
                     ▼
               POST-013 seven-day soak
                     │
                     ▼
               EP-3 revalidate POST-008/009/010
                     │
                     ▼
               POST-014 external user
                     │
                     ▼
               POST-015 platform decision
                     │
                     ▼
             external-public promotion

POST-011 TUF SLO ───────► POST-009 custody ───────► POST-010 recovery (0B)
POST-016 QWLeague ──────► 1.3 ecosystem (não é dependência de EP-0)
POST-019 release-truth lint ─► POST-003/004
```

Gate 0B fecha com POST-011, POST-009 e POST-010 antes de 0C. POST-008 tem duas
fases: fecha a convergência operacional requerida por 0C e, após EP-2, repete
igualdade, fallback e disponibilidade contra o digest exato do candidato
external-public em EP-3. A entrada EP-3 não reabre a ordem constitucional: ela
revalida os receipts de mirror, custody e recovery contra o candidato externo.

## Dependências duras e suaves

| Tipo | Exemplos | Regra |
| --- | --- | --- |
| dura | 001→002; 002→017→003; 003→020→018 | falha impede avanço |
| dura de segurança | 011→009→010; EP-2→EP-3 | sem TUF sustentável, `NO-GO` |
| informativa | 016→1.3 | não abrir endpoint, conta ou fila |
| de qualidade | 019→003/004 | lint encontra conflito, não escolhe autoridade |

Qualquer dependência nova de código, workflow, serviço, chave ou produto é
PLAN_DEVIATION para o orquestrador; não deve ser absorvida no pacote
documental.
