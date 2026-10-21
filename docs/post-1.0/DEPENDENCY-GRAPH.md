# Dependency graph

The post-1.0 work is a DAG. An arrow means “must be closed before”; it does
not mean the next item is already authorized.

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
POST-016 QWLeague ──────► 1.3 ecosystem (not a dependency of EP-0)
POST-019 release-truth lint ─► POST-003/004
```

Gate 0B closes with POST-011, POST-009 and POST-010 before 0C. POST-008 has two
phases: it closes the operational convergence 0C requires and, after EP-2,
repeats equality, fallback and availability against the exact digest of the
external-public candidate in EP-3. The EP-3 entry does not reopen the
constitutional order: it revalidates the mirror, custody and recovery receipts
against the external candidate.

## Hard and soft dependencies

| Kind | Examples | Rule |
| --- | --- | --- |
| hard | 001→002; 002→017→003; 003→020→018 | a failure stops progress |
| hard, security | 011→009→010; EP-2→EP-3 | without sustainable TUF, `NO-GO` |
| informational | 016→1.3 | do not open an endpoint, account or queue |
| quality | 019→003/004 | lint finds a conflict, it does not choose the authority |

Any new dependency on code, a workflow, a service, a key or a product is a
PLAN_DEVIATION for the orchestrator; it must not be absorbed into the
documentation package.
