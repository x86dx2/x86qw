# Agent result

```json
{
  "assignment_id": "",
  "role": "",
  "native_agent_id": "NOT_OBSERVABLE",
  "parent_native_agent_id": "NOT_OBSERVABLE",
  "harness_version": "NOT_OBSERVABLE",
  "requested_model": "",
  "observed_model": "NOT_OBSERVABLE",
  "requested_effort": "",
  "observed_effort": "NOT_OBSERVABLE",
  "requested_sandbox": "",
  "observed_sandbox": "NOT_OBSERVABLE",
  "approval_policy_observed": "NOT_OBSERVABLE",
  "permission_evidence_ref": null,
  "native_spawn_evidence_ref": null,
  "cwd": "NOT_OBSERVABLE",
  "git_head": "NOT_OBSERVABLE",
  "content_identity": "",
  "started_at_utc": null,
  "finished_at_utc": null,
  "result_status": "NOT_RUN",
  "limitations": [],
  "findings": [],
  "tests": []
}
```

Findings: arquivo/símbolo, impacto, reprodução, severidade e evidência.
Testes: comando, ambiente relevante, saída sanitizada, exit code,
PASS/FAIL/SKIP/NOT_RUN. Não relatar execução sem prova.
Identidade observada vem de metadata nativa, não da resposta do modelo ou de PID.
Uma correção referencia o registro anterior; não apaga história.
