# x86QW site

The whole portal lives in this directory:

- `public/`: HTML, CSS, JavaScript, fonts, catalog and edge files;
- `wrangler.jsonc`: the Cloudflare Workers Static Assets configuration;
- `PRODUCT.md` and `DESIGN.md`: product and visual system;
- `docs/`: operation and deploy;
- `tests/`: semantic contracts and local references.

To run it locally:

```sh
cd site
npm ci
npx playwright install chromium
npm test
npm run dev
```

Open <http://127.0.0.1:8787>. The catalog the installer reads is
`public/api/v1/catalog.json`, published on the same origin as the portal.
