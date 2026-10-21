# Suporte do x86QW

## Comece pelo diagnóstico local

Em qualquer pasta, com o comando `x86qw` no `PATH` (ou `./x86qw.sh` na pasta da
instalação; `x86qw.cmd` no Windows):

```sh
x86qw version
x86qw verify
x86qw doctor
```

Se a verificação encontrar conteúdo gerenciado ausente ou incorreto, revise
primeiro o plano de reparo:

```sh
x86qw repair --dry-run
```

`x86qw doctor --bundle` grava um zip de diagnóstico, fora da instalação e com a
pasta pessoal trocada por `~`, para anexar a um relato depois de revisado.

## Onde pedir ajuda

- **Falha reproduzível ou regressão:** abra um [relatório de bug](https://github.com/x86dx2/x86qw/issues/new?template=bug-report.yml).
- **Ideia ou novo recurso:** use a [proposta de melhoria](https://github.com/x86dx2/x86qw/issues/new?template=feature-request.yml).
- **Vulnerabilidade:** não abra issue; siga a [política de segurança](SECURITY.md).
- **Instalação e uso da CLI:** consulte a [seção de instalação](../README.md#instalação), `x86qw help` e `x86qw COMANDO --help`.
- **Servidor, QTV ou QWFWD:** consulte o [guia de hospedagem](../docs/HOSTING.md).

## Informações úteis em um diagnóstico

Inclua sistema operacional e arquitetura, versão do x86QW, perfil instalado, comando exato, saída completa sem segredos e o resultado de `verify`. Diga também se a instalação é nova ou atualizada e se o problema ocorre com stable, nightly ou ambos.

Remova senhas, tokens, endereços privados e caminhos pessoais antes de publicar logs. Não anexe o conteúdo de `.x86qw/sessions/` nem `.x86qw/setup/` sem revisar cada arquivo.

## Limites

O projeto oferece suporte ao empacotamento, à CLI e às customizações x86QW. Problemas confirmados no software upstream podem ser encaminhados ao mantenedor correspondente com um caso reproduzível. A matriz de suporte declarada fica no [README](../README.md#compatibilidade).
