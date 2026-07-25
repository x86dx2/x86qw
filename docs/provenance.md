# Proveniência e redistribuição

Um arquivo estar disponível para download não significa que possa ser
redistribuído. Nenhum componente entra no mirror antes de registrar licença,
atribuição, origem imutável e checksum.

## Inventário inicial

| Componente | Origem conhecida | Tratamento inicial |
| --- | --- | --- |
| ezQuake stable | `QW-Group/ezquake-source` | revisar release, licença e fontes correspondentes |
| ezQuake nightly | `builds.quakeworld.nu` | vincular ao commit e revisar redistribuição |
| dados nQuake | `nQuake/distfiles` | classificar GPL e non-GPL arquivo a arquivo |
| classicQ | `classicq/classicq` | revisar release, licença e dependências incluídas |
| unezQuake | `dusty-qw/unezquake` | revisar release, licença e dependências incluídas |
| mapas e LOCs | `maps.quakeworld.nu` e autores | exigir licença ou autorização por item |
| presets x86QW | autoria deste projeto | versionar como fonte, sem pacote externo |
| PAKs de `id1` | cópia do usuário | nunca baixar, versionar ou espelhar |

“Revisar” significa que o componente ainda não está autorizado para o mirror.

## Registro obrigatório por artefato

- componente, versão, canal, sistema e arquitetura;
- URL original imutável, projeto e autor;
- licença declarada e arquivo de licença preservado;
- obrigações de atribuição ou oferta de código-fonte;
- nome, tamanho e SHA-256 do arquivo recebido;
- data de ingestão e ferramenta usada;
- URLs e SHA-256 do pacote publicado pelo x86QW.

Se a redistribuição não estiver clara, o catálogo poderá apontar para a origem,
mas o x86QW não armazenará uma cópia até obter autorização.
