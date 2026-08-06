# Resposta a incidente de release

1. Pare a promoção de assets e metadata; não sobrescreva tags ou pacotes.
2. Preserve logs, `candidate.json`, SBOM, proveniência e evidência sem segredos.
3. Verifique o último snapshot confiável e a versão monotônica aceita pela CLI.
4. Revogue a chave ou mirror afetado conforme o runbook de chaves.
5. Publique uma correção somente com novo commit, novo candidato e novos
   digests; o artefato comprometido permanece imutável para auditoria.
6. Registre impacto, plataformas afetadas e checks que falharam na issue de
   incidente.

Não altere PAKs nem reescreva a proveniência dos componentes upstream durante
um incidente.
