# Runbook de chaves

O repositório TUF público usa chaves Ed25519 e quatro papéis, definidos pela
root em `maintenance/trust/root.json` (também embutida na CLI):

| Papel | Threshold | Onde fica a chave | Vale por |
|---|---|---|---|
| root | 2 de 3 | offline, no Mac do mantenedor | até a próxima root (v1 vence em 2027-08-10) |
| targets | 2 de 3 | offline, no Mac do mantenedor | 90 dias a partir da assinatura |
| snapshot | 1 de 2 | offline, no Mac do mantenedor | 90 dias a partir da assinatura |
| timestamp | 1 de 2 | offline e no segredo `TUF_TIMESTAMP_KEY_B64` | 30 dias, renovado toda semana |

- As chaves offline ficam em `.tuf-production/keys`, ignorado pelo Git. Elas
  nunca entram no GitHub, no GitLab, na CI, no catálogo nem em logs.
- Só a chave da role timestamp fica online; ela não assina root, targets nem
  snapshot, então não muda o catálogo.
- `x86qw-release sign` assina uma geração nova com as chaves offline; a
  renovação semanal usa `x86qw-release renew-timestamp` com a chave online. O
  passo a passo está no [runbook de release](release.md).
- Uma root nova exige a assinatura da root atual (threshold 2) e da nova. A CLI
  segue a cadeia de roots publicadas a partir da que traz embutida.

Não use hashes fornecidos pelo próprio catálogo como autenticação. Não gere
chaves de produção em testes.
