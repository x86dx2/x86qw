# Pro-X 1.1 no x86QW

## Original preservado

- `upstream/prox_11.zip`: pacote público completo 1.1, 9095898 bytes, SHA-256 `8f68563cd5abec1a1fbf2b1ec2288a96693c7bad12b2ab6ce977451c1ee72010`;
- contém gamecode, mídia, seis mapas `proxmap1` a `proxmap6` e o ENT opcional de `q1_q3dm13`.

O autor informou publicamente que não liberaria a fonte. O x86QW preserva o binário original e não tenta decompilá-lo ou reimplementá-lo.

## Alterações x86QW

- `runtime/maps/proxmap1.ent` reproduz exatamente o lump de entidades do BSP original, removendo somente quatro chaves obsoletas `"noname" "1"`;
- a correção elimina quatro mensagens `noname is not a field` sem mudar entidades, arena, geometria ou gameplay;
- força a leitura de entidades externas somente ao iniciar o Pro-X, inclusive se
  uma configuração pessoal antiga deixou `sv_loadentfiles` desativado;
- inicia o diretório do mod uma única vez com `-game` e publica `*gamedir`
  separadamente, sem recarregar o filesystem durante o startup;
- separa configurações de cliente, servidor e usuário.

## Dependências opcionais

- `q1edge.bsp` e `q1_q3dm13.bsp` são arenas externas condicionais e não são necessárias para os seis mapas do pacote;
- esses mapas não foram incorporados automaticamente.

## Validação de runtime

- 30/07/2026: ezQuake 3.6.9 e nightly `20260616-101233_a86996a` em `proxmap1`;
- gamecode 1.1 carregado, jogador entrou e as quatro mensagens `noname is not a field` desapareceram;
- nenhum arquivo obrigatório ausente, comando bloqueado ou erro do mod.

## Deliberadamente não alterado

- nenhum código, regra, mapa ou conteúdo visual foi criado.
