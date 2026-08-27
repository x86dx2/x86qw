import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';

test('catalog failure is announced in text instead of color alone', async () => {
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
  const header = makeNode(true);
  const status = makeNode(false);
  globalThis.document = {
    querySelectorAll(selector) {
      if (selector === '[data-catalog-live]') return [header, status];
      return [];
    },
  };
  globalThis.fetch = async () => { throw new Error('offline'); };

  const script = pathToFileURL(new URL('../public/assets/site.js', import.meta.url).pathname);
  await import(`${script.href}?catalog-error-test=${Date.now()}`);
  await new Promise((resolve) => globalThis.setTimeout(resolve, 10));

  assert.equal(header.dataset.state, 'error');
  assert.equal(header.nodes['.catalog-label'].textContent, 'Catálogo indisponível');
  assert.match(header.attributes['aria-label'], /Não foi possível confirmar/);
  assert.equal(status.dataset.state, 'error');
  assert.equal(status.nodes['.catalog-label'].textContent, 'Catálogo indisponível');
  assert.equal(status.nodes['.catalog-value'].textContent, 'Não foi possível confirmar o estado');
});
