import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const SITE_JS = fileURLToPath(new URL('../public/assets/site.js', import.meta.url));

const makeNode = (header = false) => {
  const nodes = {
    '.catalog-label': { textContent: header ? 'Verificando' : 'Consultando o catálogo' },
    '.catalog-value': { textContent: '—' },
    '.catalog-detail': { textContent: 'Lendo o contrato público em tempo real.' },
  };
  return {
    dataset: { state: 'loading' },
    attributes: {},
    classList: { contains(name) { return header && name === 'header-status'; } },
    querySelector(selector) { return nodes[selector] ?? null; },
    setAttribute(name, value) { this.attributes[name] = value; },
    nodes,
  };
};

async function loadSiteScript() {
  const source = await readFile(SITE_JS, 'utf8');
  Function(source)();
  await new Promise((resolve) => globalThis.setTimeout(resolve, 10));
}

test('catalog failure is announced in text instead of color alone', async () => {
  const header = makeNode(true);
  const status = makeNode(false);
  globalThis.document = {
    querySelectorAll(selector) {
      if (selector === '[data-catalog-live]') return [header, status];
      return [];
    },
  };
  globalThis.fetch = async () => { throw new Error('offline'); };

  await loadSiteScript();

  assert.equal(header.dataset.state, 'error');
  assert.equal(header.nodes['.catalog-label'].textContent, 'Catálogo indisponível');
  assert.match(header.attributes['aria-label'], /Não foi possível confirmar/);
  assert.equal(status.dataset.state, 'error');
  assert.equal(status.nodes['.catalog-label'].textContent, 'Catálogo indisponível');
  assert.equal(status.nodes['.catalog-value'].textContent, 'Não foi possível confirmar o estado');
});

test('live status reads product.json instead of the full catalog', async () => {
  const requested = [];
  const header = makeNode(true);
  const status = makeNode(false);
  globalThis.document = {
    querySelectorAll(selector) {
      if (selector === '[data-catalog-live]') return [header, status];
      return [];
    },
  };
  globalThis.fetch = async (url, options) => {
    requested.push({ url: String(url), cache: options?.cache });
    return {
      ok: true,
      async json() {
        return {
          project: 'x86qw',
          package_count: 29,
          games: [{ id: 'ktx', version: '1.47' }],
        };
      },
    };
  };

  await loadSiteScript();

  assert.deepEqual(requested, [{ url: '/api/v1/product.json', cache: 'no-store' }]);
  assert.equal(header.dataset.state, 'ready');
  assert.equal(header.nodes['.catalog-label'].textContent, 'Catálogo publicado');
  assert.equal(status.dataset.state, 'ready');
  assert.equal(status.nodes['.catalog-value'].textContent, '29 pacotes auditados');
  assert.match(status.nodes['.catalog-detail'].textContent, /KTX atual: 1\.47/);
});
