# Proveniência e redistribuição

Um arquivo estar disponível para download não significa que possa ser
redistribuído. Nenhum componente entra no mirror antes de registrar licença,
atribuição, origem imutável e checksum.

## Auditoria inicial — atualizada em 29 de julho de 2026

| Componente | Evidência | Estado do mirror |
| --- | --- | --- |
| ezQuake stable | [repositório](https://github.com/QW-Group/ezquake-source) identificado como GPL-2.0 | 3.6.9 preservado em `dist/clients/ezquake/stable/3.6.9` e replicado nos mirrors |
| ezQuake nightly | binários em `builds.quakeworld.nu`, derivados do mesmo projeto | build `20260616-101233_a86996a` preservado em `dist/clients/ezquake/nightly/20260616-101233_a86996a` |
| componentes de referência nQuake | [distfiles](https://github.com/nQuake/distfiles) fixado por commit e decomposto por `maintenance/inventory/components.json` | originais em `dist/distributions/nquake`; pacotes de entrega são builds reproduzíveis temporários |
| KTX | `1.46-dev` identificado no QVM embarcado; [release oficial 1.47](https://github.com/QW-Group/ktx/releases/tag/1.47) com checksums | QVM 1.47 aplicado sobre os recursos nQuake e publicado como pacote independente |
| Pro-X | [release pública 1.1](https://www.quakeworld.nu/forum/topic/3741/prox-11-released) recuperada do arquivo histórico do autor | ZIP original em `dist/mods/pro-x/1.1/upstream/`; código-fonte público não localizado |
| Team Fortress | [runtime 2.9 e cópia pública das fontes](https://www.frag-net.com/mod_quake_fortress.html) | runtime em `upstream/`; fonte pública preservada em `source/`, idêntica à cópia do Internet Archive, mas sem comprovação da publicação original; gamecode 2.9 aplicado sobre assets nQuake |
| Total Destruction 2 | [distribuição TD2QW 2.22](https://arenacamper.ddns.net/repo/quakeworld/) completa, com código-fonte; `saw_down.wav` histórico é byte-idêntico ao `saw.wav` preservado | original completo em `dist/mods/td2/2.22/source/` e pacote instalável independente, sem mapas nem senha padrão |
| mapas e LOCs | `maps.quakeworld.nu` e autores individuais | fora do mirror; adicionar somente itens incorporados pontualmente ao x86QW |
| presets x86QW | autoria deste projeto | liberado após definirmos a licença do próprio x86QW |
| PAKs de `id1` | cópia registrada fornecida pelo mantenedor, fixada por SHA-256 | fontes canônicas em `dist/game-data/id1`; pacote público separado `x86qw-core-id1`, nunca incorporado ao instalador |

“Candidato” ainda não autoriza publicação. A licença do código-fonte não prova,
sozinha, que todos os arquivos agregados ao pacote binário usam termos
compatíveis. O repositório `nQuake/distfiles` demonstra por que a auditoria deve
acontecer por artefato, e não apenas pelo nome do projeto. Clientes além do
ezQuake ficam fora do escopo ativo e não são capturados nesta fase.

### Receita inicial do ezQuake 3.6.9

Em 25 de julho de 2026, os três assets oficiais da release 3.6.9 foram baixados
em uma área temporária. Tamanho, SHA-256 e membros mínimos dos ZIPs coincidiram
com `checksums.txt` e com os digests publicados pela API do GitHub:

| Plataforma | Arquivo | Tamanho | SHA-256 |
| --- | --- | ---: | --- |
| macOS | `ezQuake-macOS-universal.zip` | 8.560.464 | `2ccea8f214c91e5fc92b5cb195c81ac584f75a551d11a58de56e5e7951eec7ed` |
| Linux | `ezQuake-linux-x86_64.zip` | 22.268.939 | `6d5707bf9be1a8338441265f9bf03154d107488a4fed9eab4c989c29de6573ee` |
| Windows | `ezQuake-windows-x64.zip` | 4.135.630 | `1814d2c9df12a732a5b2efb8720e67bb94094b422794e5f2550821f17a377f4d` |

As três receitas stable estão `ready`; seus artefatos ficam em `dist/clients/ezquake/stable/3.6.9`
e são publicados exclusivamente nos projetos `x86qw`. O catálogo registra os três binários nightly e o commit
completo `a86996a3d33dc1bc3fb15bfe7bcadd662b822557`. Os tarballs de fonte exatos de
stable e nightly também ficam preservados em `dist/` para consulta e compilação.

### Atualização independente do KTX

O release oficial `qwprogs-qvm.zip` 1.47 possui SHA-256
`e7b6382197a31b8cbf010b1e3a3b20c19dc56d57290015ebba306f0e7ab5c6ed`.
Seu membro `qwprogs.qvm` possui 1.578.544 bytes e SHA-256
`a1987546d3b00453e5a0e1e7e89d75edd2a6779997436d5dac6a7d741bdcc79c`;
`qwprogs.map` possui 112.973 bytes e SHA-256
`71b3b9798ff91aa9d168ce8e8a28c2f2d23d54ba4c5ad84839dd4844b37e9c96`.
O empacotador preserva os demais membros do `ktx.pk3` de referência, troca o
QVM e acrescenta seu mapa de símbolos oficial. O pacote x86QW resultante é
`1.47+x86qw.5`; fontes, override, catálogo de modos, gameplay e hashes ficam no inventário
versionado e nos metadados internos do ZIP.

O fluxo completo de instalar, verificar e desinstalar foi executado com ezQuake
stable 3.6.9 e com a nightly `20260616-101233_a86996a`. Isso comprova a gestão
do pacote no cliente; o QVM continua sendo código de servidor, oficialmente
destinado ao MVDSV segundo o upstream do KTX.

Todos os artefatos instaláveis atuais também são publicados no GitLab Generic Package Registry.
O catálogo ordena GitHub primeiro e GitLab depois; a ferramenta de publicação
baixa ambas as cópias e exige o mesmo tamanho e SHA-256 antes do registro.

## Registro obrigatório por artefato

- componente, versão, canal, sistema e arquitetura;
- URL original imutável, projeto e autor;
- licença declarada e URL do texto de licença preservado;
- URLs das fontes correspondentes e obrigações de atribuição;
- nome, tamanho e SHA-256 do arquivo recebido;
- data de ingestão e ferramenta usada;
- URLs e SHA-256 do pacote publicado pelo x86QW.

Se a redistribuição não estiver clara, o catálogo poderá apontar para a origem,
mas o x86QW não armazenará uma cópia até obter autorização.
