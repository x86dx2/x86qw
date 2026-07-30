# Total Destruction 2 2.22 no x86QW

## Original preservado

- `source/quakeworld-TD2.22QW-server_PTBR.tar.gz`: distribuição completa original com fonte, manuais, gamecode, modelos e sons, 567029 bytes, SHA-256 `b0b7632debe931e435008df939bade3791b5d21abfbb66f828790f1996beca93`.

## Alterações x86QW

- restaura `saw_down.wav` copiando `saw.wav`, que é o único byte original verificável para a referência ausente;
- `source/0001-runtime-compatibility.patch` restaura o campo worldspawn padrão `wad`;
- remove `easyrecord`, `stop` e centenas de `wait`, deixando gravações sob controle do jogador;
- remove binds e `scr_centertime` remotos, já fornecidos pelo perfil local;
- remove alterações persistentes de gamma e mantém a duração visual da bomba de luz com os efeitos de entidade originais;
- remove o ramo morto de `monster_vomit` que chamava `vomitus/v_sight1.wav`, arquivo inexistente também no código-base Quake 1.06;
- inicia o diretório do mod uma única vez com `-game` e publica `*gamedir`
  separadamente, sem recarregar o filesystem durante o startup;
- `runtime/qwprogs.dat` foi recompilado com FTEQCC e promovido após smoke em stable e nightly.

## Lacunas conhecidas

- a lista de votação cita 30 mapas externos não incluídos na distribuição original preservada;
- o build produz 14 warnings históricos que ainda precisam ser classificados.

## Validação de runtime

- 30/07/2026: ezQuake 3.6.9 e nightly `20260616-101233_a86996a` em `dm6` com `temp1 65560`;
- jogador entrou, armas e efeitos carregaram e o `td2qw 2.22` foi preservado;
- nenhuma demo automática, `stop`, gamma persistente, bind remoto ou erro `wad`/`vomitus`.

## Correções encaminhadas ao cliente

- o falso erro de QVM antes do fallback PR1 foi enviado ao ezQuake no
  [PR #1149](https://github.com/QW-Group/ezquake-source/pull/1149);
- o ruído de registros duplicados após `vid_restart` foi enviado separadamente
  no [PR #1150](https://github.com/QW-Group/ezquake-source/pull/1150).

## Deliberadamente não alterado

- `temp1 65560` e as regras de armas, magias, runas e poderes foram mantidos;
- nenhum mapa externo, menu, modo, som ou mecânica foi adicionado.
