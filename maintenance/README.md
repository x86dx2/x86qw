# Manutencao da distribuicao

Este diretorio e o plano de controle do `dist/`. Ele nao e uma segunda copia do
produto: inventarios e receitas explicam como montar a arvore canonica, os
o binario `x86qw-content` (codigo em `go/cmd/x86qw-content`) executa o processo e `build/` recebe somente derivados
temporarios.

## Fonte unica de operacao

Use apenas o `x86qw-content` (`go -C go run ./cmd/x86qw-content ...` ou o
binario compilado), que encontra o checkout a partir do diretorio atual:

```sh
x86qw-content check
x86qw-content update
x86qw-content add caminho/change.json
x86qw-content verify
x86qw-content build
x86qw-content publish
x86qw-content commit --push
```

- `check [COMPONENTE...]`: consulta os upstreams declarados sem escrever no
  projeto, componente por componente; retorna 2 quando encontra novidade, 1
  quando nada mudou mas algum upstream nao pôde ser consultado e 0 quando
  tudo está atual;
- `update [COMPONENTE...]`: sincroniza somente bytes stable/nightly e nQuake
  cuja identidade já foi revisada, exige Git limpo antes da mutação, usa
  staging, reconcilia arquivos antigos, atualiza os metadados dependentes,
  valida e troca a árvore de forma transacional;
- `add`: incorpora um novo pacote, add-on ou configuracao a partir de uma
  definicao local revisada;
- `verify`: valida catalogo, receitas, componentes, hashes, estrutura, TUF e Git LFS;
- `build`: valida os binarios stable e gera 21 ZIPs de componentes mais o
  pacote obrigatório `x86qw-core-id1` em `maintenance/build/packages/`;
- `publish`: publica/verifica GitHub Releases e GitLab Generic Packages nos
  repositórios `x86qw`, que são os únicos destinos da distribuição.
  Pacotes de conteúdo nunca recebem o selo Latest, que pertence à release da
  CLI;
- `commit`: adiciona ao Git somente `dist/`, inventarios, receitas e o catalogo.

`update --commit --push` existe para automacao, mas a rotina recomendada e
separar revisao e publicacao:

```sh
x86qw-content check
x86qw-content update --dry-run
x86qw-content update
x86qw-content verify
git diff --stat
x86qw-content commit --push
# após revisão, checks verdes e aprovação separada:
x86qw-content publish
```

O catálogo que `update`, `add` ou `build --register` mudou só vale depois de
assinado: rode `go -C go run ./cmd/x86qw-release sign` e `x86qw-content verify`
antes do PR. O merge publica o site; o passo a passo está no
[runbook de release](../docs/runbooks/release.md#publicar-conteúdo).

As comparacoes com upstreams publicos usam a API HTTPS oficial do GitHub e URLs
publicas de releases e downloads. `check` e `update` nao exigem conta, token ou
autenticacao no GitHub. Credenciais sao necessarias apenas no comando
`publish`, porque ele grava os artefatos nos mirrors do projeto.

## Fronteira de bytes remotos

`go/internal/download` é a única fronteira de manutenção e da CLI para receber
bytes HTTP persistentes. Um artefato exige HTTPS, destino, tamanho, SHA-256,
deadline e retry; metadados dinâmicos usam leitura limitada e nunca gravam
payload gerenciado. Não existe contrato para promover conteúdo não fixado: o
SHA-256 observado na própria transferência não pode se tornar a referência que
valida esses mesmos bytes.

## Atualizacoes independentes

Cada componente da distribuição (`ezquake`, `ktx`, `mvdsv`, `nquake-maps`...)
é consultado e atualizado por conta própria:

```sh
x86qw-content check                 # todos
x86qw-content check mvdsv ktx       # só esses
x86qw-content update --dry-run mvdsv
x86qw-content update mvdsv
```

- um upstream fora do ar (HTTP 429, timeout) aparece como
  `[INDISPONIVEL] componente: motivo` e não impede a consulta dos demais;
- sem seleção, `update` pula (`[PULADO]`) o que estiver indisponível ou exigir
  definição revisada e atualiza o resto; com seleção explícita, o componente
  pedido que não puder ser atualizado faz o comando falhar;
- componente fora da seleção ou pulado mantém exatamente os arquivos que o
  manifesto registra, sem mudar nem sumir.

Os 17 componentes que consomem arquivos do nQuake (os 13 do snapshot, os
overlays Final Arena e Team Fortress, o `ktx.pk3` do KTX e os arquivos do
Pro-X) fixam cada um sua própria revisão em `reference_revision`
(`component-releases.json`). Os arquivos consumidos ficam todos numa única
árvore, `dist/distributions/nquake/`, com o mesmo layout do repositório
nQuake; a revisão de cada arquivo é a do seu dono e aparece na URL registrada
em `dist/manifest.json` (`verify` exige essa correspondência).

O nQuake é a base da distribuição. `maintenance/inventory/nquake.json`
registra todos os arquivos da última revisão revisada (caminho, tamanho,
SHA-256 e hash Git) e, em `unused`, o motivo de cada parte que a distribuição
não usa. Todo arquivo do nQuake é consumido por um componente (fonte
`reference` em `components.json`) ou declarado não usado; `verify` exige essa
classificação completa e sem sobreposição.

Quando o nQuake anda:

- `check` lê a árvore Git e o `manifest.json` que o nQuake publica (SHA-256 de
  cada arquivo, conferido contra a árvore) e marca para atualização somente os
  componentes cujos arquivos consumidos mudaram; os demais continuam na
  revisão que já fixam. Mudanças em partes não usadas aparecem como
  `[NQUAKE] nao usado pela distro, <parte>: N novo(s)...` e um arquivo que
  ninguém classificou aparece como `[CLASSIFICAR]`;
- `update` leva cada componente afetado à revisão nova sozinho: baixa só os
  arquivos que mudaram (os iguais são reaproveitados do `dist`), confere
  SHA-256 e hash Git, troca a revisão na versão (ou incrementa `+x86qw.N`
  quando a versão não carrega a revisão) e refixa as customizações x86QW
  (`text_replacements`, `archive_text_replacements`) cujo texto ainda se
  aplica ao arquivo novo. Uma customização que não se aplica mais, ou a camada
  de composição do `ktx.pk3`, para apenas aquele componente
  (`[PULADO]`/`[REVISAR]`) para revisão;
- sem seleção, `update` também avança `nquake.json` para a revisão nova, desde
  que todo arquivo esteja classificado.

Para adotar uma parte hoje não usada, crie o componente com `add` e remova a
regra correspondente de `unused`.

O snapshot nQuake já registrado pode ser reparado mecanicamente porque caminho,
URL, tamanho e SHA-256 estão fixados no manifesto. Uma revisão nQuake nova, um
novo stable/nightly ou uma release nova de KTX, TD2 ou outro mod precisa de
intake revisado: membros, identidade, compatibilidade, versão e customizações
não podem ser inferidos da própria transferência. O gerenciador detecta a
novidade, marca `review_required` e interrompe `update` antes de confirmação,
staging ou download, orientando o uso de `add`; ele nunca sobrescreve uma versão
publicada.

O KTX e montado em tres camadas: `gpl/qw/ktx.pk3` do snapshot nQuake, recursos
e QVM da release oficial e, por ultimo, ajustes x86QW. A politica em
`dist/mods/ktx/<versao>/x86qw/policy/merge-policy.json` preserva arquivos exclusivos e
registra cada conflito compartilhado com os hashes das duas origens. Qualquer
conflito novo ou alterado interrompe o build para revisao humana.

A convenção de `source/`, `upstream/` e `x86qw/`, incluindo a assimetria
intencional entre os runtimes de QTV e QWFWD, está documentada em
[`docs/components.md`](docs/components.md#layout-dos-serviços-versionados).

## Definicao de inclusao

`add` aceita um JSON temporario com identidade `distribution-change`. O arquivo
nao precisa ser versionado: o resultado permanente e registrado em `dist/`, no
BOM, no inventario de releases e no catalogo.

`add --dry-run` valida a mesma identidade de entrada antes de qualquer rede ou
mutacao: destinos, fontes locais, HTTPS, tamanho positivo e SHA-256 previamente
revisado, metadados gerenciados, componente, perfil e pacote. A simulacao nunca
imprime a definicao bruta; URLs aparecem somente com origem redigida, sem
credenciais, caminho, query ou fragmento.

Um remoto novo precisa corresponder exatamente a pelo menos uma autoridade:
artefato de uma release proposta, fonte preservada no registro de upstreams,
pacote público proposto ou referência nQuake fixada. Namespace de
`distribution_component`, consumidor operacional e identidade lógica de
`package` precisam concordar com essa autoridade. Para ezQuake, o caminho é
derivado de
`clients/ezquake/<canal>/<versão>/<plataforma>-<arquitetura>/<arquivo>` e deve
coincidir com componente, canal, versão, plataforma, arquitetura e nome do
pacote proposto.

```json
{
  "format": 1,
  "project": "x86qw",
  "kind": "distribution-change",
  "files": [
    {
      "url": "https://arenacamper.ddns.net/repo/quakeworld/quakeworld-TD2.22QW-server_PTBR.tar.gz",
      "destination": "dist/mods/td2/2.22/source/quakeworld-TD2.22QW-server_PTBR.tar.gz",
      "size": 567029,
      "sha256": "b0b7632debe931e435008df939bade3791b5d21abfbb66f828790f1996beca93",
      "managed": true,
      "distribution_component": "td2",
      "consumer": "development:td2",
      "package": "total-destruction-2"
    }
  ]
}
```

O exemplo usa uma identidade canônica já inventariada para tornar o contrato
executável, e não apenas ilustrativo. Um arquivo usa `source` relativo à
definição ou `url` HTTPS, nunca ambos. Toda URL persistente exige `managed:
true`, não aceita credenciais, fragmento ou query e precisa corresponder
exatamente a uma das autoridades descritas acima. Um
caminho remoto que já existe no manifesto é imutável: URL, tamanho, SHA-256,
owner, consumer e package não podem mudar no lugar. A CLI entra apenas pela
release `x86qw-cli`, nunca por `add`.

Levar componentes a uma revisão nova do nQuake usa uma transição explícita e
resistente a plano obsoleto na mesma definição, que nomeia cada componente
movido e a revisão que ele deixa:

```json
{
  "reference": {
    "repository": "https://github.com/nQuake/distfiles",
    "revision": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "components": {
      "x86qw-client-bootstrap": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    }
  }
}
```

A revisão anterior de cada componente precisa ser exatamente a inventariada no
momento da aplicação, o repositório não pode mudar e cada URL é derivada da
origem GitHub canônica, da nova revisão e do caminho declarado. A lista
`files` precisa trazer todos os arquivos consumidos pelos componentes movidos
na revisão nova (com `package` igual ao componente); a versão de cada um
passa a carregar a revisão nova. Os arquivos que eles deixam só são removidos
no workspace transacional, depois da validação do plano, e os demais
componentes não mudam de revisão nem de bytes.

Fontes locais precisam estar declaradas exatamente em `project_sources`,
`project_inputs`, `project_overrides` ou `project_archive_overrides` do BOM
proposto. A cópia usa temporário exclusivo, modo `0600` no POSIX, hash durante
streaming, `fsync` e `replace` atômico; assim, o staging por hardlink não altera
o `dist/` vivo e uma mudança da fonte depois do plano é rejeitada. No Windows,
esse temporário herda a ACL da pasta do checkout. Destinos usam nomes
portáveis e colisões por caixa ou normalização Unicode e symlinks intermediários
são rejeitados.
O contrato comum usa `/` somente como separador, rejeita caracteres inválidos e
nomes de dispositivo Win32 (inclusive `CONIN$`, `CONOUT$`, `COM¹` e `LPT¹`),
componentes terminados em ponto/espaço e limita o caminho relativo completo a
240 unidades UTF-16. Essa margem acomoda o prefixo usual do checkout do runner
Windows sob o limite Win32 clássico; checkouts locais ainda devem usar uma raiz
curta.

Para criar ou substituir um componente, a mesma definição também precisa trazer
os objetos completos `component` e `release`, `replace: true` quando aplicável e
a lista `profiles`. O `--dry-run` monta e valida esses inventários em memória
antes de qualquer rede. Ao alterar um perfil, `add` preserva automaticamente sua
assinatura anterior e registra a nova em `inventory/components.json`. Esse
histórico permite que `x86qw upgrade` reconheça instalações que pularam
releases intermediárias.

Para atualizar um componente existente, envie o objeto completo com
`"replace": true` e uma nova release imutavel. Configuracoes x86QW devem ficar
em um subdiretorio `x86qw/` do componente e ser declaradas como
`project_sources`; a copia original do upstream permanece intacta.

## Conteudo dos subdiretorios

```text
inventory/component-policy.json    namespaces aceitos no dist
inventory/components.json          BOM, perfis, dependencias e destinos
inventory/component-releases.json  versoes, estrategias, artefatos e hashes
inventory/upstreams.json           versoes, fontes preservadas e receitas de build
recipes/                            receitas dos clientes stable preservados
recipes/sources/                    instrucoes de compilacao por upstream
build/                              artefatos derivados ignorados pelo Git
docs/                               componentes e proveniencia
```

Nao existe mais um inventario-resumo paralelo: `dist/manifest.json` e a unica
fonte de verdade para os bytes upstream efetivamente preservados.
