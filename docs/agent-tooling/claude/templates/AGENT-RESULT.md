# Agent result (Claude Code)

```json
{
  "assignment_id": "",
  "role": "",
  "native_agent_id": "NOT_OBSERVABLE",
  "transcript_ref": null,
  "harness_version": "NOT_OBSERVABLE",
  "requested_model": "",
  "observed_model": "NOT_OBSERVABLE",
  "requested_effort": "",
  "observed_effort": "NOT_OBSERVABLE",
  "requested_tools": [],
  "guard_mode": "",
  "parent_permission_mode": "NOT_OBSERVABLE",
  "guard_denials": [],
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

Findings: arquivo/símbolo, impacto, reprodução, severidade (P0-P3) e evidência.
Testes: comando, ambiente relevante, saída sanitizada, exit code e
PASS/FAIL/SKIP/NOT_RUN. Não relatar execução sem prova.
Modelo observado vem do transcript nativo do harness (campo `model` das mensagens),
nunca da resposta do modelo ou de PID. Effort só conta como observado se o
transcript o expuser; caso contrário, NOT_OBSERVABLE.
Negações do guard são registradas em guard_denials, com comando e motivo.
Uma correção referencia o registro anterior; não apaga história.
