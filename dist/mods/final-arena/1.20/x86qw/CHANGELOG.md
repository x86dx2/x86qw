# Final Arena 1.20 no x86QW

## Original reconstruído

- `upstream/fasrv12.zip`: servidor oficial 1.20, 278932 bytes, SHA-256 `a250816fc3ffa461e21fc0c85cb286751b2bb22de2fd7d7bc42add7d0fd6ca70`;
- `upstream/farena12.zip`: cliente oficial 1.20, 7018988 bytes, SHA-256 `88dac7c3b7982deaddd3970a5c3293eeee8a30e18ddf9fd404c1504c6ab519dd`;
- `source/qwrasrc12.zip`: fonte QuakeWorld oficial extraída do servidor, 70227 bytes, SHA-256 `b83684390e6c68e2cdb39e9d22312df798b4c4b27ff3d816f75cfb27a7f67de7`.

Os 93 membros do `pak0.pak` oficial foram comparados com o conteúdo nQuake e estão byte a byte preservados. O `qwprogs.dat` do nQuake é uma derivação posterior atribuída a Rakk; sua fonte pública não foi localizada.

## Alterações x86QW

- mantém o gamecode Rakk já usado pelo nQuake para não perder votação, mochilas, airgib e estatísticas sem fonte equivalente;
- separa perfil de cliente, configuração de servidor e arquivo pessoal;
- inicia o diretório do mod uma única vez com `-game` e publica `*gamedir`
  separadamente, sem recarregar o filesystem durante o startup;
- expõe controles e ajuda sem alterar as regras do mod.

## Lacunas conhecidas

- a fonte oficial 1.20 permite reconstruir a base, mas não a derivação Rakk;
- `sprites/s_aball.spr` aparece apenas em `amtest.qc`, uma entidade de teste, e não existe no cliente oficial.

## Validação de runtime

- 30/07/2026: ezQuake 3.6.9 e nightly `20260616-101233_a86996a` em `23ar-a`;
- gamecode Rakk carregado, mapa correto aberto e jogador entrou na fila;
- nenhum arquivo obrigatório ausente, comando bloqueado ou erro do mod.

## Correções encaminhadas ao cliente

- o falso erro de QVM antes do fallback PR1 foi enviado ao ezQuake no
  [PR #1149](https://github.com/QW-Group/ezquake-source/pull/1149);
- o ruído de registros duplicados após `vid_restart` foi enviado separadamente
  no [PR #1150](https://github.com/QW-Group/ezquake-source/pull/1150).

## Deliberadamente não alterado

- nenhuma alteração Rakk foi reimplementada por inferência;
- nenhum sprite, mapa ou modo novo foi adicionado.
