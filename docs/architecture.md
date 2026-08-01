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
https://x86qw.x86.com.br/api/v1/product.json fatos públicos do produto
https://github.com/x86dx2/x86qw/tree/main/dist distribuição canônica
https://github.com/x86dx2/x86qw/releases      releases oficiais
https://gitlab.com/x86dx2/x86qw               código e pacotes de contingência
```

Dentro de um checkout, o instalador lê o catálogo versionado, materializa os
componentes diretamente de `dist/distributions/nquake` e `dist/mods` e usa `distribution_path`
somente para artefatos upstream indivisíveis, como os clientes ezQuake. As URLs
HTTPS dos pacotes derivados são fallbacks para instalações sem as fontes locais.
O bundle público ativa `--online-only`: ignora fontes e artefatos locais, consulta
o catálogo publicado e grava a CLI permanente e seu recibo em `.x86qw/cli/`.
Os launchers `dist/installer/bin/x86qw.sh` e `x86qw.cmd` são fontes versionadas incluídas
no bundle e apenas copiadas para a raiz da instalação.
`.x86qw/` é exclusivamente o plano de controle. MVDSV ocupa a raiz operacional
tradicional (`mvdsv` ou `mvdsv.exe`), QTV ocupa `qtv/`, QWFWD ocupa `qwfwd/` e
as licenças ficam em `docs/licenses/`. Os pacotes carregam variantes numa área
de staging, mas o instalador seleciona exatamente uma plataforma antes do
overlay; o staging e `BUILD.json` nunca chegam ao destino. O contrato começa em
uma árvore nova e não migra o layout anterior.
Os bundles publicados ficam em `dist/installer/packages/<versão>/`; o link
relativo `dist/installer/packages/latest` aponta para a maior versão disponível. O
catálogo conserva as versões oficiais iniciadas em `0.1.0`, marca exatamente uma
como `current` e os bootstraps permanecem fixados nessa versão e em seu SHA-256.

## Repositórios

- `x86dx2/x86qw`: distribuição completa em `dist/`, catálogo, instalador, site,
  validações, GitHub Releases e GitLab Generic Packages. Não existe repositório
  de distribuição paralelo. Os pacotes de componentes são builds reproduzíveis
  das fontes canônicas e não são versionados novamente na árvore Git.
  Os dois PAKs registrados permanecem em `dist/game-data/id1`; o builder produz
  `x86qw-core-id1` como pacote de dados-base separado, com tamanho e SHA-256 no
  catálogo. Eles não entram no bundle do instalador.

O GitHub é o remoto principal e `gitlab.com/x86dx2/x86qw` mantém a cópia de
contingência do código e dos pacotes. `maintenance/manage.py publish` envia
todos os artefatos exclusivamente aos projetos `x86qw`. Ele envia somente artefatos já catalogados, baixa
cada cópia pública, confere tamanho e SHA-256 e só então registra a segunda URL.
O pacote usa a identidade `x86qw-installer`, os assets e tags seguem a convenção
`x86qw-installer-X.Y.Z` e os títulos humanos usam `x86QW Installer X.Y.Z`.
Somente a release do instalador marcada como `current` recebe o selo **Latest**
do GitHub. Clientes, dados-base e mods usam títulos `x86QW Content · ...` e são
sempre publicados com `make_latest=false`; continuam acessíveis por suas tags e
pelo catálogo, sem se apresentar como uma nova versão do instalador.

Customizações próprias fazem parte da distribuição, não são constantes
escondidas no instalador. Para o TD2, elas ficam em
`dist/mods/td2/2.22/x86qw/` e são declaradas como `project_sources` no BOM. GitHub é a
origem principal dessa camada e GitLab mantém a mesma árvore como contingência.
O bootstrap geral segue o mesmo contrato em `dist/mods/x86qw/core/`: ele
substitui explicitamente o `autoexec.cfg` da referência, instala documentação
atual e cria somente modelos de configuração pessoal.

O catálogo de pacotes canônico é `site/public/api/v1/catalog.json`, exatamente o arquivo
servido pelo Worker. Manter o site no mesmo repositório elimina sincronização e
permite que uma única validação cubra publicação e consumo. Os arquivos grandes
ficam na árvore Git por meio do Git LFS. Releases e Generic Packages hospedam
os clientes espelhados e os pacotes derivados necessários fora de um checkout.

Capacidades, runtimes, jogos e compatibilidade são declarados em
`maintenance/inventory/`. A projeção pública
`site/public/api/v1/product.json` combina esses contratos com `VERSION` e o
catálogo de pacotes; `verify` rejeita qualquer projeção ou texto factual
divergente.

## Regra de entrada de componentes

`maintenance/inventory/component-policy.json` é a fronteira geral da distribuição.
`maintenance/inventory/components.json` decompõe o conteúdo de referência em BOM,
perfis, dependências, origens e destinos. O coletor rejeita caminhos fora dessas
declarações e a validação offline rejeita arquivos sem consumidor ou componente.
Pesquisar ou avaliar um recurso não o torna parte do x86QW.

`maintenance/inventory/component-releases.json` mantém a camada de atualização separada: versão
atual, estratégia, upstream, artefatos consumidos e hashes. Assim uma release
como KTX pode avançar sem renomear ou reconstruir componentes alheios.

Todo conteúdo canônico fica sob `dist/`: `clients/ezquake/stable/` contém as
releases oficiais, `clients/ezquake/nightly/` contém os snapshots de
desenvolvimento, `distributions/nquake/` contém somente o conteúdo incorporado
da distribuição de referência, `mods/` mantém upstreams e ajustes próprios e
`game-data/id1/` mantém os PAKs registrados. ZIPs derivados
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
exigem uma definição revisada em `add` e `publish` continua explícito. Um adaptador
deve consumir somente arquivos declarados, verificar hashes internos e produzir
um novo pacote imutável. Cada mod escolhe uma composição verificável: merge de
arquivos para KTX, snapshot integral para Final Arena, substituição completa
para Pro-X, assets de referência com gamecode atualizado para Team Fortress e
pacote independente para TD2. As harmonizações x86QW são sempre aplicadas por
último. No KTX, toda divergência compartilhada precisa constar, com hashes e
resolução, em `dist/mods/ktx/1.47/x86qw/policy/merge-policy.json`; em Team Fortress, o
builder retira do PAK montado o gamecode 2.8 e a mídia estritamente duplicada
antes de adicionar o `qwprogs.dat` 2.9 extraído do ZIP original. Uma mudança não
declarada interrompe o build.

## Fluxo de publicação

```text
fonte fixada -> dist/ -> Git LFS -> catálogo -> instalador de desenvolvimento
                     -> maintenance/build/packages/ -> mirrors -> instalador público
CLI + catálogos runtime mínimos -> bundle do instalador -> GitHub/GitLab -> bootstrap curl/PowerShell
dist/game-data/id1 -> pacote x86qw-core-id1 -> GitHub/GitLab -> instalação inicial
```

As receitas versionadas ficam em `maintenance/recipes/`. O gerenciador valida
origem, tamanho, SHA-256 e membros mínimos. Atualizações são montadas em staging
e somente substituem a árvore canônica quando catálogo, inventários, receitas e
payloads formam um estado coerente.

O Worker serve o site e o catálogo. O bundle público contém `x86qw.pyz`, os
launchers, `installer.json` e a ponte mínima que permite à CLI 0.1.3 iniciar o
zipapp. A ponte só existe na extração temporária; o zipapp incorpora um manifesto runtime mínimo:
não leva PAKs, pacotes de mods, fontes, gamecodes
nem os inventários de desenvolvimento. Para componentes, o instalador valida e
materializa as fontes em `dist/` quando presentes; caso contrário, baixa o ZIP
derivado de uma URL registrada. Nos dois caminhos, gera o mesmo inventário de
arquivos gerenciados antes de alterar `quake-world/`.

Depois de qualquer alteração de componentes, o instalador gera `qw/pak.lst`
com uma ordem determinística, registra o arquivo em recibo próprio e rejeita
divergências na verificação. Os launchers sempre passam `-nohome`, evitando que
um diretório `~/.ezquake` externo sobreponha a distribuição autocontida.

## Segurança e recuperação

- workflows de pull request usam somente `contents: read` e não recebem secrets;
- a matriz Linux, macOS e Windows bloqueia a etapa manual de release;
- toda URL de artefato usa HTTPS;
- nomes de arquivo não podem conter caminhos;
- nenhum pacote é aceito sem tamanho e SHA-256;
- versões publicadas não são substituídas, apenas descontinuadas no catálogo;
- GitHub e GitLab hospedam o mesmo repositório com Git LFS; os registries são
  canais adicionais de entrega. R2 não faz parte da arquitetura atual.
- senhas de serviço podem vir de prompt sem eco ou arquivo privado e nunca
  entram na linha de comando dos filhos;
- preflight de portas ocorre antes do primeiro processo; readiness e rollback
  encerram startups parciais;
- `.x86qw/sessions/` registra journals privados e remove após crash somente
  arquivos criados pela sessão cujo hash ainda coincide.
