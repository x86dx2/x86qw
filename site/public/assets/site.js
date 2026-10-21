const copyInstallButtons = document.querySelectorAll('[data-copy-install]');
const catalogLive = document.querySelectorAll('[data-catalog-live]');

copyInstallButtons.forEach((button) => {
  button.addEventListener('click', async () => {
    const command = document.getElementById(button.dataset.copyTarget)?.textContent?.trim();
    const status = button.querySelector('[data-copy-status]');
    if (!command) return;
    try {
      await navigator.clipboard.writeText(command);
      button.dataset.copyState = 'copied';
      if (status) status.textContent = 'Copied';
    } catch {
      button.dataset.copyState = 'error';
      if (status) status.textContent = 'Select the command';
    }
    window.setTimeout(() => {
      button.dataset.copyState = 'idle';
      if (status) status.textContent = '';
    }, 2400);
  });
});

const applyCatalog = (state, label, value, detail) => {
  catalogLive.forEach((box) => {
    box.dataset.state = state;
    if (box.classList.contains('header-status')) {
      box.setAttribute('aria-label', `${label}. ${value}`);
      const labelNode = box.querySelector('.catalog-label');
      if (labelNode) labelNode.textContent = label;
      return;
    }
    const labelNode = box.querySelector('.catalog-label');
    const valueNode = box.querySelector('.catalog-value');
    const detailNode = box.querySelector('.catalog-detail');
    if (labelNode) labelNode.textContent = label;
    if (valueNode) valueNode.textContent = value;
    if (detailNode) detailNode.textContent = detail;
  });
};

if (catalogLive.length) {
  applyCatalog(
    'loading',
    'Checking the catalog',
    'Waiting for an answer',
    'Reading the public contract live.',
  );
  fetch('/api/v1/product.json', { cache: 'no-store' })
    .then((response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    })
    .then((product) => {
      if (
        product.project !== 'x86qw'
        || !Number.isInteger(product.package_count)
        || product.package_count < 0
        || !Array.isArray(product.games)
      ) {
        throw new Error('incompatible product');
      }

      const count = product.package_count;
      const ktx = product.games.find((item) => item.id === 'ktx');
      const ktxVersion = ktx && ktx.version;
      applyCatalog(
        count ? 'ready' : 'pending',
        count ? 'Catalog published' : 'Distribution in preparation',
        `${count} ${count === 1 ? 'audited package' : 'audited packages'}`,
        count
          ? `Every entry has a reviewed origin, version, hash and mirrors.${ktxVersion ? ` Current KTX: ${ktxVersion}.` : ''}`
          : 'No binary is published before its license and provenance are reviewed.',
      );
    })
    .catch(() => {
      applyCatalog(
        'error',
        'Catalog unavailable',
        'Could not confirm the status',
        'Try opening the JSON directly or come back in a few minutes.',
      );
    });
}
