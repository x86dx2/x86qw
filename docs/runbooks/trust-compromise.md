# Runbook — comprometimento de chave TUF

Complementa o [ADR 0006](../adr/0006-tuf-trust-metadata.md) e o
[runbook de chaves](key-management.md). Nada aqui roda automaticamente.

## Chave online do timestamp (`TUF_TIMESTAMP_KEY_B64`)

Quem tem essa chave consegue prolongar a validade da geração publicada, mas não
mudar o catálogo nem os pacotes.

1. Desative o workflow `tuf-timestamp-auto-renewal.yml` e apague o segredo.
2. Prepare uma root nova que troque a chave da role timestamp (ver abaixo).
3. Com as chaves offline, assine uma geração nova (`x86qw-release sign`) e
   publique pelo fluxo normal de PR e deploy.
4. Grave a nova chave online no segredo e reative o workflow.

## Chaves offline (targets, snapshot ou root)

1. Pare publicações: nenhum merge que mexa em `site/public` até o fim.
2. Gere as chaves novas fora do checkout, numa máquina confiável.
3. Prepare a root `N+1` sem as chaves comprometidas. Ela precisa da assinatura
   da root atual (threshold 2 de 3) e da nova; se a root atual não alcançar o
   threshold, pare: não há recuperação sem reinstalar a CLI com uma root nova
   embutida.
4. Publique a root nova em `site/public/api/v1/trust/metadata/N+1.root.json` e
   em `maintenance/trust/root.json`, assine uma geração nova com as chaves
   novas e confira com `x86qw-release monitor --checkout` depois do deploy.
5. Registre o que aconteceu numa issue, sem segredos.

`x86qw-release` ainda não tem um comando para gerar uma root nova; esse passo
é manual com a biblioteca go-tuf. As chaves privadas nunca entram em shell,
argv, journal, ticket ou log.
