# Instalador x86QW

As interfaces públicas são `install.sh`/`install.ps1` para instalar e `x86qw.sh`
para jogar ou manter uma instalação. Este diretório é a fonte canônica de tudo
que o projeto distribui como instalador. A raiz separa executáveis,
documentação e pacotes imutáveis:

- `bin/install.sh`: bootstrap público para macOS e Linux;
- `bin/install.ps1`: bootstrap público para Windows;
- `bin/x86qw.sh`: launcher permanente para macOS e Linux;
- `bin/x86qw.cmd`: launcher permanente para Windows;
- `assets/x86qw.svg`: ícone mestre do projeto e suas derivações nativas;
- `bin/manager.py`: gerenciador principal de instalação e manutenção;
- `bin/gameplay.py`: implementação interna de gameplay, jogos e modos KTX;
- `bin/services.py`: execução segura de MVDSV, QTV e QWFWD em primeiro plano;
- `docs/installer.md`: manual completo;
- `packages/<versão>/`: histórico de bundles públicos imutáveis;
- `packages/latest`: link simbólico relativo para a versão corrente.

O link `latest` seleciona a versão corrente dentro do Git sem duplicar o bundle.
O catálogo oficial começa em `0.1.0`. A baseline-fonte no Git é `1.0.13`
(`packages/latest`). A `1.0.12`, a `1.0.11`, a `1.0.10`, a `1.0.9`, a `1.0.8`, a `1.0.7`, a `1.0.6`, a `1.0.5`, a `1.0.4`, a `1.0.3`, a `1.0.2`, a `0.7.13` e a release GitHub `1.0.0` owner-only
permanecem históricas e imutáveis. A versão de desenvolvimento é sempre lida
de `VERSION`.
Os bootstraps públicos continuam gravando versão e SHA-256 concretos: links
simbólicos não são portáveis nos mirrors HTTP nem substituem a verificação de
integridade feita no computador do jogador.

No GitHub, o selo **Latest** pertence exclusivamente ao bundle corrente do
instalador. Releases de ezQuake, dados-base e componentes usam a família de
títulos `x86QW Content · ...` e nunca alteram esse selo.

Os dois launchers permanentes entram no bundle multiplataforma, mas somente o
launcher nativo é instalado: `x86qw.sh` no macOS/Linux e `x86qw.cmd` no
Windows. No POSIX, `~/.local/bin/x86qw` aponta para o launcher instalado. No
Windows, o instalador cria atalhos no Menu Iniciar e na Área de Trabalho usando
o ICO embutido. Upgrade, rollback e desinstalação tratam essas integrações como
parte da geração da CLI.

O bundle é deliberadamente enxuto: contém `x86qw.pyz`, os dois launchers e
`installer.json`. Uma ponte mínima formada por `dist/installer/bin/manager.py`
e `_x86qw/installer.json` permite que instaladores antigos entreguem o controle ao zipapp;
ela existe apenas na extração temporária e nunca é instalada. O zipapp reúne a
implementação da CLI, projeções mínimas de runtimes, jogos, capacidades e
compatibilidade, além do manifesto de modos KTX. PAKs, pacotes de mods, configurações,
gamecodes, fontes e inventários de desenvolvimento não entram nele. A instalação
obtém cada payload pelo pacote independente registrado no catálogo; os PAKs
obrigatórios usam o pacote `x86qw-core-id1`.
Ao atualizar uma instalação antiga, a pasta interna da CLI é substituída como
uma unidade, removendo as cópias legadas de PAKs, configurações e gamecodes.
No computador do jogador, `.x86qw/cli/` contém `x86qw.pyz`, `x86qw.ico` e seu
`receipt`; os caminhos de desenvolvimento `dist/` e `maintenance/` nunca são
materializados. Clientes e componentes mantêm seus metadados em contextos
próprios sob `.x86qw/clients/` e `.x86qw/components/`.

`site/public/install.sh` e `site/public/install.ps1` são projeções byte a byte
das fontes em `bin/` para publicação pelo Worker. O construtor atualiza primeiro
os arquivos canônicos e depois sincroniza as duas cópias públicas. Os testes
rejeitam qualquer divergência.

Validacao isolada:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest maintenance.tests.test_installer maintenance.tests.test_modern_components -v
```
