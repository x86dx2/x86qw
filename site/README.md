# Site x86QW

Todo o portal esta contido neste diretorio:

- `public/`: HTML, CSS, JavaScript, fontes, catalogo e arquivos de edge;
- `wrangler.jsonc`: configuracao do Cloudflare Workers Static Assets;
- `PRODUCT.md` e `DESIGN.md`: produto e sistema visual;
- `docs/`: operacao e deploy;
- `tests/`: contratos semanticos e referencias locais.

Executar localmente:

```sh
cd site
npx --yes wrangler@4.114.0 dev --ip 127.0.0.1 --port 8787
```

Abra <http://127.0.0.1:8787>. O catalogo consumido pelo instalador fica em
`public/api/v1/catalog.json` e e publicado na mesma origem do portal.
