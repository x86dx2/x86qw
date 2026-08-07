# Runbook — comprometimento de trust metadata

Este procedimento é um contrato para uma futura cadeia de trust metadata. O
baseline 0.7.3 não contém endpoint, chave de produção ou implementação de
rotação; nada neste documento autoriza publicação automática ou substitui
aprovação humana.

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

Ao validar metadata assinada quando esse recurso existir, persista
atomicamente o estado de confiança completo, incluindo o envelope/digest do
root e a evidência correspondente. Um estado incompleto não prova a próxima
rotação e deve interromper a operação.

As chaves privadas de produção ficam offline. Não gere uma chave de produção ou
reutilize qualquer fixture de teste como configuração oficial.

## Gate externo para confiança oficial

Antes de habilitar trust no catálogo oficial, registre fora do repositório:

- endpoint HTTPS e política de mirrors imutáveis;
- custódia offline, operadores e threshold de cada papel;
- revisão criptográfica independente do RSA-PSS e vetores cruzados;
- cerimônia de assinatura, aprovação humana e plano de rotação/recuperação.

Sem esses itens, mantenha o trust oficial desabilitado. Bundles públicos 0.7.3
sem metadata local continuam no fluxo legado por compatibilidade; qualquer
conjunto local incompleto ou estado persistido inválido deve interromper a
operação, nunca cair para um catálogo não autenticado.
