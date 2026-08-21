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
      if (status) status.textContent = 'Copiado';
    } catch {
      button.dataset.copyState = 'error';
      if (status) status.textContent = 'Selecione o comando';
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
  fetch('/api/v1/catalog.json', { cache: 'no-store' })
    .then((response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    })
    .then((catalog) => {
      if (catalog.project !== 'x86qw' || !Array.isArray(catalog.packages)) {
        throw new Error('catálogo incompatível');
      }

      const count = catalog.packages.length;
      const ktx = catalog.packages.find((item) => item.package === 'ktx');
      const ktxVersion = ktx && (ktx.upstream_version || ktx.version);
      applyCatalog(
        count ? 'ready' : 'pending',
        count ? 'Catálogo publicado' : 'Distribuição em preparação',
        `${count} ${count === 1 ? 'pacote auditado' : 'pacotes auditados'}`,
        count
          ? `Cada entrada possui origem, versão, hash e mirrors revisados.${ktxVersion ? ` KTX atual: ${ktxVersion}.` : ''}`
          : 'Nenhum binário será publicado antes da revisão de licença e proveniência.',
      );
    })
    .catch(() => {
      applyCatalog(
        'error',
        'Catálogo indisponível',
        'Não foi possível confirmar o estado',
        'Tente abrir o JSON diretamente ou volte em alguns minutos.',
      );
    });
}
