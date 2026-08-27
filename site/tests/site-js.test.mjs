import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';

test('each install button copies the command for its own shell', async () => {
  const copied = [];
  const commands = new Map([
    ['install-unix-command', { textContent: 'curl -fsSL https://qw.x86.com.br/install.sh | bash' }],
    ['install-windows-command', { textContent: 'irm https://qw.x86.com.br/install.ps1 | iex' }],
  ]);
  const buttons = [...commands.keys()].map((target) => {
    const listeners = new Map();
    const status = { textContent: '' };
    return {
      dataset: { copyTarget: target },
      textContent: commands.get(target).textContent,
      status,
      addEventListener(type, listener) {
        listeners.set(type, listener);
      },
      querySelector(selector) {
        return selector === '[data-copy-status]' ? status : null;
      },
      async click() {
        await listeners.get('click')?.();
      },
    };
  });

  globalThis.document = {
    getElementById(id) {
      return commands.get(id) ?? null;
    },
    querySelector(selector) {
      if (selector === '.catalog-status') return null;
      if (selector === '[data-copy-install]') return buttons[0];
      if (selector === '[data-install-command]') return commands.get('install-unix-command');
      return null;
    },
    querySelectorAll(selector) {
      return selector === '[data-copy-install]' ? buttons : [];
    },
  };
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: { clipboard: { async writeText(command) { copied.push(command); } } },
  });
  globalThis.window = { setTimeout() {} };

  const script = pathToFileURL(new URL('../public/assets/site.js', import.meta.url).pathname);
  await import(`${script.href}?copy-test=${Date.now()}`);
  await buttons[0].click();
  await buttons[1].click();

  assert.deepEqual(copied, [...commands.values()].map(({ textContent }) => textContent));
  assert.deepEqual(buttons.map(({ dataset }) => dataset.copyState), ['copied', 'copied']);
  assert.deepEqual(buttons.map(({ status }) => status.textContent), ['Copiado', 'Copiado']);
  assert.deepEqual(
    buttons.map(({ textContent }) => textContent),
    [...commands.values()].map(({ textContent }) => textContent),
  );
});
