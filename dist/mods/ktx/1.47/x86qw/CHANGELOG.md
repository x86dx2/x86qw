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
- deixa `k_defmode` sob controle do launcher e inicia diretamente no usermode
  selecionado, sem aplicar primeiro o padrão 4on4;
- declara em `modes.json` Duel, 2on2, 4on4, FFA, Clan Arena, HoonyMode,
  Midair, Race e Practice, com mapas e entradas compatíveis com KTX 1.47;
- ativa Midair, Race e Practice por perfis estáticos de entrada única, somente
  depois que o KTX confirma a conexão local; a remoção imediata do alias evita
  alternância ou recarga em ciclo;
- mantém CTF fora do launcher enquanto a distribuição não possuir um mapa ou
  ENT CTF curado com as duas bandeiras;
- reaplica o mapa de teclas competitivo do nQuake ao voltar de outros mods,
  incluindo quick weapons, mensagens de equipe, timers e comandos da partida;
- mantém como única extensão de tecla o `F10`, vazio no nQuake, para ajuda sob
  demanda, e continua carregando o arquivo pessoal por último;
- mantém os exemplos do arquivo pessoal independentes de aliases internos;
- deixa o console limpo ao iniciar e mantém a ajuda em `F10` somente sob demanda;
- registra o preset escolhido no launcher e oferece `ktx_mode` para confirmá-lo
  novamente no console;
- força a conexão local inicial como jogador; isso evita o caminho de espectador
  incompatível entre o QVM atual e o servidor integrado do ezQuake 3.6.9;
- aplica os valores de timing e salto do KTX somente ao KTX e restaura o padrão
  nQuake ao abrir outro mod, impedindo o vazamento pelo `config.cfg` pessoal;
- preserva a ordem determinística de pacotes.

## Pendências conhecidas

- `1.48-dev` é desenvolvimento no master, não uma release promovida.

## Validação de runtime

- 30/07/2026: ezQuake 3.6.9 e nightly `20260616-101233_a86996a` em `dm6`;
- houve uma única carga do QVM 1.47, o mapa escolhido foi preservado e o jogador entrou;
- nenhum `SV_PreSpawn_f from different level`, erro de QVM ou comando desconhecido.
- 30/07/2026: Duel iniciou diretamente em `1on1`; Midair foi ativado uma vez
  em `povdmm4`; Race carregou a rota de `dm6` sem ciclo de alternância; Practice
  confirmou o servidor destravado. Todos os processos encerraram sem órfãos.
- 30/07/2026: dois crashes reproduzidos no ezQuake 3.6.9 foram identificados
  como `SIGSEGV` em `SV_SpawnSpectator`, ao receber `classname` nulo do runtime
  QVM. A conexão local explícita com `spectator 0` permaneceu ativa no mesmo
  cenário de Duel que encerrava em aproximadamente oito segundos.

## Correção encaminhada ao cliente

- o ruído de registros duplicados após `vid_restart` não pertence ao KTX; a
  correção genérica foi enviada ao ezQuake no
  [PR #1150](https://github.com/QW-Group/ezquake-source/pull/1150).

## Deliberadamente não alterado

- nenhum modo, votação, arma, regra, bot ou mapa foi criado no gamecode;
- o launcher apenas expõe usermodes e comandos já existentes no KTX 1.47.
