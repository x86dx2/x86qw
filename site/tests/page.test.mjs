import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const page = (name) => readFile(new URL(`../public/${name}`, import.meta.url), 'utf8');

// Windows PowerShell 5.1 may offer only TLS 1.0, which the site and GitHub
// refuse, so the command turns on TLS 1.2 before it fetches the bootstrap.
test('the Windows command turns on TLS 1.2 first', async () => {
  const command = (await page('index.html')).match(/id="install-windows-command"[^>]*>([^<]*)</)[1];
  assert.equal(
    command,
    'powershell -c &quot;[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072; irm https://qw.x86.com.br/install.ps1 | iex&quot;',
  );
});

// Opened in a browser, a bootstrap shows as text to read before running it;
// without this, Cloudflare sends install.ps1 with no type and install.sh as
// application/x-sh, and with nosniff both download.
test('the bootstraps are served as text', async () => {
  const rules = (await page('_headers')).split(/\n(?=\S)/);
  for (const script of ['/install.sh', '/install.ps1']) {
    const rule = rules.find((block) => block.startsWith(`${script}\n`)) ?? '';
    assert.match(rule, /^ {2}Content-Type: text\/plain; charset=utf-8$/m, script);
  }
});
