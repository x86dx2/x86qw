# KTX 1.47 no x86QW

## Camadas

1. nQuake fornece a seleção histórica e os recursos exclusivos ainda úteis.
2. KTX 1.47 substitui os arquivos compartilhados pelo QVM, mapa de símbolos, configurações, bots, LOCs e recursos oficiais.
3. x86QW aplica somente integração e perfil local.

## Alterações x86QW

- mantém o QVM oficial sem recompilação nem alteração de gameplay;
- configura o servidor local para `sv_progtype 2`;
- deixa `k_defmap` sob controle do launcher, evitando a troca automática para
  `dm3`, a segunda carga do QVM e o aviso `SV_PreSpawn_f from different level`;
- fornece binds, ajuda e arquivo pessoal separados da configuração upstream;
- preserva a ordem determinística de pacotes.

## Pendências conhecidas

- `1.48-dev` é desenvolvimento no master, não uma release promovida.

## Validação de runtime

- 30/07/2026: ezQuake 3.6.9 e nightly `20260616-101233_a86996a` em `dm6`;
- houve uma única carga do QVM 1.47, o mapa escolhido foi preservado e o jogador entrou;
- nenhum `SV_PreSpawn_f from different level`, erro de QVM ou comando desconhecido.

## Deliberadamente não alterado

- nenhum modo, votação, arma, regra, bot ou mapa foi criado;
- o menu de modos sugerido na auditoria foi descartado por ser recurso novo.
