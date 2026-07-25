# Proveniência e redistribuição

Um arquivo estar disponível para download não significa que possa ser
redistribuído. Nenhum componente entra no mirror antes de registrar licença,
atribuição, origem imutável e checksum.

## Auditoria inicial — 25 de julho de 2026

| Componente | Evidência | Estado do mirror |
| --- | --- | --- |
| ezQuake stable | [repositório](https://github.com/QW-Group/ezquake-source) identificado como GPL-2.0 | candidato; auditar o conteúdo da release e publicar a fonte correspondente |
| ezQuake nightly | binários em `builds.quakeworld.nu`, derivados do mesmo projeto | bloqueado até vincular cada build ao commit e à fonte correspondente |
| dados nQuake | [distfiles](https://github.com/nQuake/distfiles) contém árvores `gpl` e `non-gpl`, mas não possui licença única no topo | bloqueado; classificar arquivo por arquivo e não presumir licença para `non-gpl` |
| classicQ | [repositório](https://github.com/classicq/classicq) identificado como GPL-2.0 | candidato; auditar dependências e arquivos incluídos na release |
| unezQuake | [repositório](https://github.com/dusty-qw/unezquake) identificado como GPL-2.0 | candidato; auditar dependências e arquivos incluídos na release |
| mapas e LOCs | `maps.quakeworld.nu` e autores individuais | bloqueado; exigir licença ou autorização por item |
| presets x86QW | autoria deste projeto | liberado após definirmos a licença do próprio x86QW |
| PAKs de `id1` | cópia comercial fornecida pelo usuário | proibido baixar, versionar ou espelhar |

“Candidato” ainda não autoriza publicação. A licença do código-fonte não prova,
sozinha, que todos os arquivos agregados ao pacote binário usam termos
compatíveis. O repositório `nQuake/distfiles` demonstra por que a auditoria deve
acontecer por artefato, e não apenas pelo nome do projeto.

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
