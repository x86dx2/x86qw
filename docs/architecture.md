# Arquitetura

O mapa interativo da plataforma está em
[`diagrams/x86qw-platform.html`](diagrams/x86qw-platform.html). Sua fonte
versionada fica ao lado, em `x86qw-platform.architecture.json`.

## Contratos públicos

```text
https://x86qw.x86.com.br/                    site do projeto
https://x86qw.x86.com.br/api/v1/catalog.json catálogo do instalador
https://downloads.x86.com.br/x86qw/...       artefatos, quando R2 for ativado
```

O catálogo é o único endereço que o instalador precisa conhecer. Cada pacote
contém uma lista ordenada de URLs HTTPS, permitindo trocar ou priorizar mirrors
sem publicar uma nova versão do instalador.

## Repositórios

- `x86dx2/x86qw`: catálogo, receitas, instalador, site e validações;
- `x86dx2/x86qw-dist`: será criado somente se os binários e pipelines de
  publicação tornarem o repositório principal pesado ou difícil de manter.

O GitHub é o remoto principal. `gitlab.com/x86dx2/x86qw` mantém a cópia de
contingência; o primeiro `main` já foi sincronizado, mas a atualização
automática continua pendente no roteiro.

O catálogo canônico é `site/public/api/v1/catalog.json`, exatamente o arquivo
servido pelo Worker. Manter o site no mesmo repositório elimina sincronização e
permite que uma única validação cubra publicação e consumo. A separação de
`x86qw-dist` fica adiada até existir uma distribuição real.

## Fluxo de publicação

```text
fonte fixada -> licença -> download -> validação -> SHA-256 -> pacote imutável
             -> GitHub Release -> mirror GitLab/R2 -> catálogo -> instalador
```

As receitas versionadas ficam em `recipes/`. `tools/build_package.py` aceita
somente receitas com revisão `ready`, usa um diretório temporário e produz em
`dist/` uma cópia byte a byte acompanhada de manifesto. Um build nunca entra no
catálogo implicitamente: o registro exige `--register` depois que o mesmo
arquivo já estiver disponível nas URLs declaradas.

O Worker serve o site e o catálogo. Ele não retransmite corpos de arquivos
grandes; o instalador baixa diretamente de uma das URLs registradas no
catálogo e valida o SHA-256 antes de extrair qualquer conteúdo.

## Segurança e recuperação

- tokens ficam somente nos secrets do provedor de CI;
- toda URL de artefato usa HTTPS;
- nomes de arquivo não podem conter caminhos;
- nenhum pacote é aceito sem tamanho e SHA-256;
- versões publicadas não são substituídas, apenas descontinuadas no catálogo;
- GitHub e GitLab mantêm cópias independentes; R2 será a terceira cópia.
