# Instalador x86QW

As interfaces públicas são `install.sh`/`install.ps1` para instalar e `x86qw`
para jogar ou manter uma instalação. Este diretório é a fonte canônica de tudo
que o projeto distribui como instalador. A raiz separa executáveis,
documentação e pacotes imutáveis:

- `bin/install.sh`: bootstrap público para macOS e Linux;
- `bin/install.ps1`: bootstrap público para Windows;
- `bin/x86qw`: launcher permanente para macOS e Linux;
- `bin/x86qw.cmd`: launcher permanente para Windows;
- `bin/manager.py`: gerenciador principal de instalação e manutenção;
- `bin/gameplay.py`: implementação interna de gameplay e seleção de mods;
- `docs/installer.md`: manual completo;
- `packages/<versão>/`: histórico de bundles públicos imutáveis;
- `packages/latest`: link simbólico relativo para a versão corrente.

O link `latest` seleciona a versão corrente dentro do Git sem duplicar o bundle.
O catálogo histórico oficial começa na versão `1.0.20`; versões anteriores não
fazem parte desta taxonomia e não são preservadas pela manutenção atual.
Os bootstraps públicos continuam gravando versão e SHA-256 concretos: links
simbólicos não são portáveis nos mirrors HTTP nem substituem a verificação de
integridade feita no computador do jogador.

Os launchers permanentes são arquivos normais deste diretório e entram no
bundle. A instalação apenas copia seus bytes para a raiz escolhida; nenhum
script de launcher é montado ou escrito a partir de strings em runtime.

`site/public/install.sh` e `site/public/install.ps1` são projeções byte a byte
das fontes em `bin/` para publicação pelo Worker. O construtor atualiza primeiro
os arquivos canônicos e depois sincroniza as duas cópias públicas. Os testes
rejeitam qualquer divergência.

Validacao isolada:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest maintenance.tests.test_installer maintenance.tests.test_modern_components -v
```
