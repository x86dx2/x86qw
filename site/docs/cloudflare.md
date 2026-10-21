# Publicação na Cloudflare

O `site/wrangler.jsonc` publica `site/public` como Workers Static Assets e registra
`qw.x86.com.br` como o único Custom Domain. Nenhum Worker JavaScript executa no caminho normal:
HTML, CSS e o catálogo são arquivos estáticos idênticos no edge.

## Validar sem publicar

```sh
cd site
npm ci
npm run deploy:dry-run
cd ..
go -C go run ./cmd/x86qw-content verify
```

O Wrangler vem fixado em `site/package.json`; uma atualização dele repete o
dry-run antes do deploy. A CI também monta o site como o deploy faz
(`x86qw-release site`) e roda o dry-run sobre essa montagem.

## Publicar

1. Confirme que `x86.com.br` é uma zona ativa na conta Cloudflare correta.
2. Confirme que `qw.x86.com.br` não possui um CNAME conflitante.
3. Autentique o Wrangler com um token restrito à conta e à zona.
4. O deploy normal é o workflow `site-deploy.yml`, que roda a cada merge que
   muda `site/public` (e por `workflow_dispatch`): monta o site com
   `x86qw-release site`, recusa voltar o timestamp TUF e publica com
   `wrangler deploy --assets`. A renovação semanal do timestamp publica pelo
   mesmo caminho. O token `CLOUDFLARE_API_TOKEN` fica no ambiente `release` do
   GitHub.
5. Valide `/`, `/api/v1/catalog.json` e uma URL inexistente.

O deploy cria o Custom Domain e seu registro DNS/certificado. O `account_id`
versionado no `wrangler.jsonc` identifica a conta de roteamento e não é uma
credencial. Tokens de API, chaves e demais credenciais nunca entram no
repositório.

## Atalho no portal x86.com.br

Crie uma Single Redirect Rule na zona `x86.com.br`:

```text
Request URL:          https://x86.com.br/x86qw*
Target URL:           https://qw.x86.com.br${1}
Status:               308
Preserve query string: enabled
```

O curinga preserva caminhos: `/x86qw/docs` passa a `/docs`. Essa regra pertence
ao portal geral do domínio e não ao Worker do x86QW.

O alias legado não deve ser redirecionado nem removido enquanto houver versões
publicadas que o utilizem para catálogo e metadados TUF.

O Browser Integrity Check da zona injeta um bootstrap inline antes de
`/cdn-cgi/challenge-platform/`. Por isso a CSP permite script inline, mas mantém
scripts externos restritos à própria origem e bloqueia objetos, frames, forms e
bases externas. Remover `unsafe-inline` exige primeiro desativar essa injeção na
configuração da zona.

## Referências oficiais

- [Workers Static Assets](https://developers.cloudflare.com/workers/static-assets/)
- [Custom Domains](https://developers.cloudflare.com/workers/configuration/routing/custom-domains/)
- [Single Redirects](https://developers.cloudflare.com/rules/url-forwarding/single-redirects/settings/)
