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

- `x86dx2/x86qw`: catálogo, receitas, instalador e validações;
- `x86dx2/x86qw-site`: Worker e arquivos estáticos do site;
- `x86dx2/x86qw-dist`: será criado somente se os binários e pipelines de
  publicação tornarem o repositório principal pesado ou difícil de manter.

Começamos com dois repositórios necessários. A separação de `x86qw-dist` fica
adiada até existir uma distribuição real, evitando estrutura sem uso.

## Fluxo de publicação

```text
fonte fixada -> licença -> download -> validação -> SHA-256 -> pacote imutável
             -> GitHub Release -> mirror GitLab/R2 -> catálogo -> instalador
```

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
