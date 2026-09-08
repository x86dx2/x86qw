# Assignment

- assignment_id:
- gate:
- operational_role: Bootstrap Qualifier | Orchestrator | Maker | Checker externo
- objective:
- baseline_sha:
- branch:
- cwd/worktree:
- allowlist:
- non_scope:
- dependencies:
- exclusive_lock / owner:
- risks:
- authorized_commands:
- acceptance_criteria:
- tests:
- scratch_outputs / observed_writable_roots:
- rollback:
- maker:
- test_runner:
- checkers:
- finalizer:
- subagent_ids:
- content_identity: commit SHA ou base + manifesto (nunca review_sha inventado)
- started_at_utc:
- finished_at_utc:
- results:
- limitations:
- decision:
- human_exception: nenhuma por padrão

Lifecycle: PLANNED → SPAWNED → RUNNING → REPORTING → CHECKING →
APPROVED|REWORK|BLOCKED|CANCELLED|FAILED → INTEGRATED → CLOSED.
No bootstrap sem commit: não alegar INTEGRATED. Registre estado real e próxima ação.
