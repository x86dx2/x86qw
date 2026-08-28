import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { chromium } from 'playwright';
import AxeBuilder from '@axe-core/playwright';

const PUBLIC_ROOT = fileURLToPath(new URL('../public', import.meta.url));
const VIEWPORTS = [
  { width: 320, height: 720 },
  { width: 390, height: 844 },
  { width: 1440, height: 900 },
];
const MAX_INSTALL_CONTROL_HEIGHT = 120;
const MIME = new Map([
  ['.css', 'text/css; charset=utf-8'],
  ['.html', 'text/html; charset=utf-8'],
  ['.js', 'text/javascript; charset=utf-8'],
  ['.json', 'application/json; charset=utf-8'],
  ['.png', 'image/png'],
  ['.ps1', 'text/plain; charset=utf-8'],
  ['.sh', 'text/plain; charset=utf-8'],
  ['.svg', 'image/svg+xml'],
  ['.txt', 'text/plain; charset=utf-8'],
  ['.woff2', 'font/woff2'],
]);

function publicFile(urlPath) {
  const relative = decodeURIComponent(urlPath.split('?')[0]);
  const requested = relative === '/' ? '/index.html' : relative;
  const resolved = path.resolve(PUBLIC_ROOT, `.${path.posix.normalize(requested)}`);
  if (resolved !== PUBLIC_ROOT && !resolved.startsWith(`${PUBLIC_ROOT}${path.sep}`)) {
    return null;
  }
  return resolved;
}

async function servePublic() {
  const server = createServer(async (request, response) => {
    const target = publicFile(new URL(request.url || '/', 'http://127.0.0.1').pathname);
    if (target === null) {
      response.writeHead(403).end();
      return;
    }
    try {
      const body = await readFile(target);
      response.writeHead(200, {
        'content-type': MIME.get(path.extname(target)) || 'application/octet-stream',
      });
      response.end(body);
    } catch {
      response.writeHead(404).end();
    }
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  return { server, origin: `http://127.0.0.1:${server.address().port}` };
}

function formatViolations(violations) {
  return violations
    .map((violation) => (
      `${violation.impact} ${violation.id}: ${violation.nodes.map((node) => node.target.join(' ')).join(', ')}`
    ))
    .join('\n');
}

test('home has no serious axe violations at 320, 390 and 1440', async (t) => {
  const { server, origin } = await servePublic();
  const browser = await chromium.launch();
  t.after(async () => {
    await browser.close();
    await new Promise((resolve, reject) => {
      server.close((error) => (error ? reject(error) : resolve()));
    });
  });

  for (const viewport of VIEWPORTS) {
    await t.test(`${viewport.width}px`, async () => {
      const context = await browser.newContext({
        viewport: { width: viewport.width, height: viewport.height },
      });
      try {
        const page = await context.newPage();
        await page.goto(`${origin}/`, { waitUntil: 'load' });
        await page.locator('h1').waitFor();

        const heading = await page.locator('h1').innerText();
        assert.match(heading, /Cinco jogos/i);
        assert.match(heading, /Um menu/i);
        assert.match(heading, /Uma partida/i);
        assert.doesNotMatch(heading, /0\.7\.13/);
        assert.equal(await page.locator('.arena-visual').count(), 0);
        assert.equal(await page.locator('.button-primary').getAttribute('href'), '#jogos');
        assert.ok(await page.locator('.skip-link').count());
        assert.ok(await page.locator('table.platform-matrix caption').count());
        assert.ok(await page.locator('table.platform-matrix th[scope]').count());

        const overflow = await page.evaluate(
          () => document.documentElement.scrollWidth - window.innerWidth,
        );
        assert.ok(overflow <= 1, `horizontal overflow of ${overflow}px at ${viewport.width}`);

        for (const control of await page.locator('.install-command-control').all()) {
          const box = await control.boundingBox();
          assert.ok(box, `missing install control box at ${viewport.width}`);
          assert.ok(
            box.height <= MAX_INSTALL_CONTROL_HEIGHT,
            `install control ${box.height}px tall at ${viewport.width}`,
          );
        }

        const results = await new AxeBuilder({ page })
          .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
          .analyze();
        const blocking = results.violations.filter(
          (violation) => violation.impact === 'critical' || violation.impact === 'serious',
        );
        assert.equal(blocking.length, 0, formatViolations(blocking));
      } finally {
        await context.close();
      }
    });
  }
});
