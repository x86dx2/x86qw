# Arquitetura

O mapa interativo da plataforma está em
[`diagrams/x86qw-platform.html`](diagrams/x86qw-platform.html). Sua fonte
versionada fica ao lado, em `x86qw-platform.architecture.json`.

## Contratos públicos

```text
https://x86qw.x86.com.br/                    site do projeto
https://x86qw.x86.com.br/install.sh          bootstrap macOS/Linux
https://x86qw.x86.com.br/install.ps1         bootstrap Windows
https://x86qw.x86.com.br/api/v1/catalog.json catálogo do instalador
https://github.com/x86dx2/x86qw/tree/main/dist distribuição canônica
https://github.com/x86dx2/x86qw-dist/releases mirror de entrega legado
```

Dentro de um checkout, o instalador lê o catálogo versionado, materializa os
componentes diretamente de `dist/nquake` e `dist/mods` e usa `distribution_path`
somente para artefatos upstream indivisíveis, como os clientes ezQuake. As URLs
HTTPS dos pacotes derivados são fallbacks para instalações sem as fontes locais.
O bundle público ativa `--online-only`: ignora fontes e artefatos locais, consulta
o catálogo publicado e grava uma CLI permanente em `.install/cli` na instalação.

## Repositórios

- `x86dx2/x86qw`: distribuição completa em `dist/`, catálogo, instalador, site e validações;
- `x86dx2/x86qw-dist`: GitHub Releases com binários ezQuake e pacotes dos
  componentes x86QW; o projeto homônimo usa GitLab Generic Packages como
  segundo mirror de entrega. Os pacotes de componentes são builds reproduzíveis
  das fontes canônicas e não são versionados novamente no repositório principal.
  Os dois PAKs registrados permanecem em `dist/id1` e entram somente no bundle
  público versionado do instalador, que possui tamanho e SHA-256 no catálogo.

O GitHub é o remoto principal e `gitlab.com/x86dx2/x86qw` mantém a cópia de
contingência do código. `maintenance/manage.py publish` envia somente
artefatos já presentes no catálogo, baixa cada cópia pública, confere tamanho e
SHA-256 e só então registra a segunda URL.

Customizações próprias fazem parte da distribuição, não são constantes
escondidas no instalador. Para o TD2, elas ficam em
`dist/mods/td2/2.22/x86qw/` e são declaradas como `project_sources` no BOM. GitHub é a
origem principal dessa camada e GitLab mantém a mesma árvore como contingência.
O bootstrap geral segue o mesmo contrato em `dist/mods/x86qw/core/`: ele
substitui explicitamente o `autoexec.cfg` da referência, instala documentação
atual e cria somente modelos de configuração pessoal.

O catálogo canônico é `site/public/api/v1/catalog.json`, exatamente o arquivo
servido pelo Worker. Manter o site no mesmo repositório elimina sincronização e
permite que uma única validação cubra publicação e consumo. Os arquivos grandes
ficam na árvore Git por meio do Git LFS. Releases e Generic Packages hospedam
os clientes espelhados e os pacotes derivados necessários fora de um checkout.

## Regra de entrada de componentes

`maintenance/inventory/component-policy.json` é a fronteira geral da distribuição.
`maintenance/inventory/components.json` decompõe o conteúdo de referência em BOM,
perfis, dependências, origens e destinos. O coletor rejeita caminhos fora dessas
declarações e a validação offline rejeita arquivos sem consumidor ou componente.
Pesquisar ou avaliar um recurso não o torna parte do x86QW.

`maintenance/inventory/component-releases.json` mantém a camada de atualização separada: versão
atual, estratégia, upstream, artefatos consumidos e hashes. Assim uma release
como KTX pode avançar sem renomear ou reconstruir componentes alheios.

Todo conteúdo canônico fica sob `dist/`: `ezquake/` contém os clientes,
`nquake/` contém somente o conteúdo incorporado da referência, `mods/` mantém
upstreams e ajustes próprios e `id1/` mantém os PAKs registrados. ZIPs derivados
ficam temporariamente em `maintenance/build/packages/`, fora do Git.

O fluxo de um componente customizado possui duas entradas independentes:

```text
dist/<origem incorporada> + dist/<ajuste x86QW>
                       -> instalador local -> quake-world/
                       -> maintenance/build/packages/<pacote> -> mirrors de entrega
```

Quando um componente for proposto, a ordem é: implementar a ação consumidora,
declarar o componente na política, adicionar testes e só então habilitar seu
download. Remover a ação consumidora exige remover também seus arquivos do
acervo.

Atualizações são detectadas por `maintenance/manage.py check`. O comando
`update` aplica apenas atualizações mecanicamente seguras; releases independentes
exigem uma definição revisada em `add` e `publish` continua explícito. Um adaptador deve preservar o pacote de referência,
aplicar somente membros declarados, verificar hashes internos e produzir um novo
pacote imutável. KTX 1.47 inaugura esse fluxo substituindo apenas
`qwprogs.qvm` dentro de `ktx.pk3`.

## Fluxo de publicação

```text
fonte fixada -> dist/ -> Git LFS -> catálogo -> instalador de desenvolvimento
                     -> maintenance/build/packages/ -> mirrors -> instalador público
dist/id1 + CLI -> bundle do instalador -> GitHub/GitLab -> bootstrap curl/PowerShell
```

As receitas versionadas ficam em `maintenance/recipes/`. O gerenciador valida
origem, tamanho, SHA-256 e membros mínimos. Atualizações são montadas em staging
e somente substituem a árvore canônica quando catálogo, inventários, receitas e
payloads formam um estado coerente.

O Worker serve o site e o catálogo. Para componentes, o instalador valida e
materializa as fontes em `dist/` quando presentes; caso contrário, baixa o ZIP
derivado de uma URL registrada. Nos dois caminhos, gera o mesmo inventário de
arquivos gerenciados antes de alterar `quake-world/`.

Depois de qualquer alteração de componentes, o instalador gera `qw/pak.lst`
com uma ordem determinística, registra o arquivo em recibo próprio e rejeita
divergências na verificação. Os launchers sempre passam `-nohome`, evitando que
um diretório `~/.ezquake` externo sobreponha a distribuição autocontida.

## Segurança e recuperação

- tokens ficam somente nos secrets do provedor de CI;
- toda URL de artefato usa HTTPS;
- nomes de arquivo não podem conter caminhos;
- nenhum pacote é aceito sem tamanho e SHA-256;
- versões publicadas não são substituídas, apenas descontinuadas no catálogo;
- GitHub e GitLab hospedam o mesmo repositório com Git LFS; os registries são
  canais adicionais de entrega. R2 não faz parte da arquitetura atual.
