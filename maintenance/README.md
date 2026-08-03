# Manutencao da distribuicao

Este diretorio e o plano de controle do `dist/`. Ele nao e uma segunda copia do
produto: inventarios e receitas explicam como montar a arvore canonica, os
modulos internos executam o processo e `build/` recebe somente derivados
temporarios.

## Fonte unica de operacao

Use apenas o gerenciador da raiz do contexto:

```sh
./maintenance/manage.py check
./maintenance/manage.py update
./maintenance/manage.py add caminho/change.json
./maintenance/manage.py verify
./maintenance/manage.py build
./maintenance/manage.py publish
./maintenance/manage.py commit --push
```

- `check`: consulta os upstreams declarados sem escrever no projeto; retorna
  codigo 2 quando encontra novidade;
- `update`: sincroniza somente bytes stable/nightly e nQuake cuja identidade já
  foi revisada, exige Git limpo antes da mutação, usa staging, reconcilia
  arquivos antigos, atualiza os metadados dependentes, valida e troca a árvore
  de forma transacional;
- `add`: incorpora um novo pacote, add-on ou configuracao a partir de uma
  definicao local revisada;
- `verify`: valida catalogo, receitas, componentes, hashes, estrutura e testes;
- `build`: valida os binarios stable e gera 21 ZIPs de componentes mais o
  pacote obrigatório `x86qw-core-id1` em `maintenance/build/packages/`;
- `publish`: publica/verifica GitHub Releases e GitLab Generic Packages nos
  repositórios `x86qw`, que são os únicos destinos da distribuição.
  Somente o instalador corrente recebe o selo Latest; os demais artefatos são
  identificados como conteúdo;
- `commit`: adiciona ao Git somente `dist/`, inventarios, receitas e o catalogo.

As suítes executadas por `verify` exportam `X86QW_TEST_WINDOWED=1`. Portanto,
qualquer smoke que abra um cliente usa uma janela de `1280x720` e não captura o
monitor do desenvolvedor. O launcher também força `cfg_save_onquit 0`, impedindo
que o modo janela do teste seja gravado no `config.cfg` pessoal. Somente um teste
cujo objetivo declarado seja validar fullscreen deve remover essa variável no
próprio caso de teste.

`update --commit --push` existe para automacao, mas a rotina recomendada e
separar revisao e publicacao:

```sh
./maintenance/manage.py check
./maintenance/manage.py update --dry-run
./maintenance/manage.py update
./maintenance/manage.py verify
git diff --stat
./maintenance/manage.py commit --push
# após revisão, checks verdes e aprovação separada:
./maintenance/manage.py publish
```

As comparacoes com upstreams publicos usam a API HTTPS oficial do GitHub e URLs
publicas de releases e downloads. `check` e `update` nao exigem conta, token ou
autenticacao no GitHub. Credenciais sao necessarias apenas no comando
`publish`, porque ele grava os artefatos nos mirrors do projeto.

## Fronteira de bytes remotos

`maintenance/tools/downloader.py` é a única API Python do zipapp e da manutenção
para receber bytes HTTP. O bootstrap PowerShell incorpora uma projeção mínima
antes de o zipapp existir.
Um artefato persistente usa `PinnedArtifact` e exige HTTPS, destino, tamanho,
SHA-256, máximo, deadline e retry. `BoundedMetadata` serve somente a respostas
dinâmicas efêmeras e nunca grava payload gerenciado. Não existe contrato para
promover conteúdo não fixado: o SHA-256 observado na própria transferência não
pode se tornar a referência que valida esses mesmos bytes.

Descobertas HTTP possuem limite de 4 MiB e artefatos, de 512 MiB. A resolução
DNS da fronteira usa um subprocesso isolado e cancelável; conexões TCP,
redirects, headers, leituras, retries e backoff permanecem no mesmo deadline.
A coleta recebe uma janela dentro desse orçamento, sem prometer prazo absoluto
de reap pelo sistema operacional depois de `kill`.
Referências, commits e árvores recursivas do nQuake são consultados pela API oficial do
GitHub com `BoundedMetadata`, limite de 4 MiB e deadline compartilhado. Respostas
truncadas, schemas divergentes, tipos, hashes, tamanhos ou caminhos não portáveis
são rejeitados antes de gerar a lista de assets. Catálogo, manifesto,
inventários de releases e upstreams e intake usam o mesmo validador HTTPS; URLs
persistentes também rejeitam query. O `curl` de publicação envia bytes e não é
uma rota de ingestão; ele começa com `--disable`, exige HTTPS e não segue
redirects. Um novo consumidor Python de `urlopen` ou `urlretrieve` falha na
suíte estática. Limite e TLS não autenticam metadados dinâmicos.

O contrato completo está no
[`ADR 0001`](../docs/adr/0001-fronteira-limitada-de-bytes-remotos.md) e sua
rastreabilidade, na [issue #45](https://github.com/x86dx2/x86qw/issues/45).

## Atualizacoes independentes

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
owner, consumer e package não podem mudar no lugar. Novos bundles do instalador
entram apenas pelo fluxo de release imutável, nunca por `add`.

Uma revisão nova do snapshot nQuake usa uma transição explícita e resistente a
plano obsoleto na mesma definição:

```json
{
  "reference": {
    "repository": "https://github.com/nQuake/distfiles",
    "previous_revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "revision": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  }
}
```

`previous_revision` precisa ser exatamente a revisão inventariada no momento da
aplicação, o repositório não pode mudar e cada URL do novo snapshot é derivada
da origem GitHub canônica, da nova revisão e do caminho declarado. A lista
`files` precisa formar o novo snapshot consumido completo; caso contrário, a
validação e a reconstrução dos pacotes falham dentro do workspace. O snapshot
anterior só é removido nesse workspace transacional, depois da validação do
plano, e o `dist/` vivo permanece intacto até a promoção final.

Fontes locais precisam estar declaradas exatamente em `project_sources`,
`project_inputs`, `project_overrides` ou `project_archive_overrides` do BOM
proposto. A cópia usa temporário exclusivo, modo `0600` no POSIX, hash durante
streaming, `fsync` e `replace` atômico; assim, o staging por hardlink não altera
o `dist/` vivo e uma mudança da fonte depois do plano é rejeitada. DACL privada
para esse temporário no Windows permanece no PR 4 e na
[issue #47](https://github.com/x86dx2/x86qw/issues/47). Destinos usam nomes
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
histórico permite que `./x86qw.sh upgrade` reconheça instalações que pularam
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
tools/                              implementacao interna importada por manage.py
tests/                              regressao exclusiva da distribuicao
build/                              artefatos derivados ignorados pelo Git
docs/                               componentes e proveniencia
```

Nao existe mais um inventario-resumo paralelo: `dist/manifest.json` e a unica
fonte de verdade para os bytes upstream efetivamente preservados.
