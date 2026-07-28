# Publicação na Cloudflare

O `site/wrangler.jsonc` publica `site/public` como Workers Static Assets e registra
`x86qw.x86.com.br` como Custom Domain. Nenhum Worker JavaScript executa no
caminho normal: HTML, CSS e o catálogo são arquivos estáticos no edge.

## Validar sem publicar

```sh
cd site
npx --yes wrangler@4.114.0 deploy --dry-run
cd ..
./maintenance/manage.py verify --no-tests
```

A versão 4.114.0 do Wrangler foi usada para validar esta configuração em 25 de
julho de 2026. Atualizações devem repetir o dry-run antes do deploy.

## Publicar

1. Confirme que `x86.com.br` é uma zona ativa na conta Cloudflare correta.
2. Confirme que `x86qw.x86.com.br` não possui um CNAME conflitante.
3. Autentique o Wrangler com um token restrito à conta e à zona.
4. Entre em `site/` e execute `npx --yes wrangler@4.114.0 deploy`.
5. Valide `/`, `/api/v1/catalog.json` e uma URL inexistente.

O deploy cria o Custom Domain e seu registro DNS/certificado. Tokens, IDs de
conta e credenciais nunca entram no repositório.

## Atalho no portal x86.com.br

Crie uma Single Redirect Rule na zona `x86.com.br`:

```text
Request URL:          https://x86.com.br/x86qw*
Target URL:           https://x86qw.x86.com.br${1}
Status:               308
Preserve query string: enabled
```

O curinga preserva caminhos: `/x86qw/docs` passa a `/docs`. Essa regra pertence
ao portal geral do domínio e não ao Worker do x86QW.

## Referências oficiais

- [Workers Static Assets](https://developers.cloudflare.com/workers/static-assets/)
- [Custom Domains](https://developers.cloudflare.com/workers/configuration/routing/custom-domains/)
- [Single Redirects](https://developers.cloudflare.com/rules/url-forwarding/single-redirects/settings/)
