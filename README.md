# x86QW

x86QW será uma distribuição moderna e reproduzível de QuakeWorld, mantida por
`x86dx2` e publicada em `x86qw.x86.com.br`.

Este repositório começa pela parte que precisa permanecer sob nosso controle:
o catálogo público, as regras de proveniência e as ferramentas de validação.
O instalador multiplataforma já foi migrado de `../quake`, que permanece intacto
como referência histórica. A próxima mudança troca suas consultas diretas por
este catálogo.

## Princípios

- cada artefato é imutável e identificado por SHA-256;
- origem, versão e licença são registradas antes do espelhamento;
- o instalador consulta `https://x86qw.x86.com.br/api/v1/catalog.json`;
- GitHub, GitLab e futuramente R2 são mirrors, não contratos do instalador;
- os PAKs comerciais de `id1` nunca entram no repositório ou no mirror;
- o instalador continua multiplataforma e usa somente a biblioteca padrão do
  Python.

## Estrutura inicial

```text
catalog/v1/index.json   catálogo canônico em desenvolvimento
docs/architecture.md    serviços, repositórios e fluxo de publicação
docs/provenance.md      política e inventário das fontes
docs/diagrams/          arquitetura interativa e fonte Archify
docs/installer.md       manual completo do instalador migrado
install-qw.py           instalador macOS, Linux e Windows
tools/validate_catalog.py
tests/test_catalog.py
```

## Validar

```sh
python3 tools/validate_catalog.py
python3 -m unittest discover -s tests -v
./install-qw.py --help
```

Nenhum binário ou conteúdo de terceiros é publicado nesta fase.
