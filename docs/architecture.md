# Arquitetura

O mapa interativo da plataforma está em
[`diagrams/x86qw-platform.html`](diagrams/x86qw-platform.html). Sua fonte
versionada fica ao lado, em `x86qw-platform.architecture.json`.

## Contratos públicos

```text
https://x86qw.x86.com.br/                    site do projeto
https://x86qw.x86.com.br/api/v1/catalog.json catálogo do instalador
https://github.com/x86dx2/x86qw/tree/main/dist distribuição canônica
https://github.com/x86dx2/x86qw-dist/releases mirror de entrega legado
```

Dentro de um checkout, o instalador lê o catálogo versionado e resolve primeiro
o `distribution_path` de cada pacote em `dist/`. As URLs HTTPS são fallbacks de
entrega para quem obtiver somente o instalador.

## Repositórios

- `x86dx2/x86qw`: distribuição completa em `dist/`, catálogo, instalador, site e validações;
- `x86dx2/x86qw-dist`: GitHub Releases com binários ezQuake e pacotes dos
  componentes x86QW; o projeto homônimo usa GitLab Generic Packages como
  segundo mirror de entrega. Esses serviços repetem artefatos já versionados no
  repositório principal e não são sua fonte canônica. Os dois PAKs registrados
  permanecem em `dist/id1` e não entram no catálogo de pacotes.

O GitHub é o remoto principal e `gitlab.com/x86dx2/x86qw` mantém a cópia de
contingência do código. `tools/publish_gitlab_packages.py` envia somente
artefatos já presentes no catálogo, baixa cada cópia pública, confere tamanho e
SHA-256 e só então registra a segunda URL.

Customizações próprias fazem parte da distribuição, não são constantes
escondidas no instalador. Para o TD2, elas ficam em
`dist/mods/td2/2.22/x86qw/` e são declaradas como `project_sources` no BOM. GitHub é a
origem principal dessa camada e GitLab mantém a mesma árvore como contingência.

O catálogo canônico é `site/public/api/v1/catalog.json`, exatamente o arquivo
servido pelo Worker. Manter o site no mesmo repositório elimina sincronização e
permite que uma única validação cubra publicação e consumo. Os arquivos grandes
ficam na árvore Git por meio do Git LFS; Releases e Generic Packages são cópias
de entrega dos mesmos bytes.

## Regra de entrada de componentes

`inventory/component-policy.json` é a fronteira geral da distribuição.
`inventory/components.json` decompõe o conteúdo de referência em BOM,
perfis, dependências, origens e destinos. O coletor rejeita caminhos fora dessas
declarações e a validação offline rejeita arquivos sem consumidor ou componente.
Pesquisar ou avaliar um recurso não o torna parte do x86QW.

`inventory/component-releases.json` mantém a camada de atualização separada: versão
atual, estratégia, upstream, artefatos consumidos e hashes. Assim uma release
como KTX pode avançar sem renomear ou reconstruir componentes alheios.

Todo payload fica sob `dist/`: `ezquake/` contém os clientes, `nquake/` contém
somente o conteúdo incorporado da referência, `mods/` mantém upstreams e
ajustes próprios, `packages/` contém os ZIPs seletivos usados pelo instalador e
`id1/` mantém os PAKs registrados.

O fluxo de um componente customizado possui duas entradas independentes:

```text
dist/<origem incorporada> + dist/<ajuste x86QW>
                       -> dist/packages/<pacote> -> quake-world/
```

Quando um componente for proposto, a ordem é: implementar a ação consumidora,
declarar o componente na política, adicionar testes e só então habilitar seu
download. Remover a ação consumidora exige remover também seus arquivos do
acervo.

Atualizações são detectadas por `tools/check_component_updates.py`, mas nunca
publicadas automaticamente. Um adaptador deve preservar o pacote de referência,
aplicar somente membros declarados, verificar hashes internos e produzir um novo
pacote imutável. KTX 1.47 inaugura esse fluxo substituindo apenas
`qwprogs.qvm` dentro de `ktx.pk3`.

## Fluxo de publicação

```text
fonte fixada -> dist/ -> validação -> SHA-256 -> pacote em dist/packages/
             -> Git LFS -> catálogo -> instalador -> mirrors opcionais
```

As receitas versionadas ficam em `recipes/`. `tools/build_package.py` aceita
somente receitas com revisão `ready`, usa um diretório temporário e produz em
`dist/` uma cópia byte a byte acompanhada de manifesto. O registro no catálogo
continua explícito com `--register`, mas não depende da publicação prévia em um
serviço externo.

O Worker serve o site e o catálogo. O instalador usa o artefato em `dist/`
quando presente; caso contrário, baixa de uma URL registrada. Nos dois casos,
valida tamanho e SHA-256 antes de extrair qualquer conteúdo.

## Segurança e recuperação

- tokens ficam somente nos secrets do provedor de CI;
- toda URL de artefato usa HTTPS;
- nomes de arquivo não podem conter caminhos;
- nenhum pacote é aceito sem tamanho e SHA-256;
- versões publicadas não são substituídas, apenas descontinuadas no catálogo;
- GitHub e GitLab hospedam o mesmo repositório com Git LFS; os registries são
  canais adicionais de entrega. R2 não faz parte da arquitetura atual.
