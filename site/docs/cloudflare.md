# Publishing on Cloudflare

`site/wrangler.jsonc` publishes `site/public` as Workers Static Assets and registers
`qw.x86.com.br` as the only Custom Domain. No JavaScript Worker runs on the normal path:
HTML, CSS and the catalog are identical static files at the edge.

## Validate without publishing

```sh
cd site
npm ci
npm run deploy:dry-run
cd ..
go -C go run ./cmd/x86qw-content verify
```

Wrangler is pinned in `site/package.json`; an update of it repeats the
dry run before the deploy. CI also assembles the site as the deploy does
(`x86qw-release site`) and runs the dry run on that assembly.

## Publish

1. Confirm that `x86.com.br` is an active zone in the right Cloudflare account.
2. Confirm that `qw.x86.com.br` has no conflicting CNAME.
3. Authenticate Wrangler with a token restricted to the account and the zone.
4. The normal deploy is the `site-deploy.yml` workflow, which runs on every merge that
   changes `site/public` (and on `workflow_dispatch`): it assembles the site with
   `x86qw-release site`, refuses to roll the TUF timestamp back and publishes with
   `wrangler deploy --assets`. The weekly timestamp renewal publishes through the
   same path. The `CLOUDFLARE_API_TOKEN` token lives in the GitHub `release`
   environment.
5. Check `/`, `/api/v1/catalog.json` and a URL that does not exist.

The deploy creates the Custom Domain and its DNS record and certificate. The `account_id`
versioned in `wrangler.jsonc` identifies the routing account and is not a
credential. API tokens, keys and other credentials never enter the
repository.

## Shortcut on the x86.com.br portal

Create a Single Redirect Rule in the `x86.com.br` zone:

```text
Request URL:          https://x86.com.br/x86qw*
Target URL:           https://qw.x86.com.br${1}
Status:               308
Preserve query string: enabled
```

The wildcard keeps paths: `/x86qw/docs` becomes `/docs`. That rule belongs
to the domain's general portal, not to the x86QW Worker.

The legacy alias must not be redirected or removed while published versions
still use it for the catalog and TUF metadata.

The zone's Browser Integrity Check injects an inline bootstrap before
`/cdn-cgi/challenge-platform/`. That is why the CSP allows inline script, while keeping
external scripts restricted to the site's own origin and blocking external objects, frames, forms and
bases. Removing `unsafe-inline` first requires turning that injection off in the
zone configuration.

## Official references

- [Workers Static Assets](https://developers.cloudflare.com/workers/static-assets/)
- [Custom Domains](https://developers.cloudflare.com/workers/configuration/routing/custom-domains/)
- [Single Redirects](https://developers.cloudflare.com/rules/url-forwarding/single-redirects/settings/)
