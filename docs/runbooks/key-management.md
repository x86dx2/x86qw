# Runbook de chaves

- a chave raiz é criada offline e sua chave privada nunca entra no GitHub,
  GitLab, CI, catálogo ou logs;
- a CLI fixa somente a chave pública raiz e aceita rotação assinada por um
  threshold anterior;
- chaves de `snapshot`, `current` e `evidence` são separadas e têm expiração;
- rotação registra versão monotônica, motivo, operador e data em evidência;
- comprometimento exige revogar a chave afetada, aumentar a versão da raiz,
  publicar um novo snapshot assinado e verificar mirrors antes do ponteiro
  `current`.

Não use hashes fornecidos pelo próprio catálogo como autenticação. Não gere
chaves de produção em testes locais.
