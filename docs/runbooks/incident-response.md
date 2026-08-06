# Resposta a incidente de release

Este procedimento vale para uma release publicada ou para um candidato
explicitamente criado. O baseline público 0.7.3 permanece imutável e não deve
ser reescrito durante a investigação.

1. Pare a promoção de assets e metadata; não sobrescreva tags ou pacotes.
2. Preserve logs, checksums, manifestos, SBOM/proveniência (se existirem) e
   evidência sem segredos.
3. Verifique o último artefato publicado e os logs do gate correspondente;
   quando houver metadata assinada, consulte também o runbook de chaves.
4. Isole o mirror ou credencial afetado e encaminhe a rotação de chave pela
   aprovação humana prevista no runbook de chaves.
5. Publique uma correção somente com novo commit, novo candidato e novos
   digests; o artefato comprometido permanece imutável para auditoria.
6. Registre impacto, plataformas afetadas e checks que falharam na issue de
   incidente.

Não altere PAKs nem reescreva a proveniência dos componentes upstream durante
um incidente.
