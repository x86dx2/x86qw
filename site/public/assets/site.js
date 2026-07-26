const statusBox = document.querySelector('.catalog-status');

if (statusBox) {
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
      statusBox.dataset.state = count ? 'ready' : 'pending';
      statusBox.querySelector('.catalog-label').textContent = count ? 'Catálogo publicado' : 'Distribuição em preparação';
      statusBox.querySelector('.catalog-value').textContent = `${count} ${count === 1 ? 'pacote auditado' : 'pacotes auditados'}`;
      statusBox.querySelector('.catalog-detail').textContent = count
        ? 'Cada entrada possui origem, licença, tamanho, hash e mirrors revisados.'
        : 'Nenhum binário será publicado antes da revisão de licença e proveniência.';
    })
    .catch(() => {
      statusBox.dataset.state = 'error';
      statusBox.querySelector('.catalog-label').textContent = 'Catálogo indisponível';
      statusBox.querySelector('.catalog-value').textContent = 'Não foi possível confirmar o estado';
      statusBox.querySelector('.catalog-detail').textContent = 'Tente abrir o JSON diretamente ou volte em alguns minutos.';
    });
}
