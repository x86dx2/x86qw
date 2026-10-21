# Licença do conteúdo próprio

O código Go, os presets, patches, configurações, documentação, testes,
metadados e demais arquivos criados pelo projeto x86QW são identificados como
`MIT` quando não houver um aviso mais específico. A licença canônica está em
[LICENSE](../LICENSE), e o aviso de distribuição em [NOTICE](../NOTICE).

Isso não relicencia componentes upstream. Clientes, engines, mods, ferramentas,
dados-base e PAKs continuam com seus avisos e termos de origem; a proveniência
por componente está em [maintenance/docs/provenance.md](../maintenance/docs/provenance.md)
e nos inventários. O `license_url` de um pacote de componente aponta para a
licença da fonte correspondente, não para a do x86QW. As entradas da CLI
`x86qw-cli` apontam para o `LICENSE` deste repositório.

Cada release `x86qw-cli-*` publica os binários com `SHA256SUMS` e um atestado
de proveniência do build no GitHub; os pacotes de conteúdo são verificados por
tamanho e SHA-256 contra o catálogo assinado. Esses registros documentam como
os bytes foram produzidos; eles não alteram a licença dos payloads upstream nem
substituem os avisos de origem.

As releases históricas `x86qw-installer-*` (a linha Python 0.x e 1.0)
publicaram também `candidate.json`, `sbom.spdx.json` e `provenance.json`; elas
permanecem imutáveis.
