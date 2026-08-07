# Runbook de chaves

Este é o procedimento a aplicar somente quando a cadeia de metadata assinada
for habilitada por uma decisão de release. O baseline público 0.7.3 não contém
chaves de produção, endpoint de trust ou rotação ativa.

- a chave raiz é criada offline e sua chave privada nunca entra no GitHub,
  GitLab, CI, catálogo ou logs;
- a implementação aprovada deve fixar somente a chave pública raiz e aceitar
  rotação assinada por um threshold anterior;
- chaves de `snapshot`, `current` e `evidence` devem ser separadas e ter
  expiração;
- cada rotação deve registrar versão monotônica, motivo, operador e data em
  evidência;
- comprometimento exige revogar a chave afetada, aumentar a versão da raiz,
  publicar um novo snapshot assinado e verificar mirrors antes do ponteiro
  `current`.

Não use hashes fornecidos pelo próprio catálogo como autenticação. Não gere
chaves de produção em testes locais.
