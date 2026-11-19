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
