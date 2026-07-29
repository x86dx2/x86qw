x86QW
=====

x86QW e uma distribuicao QuakeWorld autocontida, baseada no ezQuake e nos
componentes selecionados do nQuake, com mods e configuracoes mantidos pelo
projeto.

INICIAR
-------

Use o launcher da raiz do projeto:

  ./x86qw play

Ele lista apenas os mods instalados, permite escolher o mapa e inicia o
ezQuake no gamedir correto. Cada mod mostra seus controles ao carregar.

CONFIGURACOES PESSOAIS
----------------------

As atualizacoes preservam os arquivos abaixo:

  qw/x86qw-user.cfg
  qw/x86qw-ktx-user.cfg
  arena/x86qw-arena-user.cfg
  prox/x86qw-prox-user.cfg
  fortress/x86qw-fortress-user.cfg
  td2/x86qw-td2-user.cfg

Coloque customizacoes globais em qw/x86qw-user.cfg e ajustes de cada mod no
arquivo correspondente. Nao edite autoexec.cfg ou os perfis x86qw-*.cfg,
pois eles sao gerenciados pela distribuicao.

MANUTENCAO
----------

  ./x86qw verify
  ./x86qw update
  ./x86qw upgrade
  ./x86qw cleanup
  ./x86qw uninstall
  ./x86qw uninstall --purge

O cleanup remove caches regeneraveis. Downloads de servidores e dados
pessoais so sao removidos quando solicitados explicitamente.

Os PAKs registrados ficam em id1/ e sao preservados por atualizacoes e pela
desinstalacao comum. O modo uninstall --purge remove toda a instalacao.

Documentacao do ezQuake: https://ezquake.com/docs/
Projeto x86QW: https://x86qw.x86.com.br/
