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
- `update`: exige Git limpo, sincroniza stable/nightly e a referencia nQuake em
  staging, reconcilia arquivos antigos, atualiza todos os metadados dependentes,
  valida e troca a arvore de forma transacional;
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

As comparacoes com upstreams publicos usam somente o protocolo Git e URLs
publicas de releases e downloads. `check` e `update` nao exigem conta, token ou
autenticacao no GitHub. Credenciais sao necessarias apenas no comando
`publish`, porque ele grava os artefatos nos mirrors do projeto.

## Fronteira de bytes remotos

`maintenance/tools/downloader.py` é a única API Python do zipapp e da manutenção
para receber bytes HTTP. O bootstrap PowerShell incorpora uma projeção mínima
antes de o zipapp existir.
Um artefato persistente usa `PinnedArtifact` e exige HTTPS, destino, tamanho,
SHA-256, máximo, deadline e retry. `BoundedMetadata` serve somente a respostas
dinâmicas efêmeras. `BoundedPayload` é uma exceção da manutenção: recebe em
staging limitado, exige identidade independente quando ela existe e, na falta
de digest oficial, permanece numa transação que só avança após confirmação,
validação e revisão do diff. O instalador público nunca usa essa exceção.

Descobertas HTTP possuem limite de 4 MiB e payloads, de 512 MiB. Git nativo
continua responsável por identidades e árvores, não por contornar o downloader
HTTP: seu adaptador exige HTTPS, neutraliza configuração herdada de transporte,
limita stdout a 32 MiB, stderr a 1 MiB e o workspace temporário a 128 MiB, usa
deadlines de 60 segundos para consultas e 300 segundos para árvores, e encerra
a árvore de processos. O `curl` de
publicação envia bytes e não é uma rota de ingestão. Um novo consumidor Python
de `urlopen` ou `urlretrieve` falha na suíte estática. Limite e TLS não autenticam
metadados dinâmicos.

O contrato completo está no
[`ADR 0001`](../docs/adr/0001-fronteira-limitada-de-bytes-remotos.md) e sua
rastreabilidade, na [issue #45](https://github.com/x86dx2/x86qw/issues/45).

## Atualizacoes independentes

O snapshot do nQuake pode ser atualizado mecanicamente porque cada caminho ja
tem dono no BOM. O mesmo nao vale para uma release nova de KTX, TD2 ou outro
mod: membros, compatibilidade, versao e customizacoes precisam de revisao. O
gerenciador detecta a novidade e interrompe `update` com a orientacao de usar
`add`; ele nunca sobrescreve uma versao publicada.

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

```json
{
  "format": 1,
  "project": "x86qw",
  "kind": "distribution-change",
  "files": [
    {
      "source": "client.cfg",
      "destination": "dist/mods/exemplo/1.0/x86qw/client.cfg"
    },
    {
      "url": "https://upstream.example/mod-1.0.zip",
      "destination": "dist/mods/exemplo/1.0/mod-1.0.zip",
      "size": 12345,
      "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      "managed": true,
      "distribution_component": "td2",
      "consumer": "install:exemplo",
      "package": "exemplo"
    }
  ],
  "component": {
    "id": "exemplo",
    "label": "Exemplo",
    "kind": "addon",
    "description": "Componente diretamente instalavel.",
    "requires": ["nquake-bootstrap"],
    "sources": [],
    "project_sources": []
  },
  "profiles": ["complete"],
  "release": {
    "version": "1.0",
    "strategy": "upstream-package",
    "freshness": "upstream-current"
  }
}
```

O exemplo e apenas a forma do contrato; a validacao completa exige seletores,
artefatos e metadados aceitos pelos inventarios reais. Um arquivo usa `source`
relativo a definicao ou `url` HTTPS, nunca ambos. Tamanho e SHA-256 declarados
sao conferidos antes de qualquer troca. Todos os destinos ficam sob `dist/`.
Ao alterar um perfil, `add` preserva automaticamente sua assinatura anterior e
registra a nova em `inventory/components.json`. Esse histórico permite que
`./x86qw.sh upgrade` reconheça instalações que pularam releases intermediárias.

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
