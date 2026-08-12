# Runbook — comprometimento de trust metadata

Este procedimento complementa o [ADR 0006](../adr/0006-trust-metadata.md).
Ele não autoriza publicação automática nem substitui aprovação humana.

1. Pare a promoção e preserve os bytes, digests, logs e horário observados.
   Nunca registre segredo de assinatura em shell, argv, journal ou ticket.
2. Coloque `root`, `current` e `snapshot` comprometidos em quarentena; não
   sobrescreva os arquivos públicos existentes.
3. Gere a nova chave fora do workspace e prepare root `N+1`. A transição deve
   ser assinada pela threshold da raiz antiga e pela threshold da nova. Se a
   raiz antiga não estiver disponível, pare para recuperação manual.
4. Remova a chave comprometida dos papéis afetados (`current`, `snapshot` ou
   `evidence`) e mantenha papéis distintos; não reutilize a chave de `current`
   para evidência de release.
5. Verifique a cadeia em ambiente limpo e confira tamanho/digest em cada
   mirror. Publique assets primeiro, snapshot depois e o ponteiro `current`
   por último.
6. Invalide caches da versão comprometida, registre a revisão criptográfica e
   obtenha aprovação antes de reabrir a promoção.

Ao validar `release-evidence.json`, persista atomicamente o `TrustedVersions`
retornado, incluindo o envelope/digest do root e o par de evidence. Um estado
evidence-only sem esse root não consegue provar a próxima rotação e deve ser
tratado como incompleto.

As chaves privadas de produção ficam offline. Os arquivos locais desta PR usam
uma chave de teste apenas para validar a cadeia; isso não é uma autorização de
release.

## Gate externo para confiança oficial

Este checkout não contém endpoint, mirror ou chave pública de produção. Os
arquivos `maintenance/inventory/trust/` e `ROOT_PUBLIC_KEY` são fixtures de
verificação; não os reutilize como configuração oficial nem gere uma chave de
produção durante testes locais. Antes de habilitar o trust no catálogo oficial,
registre fora do repositório:

- endpoint HTTPS e política de mirrors imutáveis;
- custódia offline, operadores e threshold de cada papel;
- revisão criptográfica independente do RSA-PSS e vetores cruzados;
- cerimônia de assinatura, aprovação humana e plano de rotação/recuperação.

Sem esses itens, mantenha o trust oficial desabilitado. Bundles públicos 0.7.3
sem metadados locais continuam no fluxo legado por compatibilidade; qualquer
conjunto local incompleto ou estado persistido inválido deve interromper a
operação, nunca cair para um catálogo não autenticado.
