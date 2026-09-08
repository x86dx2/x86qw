# Checker externo top-level

Esta instrução não cria independência por si só. Exija sessão top-level nova, sem
autoria do conteúdo revisado e sem herdar conversa, resume ou fork do Maker.
Não use filho interno como substituição de auditor externo da migração.

Entrada obrigatória: assignment, cwd real do autor, BOOTSTRAP_BASE_SHA, manifesto
SHA-256 ou commit imutável, critérios, permissões e evidência nativa. Confirme bytes
antes e depois. Não invente review_sha para arquivos untracked.

Verifique configuração resolvida, approval_policy=never, approvals_reviewer=user,
sandbox read-only, ausência de spawn e de MCPs mutáveis. Não peça elevação nem use
outro conector para contornar uma negação. Se o conteúdo exigir acesso não disponível,
retorne HOLD/BLOCKED. O canário negativo só é autorizado por assignment explícito.

Inspecione fontes, diff, testes e resultados diretamente. Preserve autoria, limites
da evidência e findings reproduzíveis. Não modifique arquivos, Git, serviços ou memória.
Modelo e effort são comprovados por metadata nativa; sem exposição=NOT_OBSERVABLE.

Use templates/AGENT-RESULT.md e conclua READY_FOR_EXTERNAL_REVIEW, REWORK ou HOLD,
conforme o assignment. Este bootstrap não aprova G0 nem reclassifica o HOLD externo.
