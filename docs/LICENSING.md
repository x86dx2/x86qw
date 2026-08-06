# Licença do conteúdo próprio

O código Python, os presets, patches, configurações, documentação, testes,
metadados e demais arquivos criados pelo projeto x86QW são identificados como
`MIT` quando não houver um aviso mais específico. A licença canônica está em
[LICENSE](../LICENSE), e o aviso de distribuição em [NOTICE](../NOTICE).

Registros novos de pacotes próprios devem apontar `license_url` para
`https://github.com/x86dx2/x86qw/blob/<tag-imutável>/LICENSE`, usando a tag do
artefato (por exemplo, `x86qw-installer-1.0.0`). A raiz mutável do repositório
não é uma URL válida para novos registros. Registros históricos publicados
antes desta regra permanecem byte a byte inalterados.

Isso não relicencia componentes upstream. Clientes, engines, mods, ferramentas,
dados-base e PAKs continuam com seus avisos e termos de origem; a proveniência
por componente está em [maintenance/docs/provenance.md](../maintenance/docs/provenance.md)
e nos inventários. O `license_url` de uma release de componente é um endereço
imutável da fonte correspondente e não um endereço da licença do x86QW.

O SBOM SPDX 2.3 de um candidato é alimentado por `ownership.json`, produzido
pelos builders e vinculado ao SHA-256 de cada artefato. Ele aplica `MIT` apenas
às entradas declaradas como próprias (incluindo membros próprios conhecidos de
um archive) e mantém `NOASSERTION` para conteúdo upstream, dados-base, PAKs e
archives mistos. O candidato nunca deduz licença por nome de arquivo,
extensão ou diretório.

Bundles modernos do instalador (`>=1.0.0`) carregam cópias byte a byte de
`LICENSE` e `NOTICE` tanto no ZIP externo quanto no `x86qw.pyz` instalado. O
validador exige os dois avisos e confirma que as camadas são idênticas. Os
bundles históricos da série 0.x mantêm seu layout e seus metadados imutáveis;
essa mudança não reescreve nem republica versões anteriores.

Em uma release aprovada, `candidate.json`, `checksums.txt`,
`sbom.spdx.json` e `provenance.json` são publicados
como assets imutáveis junto aos bundles. Eles são documentos de auditoria do
processo de release; a presença pública não altera a licença dos payloads
upstream nem substitui os avisos de origem.
