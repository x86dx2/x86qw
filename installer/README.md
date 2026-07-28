# Instalador

O executavel do usuario permanece em `../install-qw.py`, na raiz do repositorio.
Este diretorio guarda somente o contexto de manutencao do instalador:

- `docs/installer.md`: comportamento, plataformas, cache, recibos e macOS;
- `tests/`: regressao do instalador e dos componentes modernos.

Validacao isolada:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s installer/tests -v
```
