# KTX 1.47 no x86QW

## Camadas

1. nQuake fornece a seleção histórica e os recursos exclusivos ainda úteis.
2. KTX 1.47 substitui os arquivos compartilhados pelo QVM, mapa de símbolos, configurações, bots, LOCs e recursos oficiais.
3. x86QW aplica somente integração e perfil local.

## Alterações x86QW

- mantém o QVM oficial sem recompilação nem alteração de gameplay;
- configura o servidor local para `sv_progtype 2`;
- fornece binds, ajuda e arquivo pessoal separados da configuração upstream;
- preserva a ordem determinística de pacotes.

## Pendências conhecidas

- o launcher ainda precisa definir o mapa escolhido como `k_defmap` antes do primeiro frame para evitar a troca automática para `dm3` e o aviso de prespawn;
- `1.48-dev` é desenvolvimento no master, não uma release promovida.

## Deliberadamente não alterado

- nenhum modo, votação, arma, regra, bot ou mapa foi criado;
- o menu de modos sugerido na auditoria foi descartado por ser recurso novo.
