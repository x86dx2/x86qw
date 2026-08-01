# KTX 1.47 no x86QW

## Camadas

1. nQuake fornece a seleção histórica e os recursos exclusivos ainda úteis.
2. KTX 1.47 substitui os arquivos compartilhados por configurações, bots, LOCs e recursos oficiais.
3. x86QW aplica integração, perfil local e uma extensão QVM mínima e reproduzível para identidades Frogbot.

## Alterações x86QW

- recompila o QVM 1.47 com patches isolados que cacheiam identidades Frogbot
  e distribuem inclusões automáticas pelo número de equipes do usermode;
- fixa o metadado de compilação na data do tag 1.47, eliminando a variação de
  `__DATE__` e `__TIME__` entre reconstruções do mesmo QVM;
  quando não recebe um perfil, o sorteio e os nomes originais permanecem intactos;
- configura o servidor local para `sv_progtype 2`;
- deixa `k_defmap` sob controle do launcher, evitando a troca automática para
  `dm3`, a segunda carga do QVM e o aviso `SV_PreSpawn_f from different level`;
- deixa `k_defmode` sob controle do launcher e inicia diretamente no usermode
  selecionado, sem aplicar primeiro o padrão 4on4;
- declara em `catalog/modes.json` os 17 usermodes nativos do KTX 1.47 e sete variações
  oficiais: Midair, DMM4, Instagib, LGC, Rocket Arena, Race e Practice;
- ativa as sete variações por perfis estáticos de entrada única, somente
  depois que o KTX confirma a conexão local; a remoção imediata do alias evita
  alternância ou recarga em ciclo;
- expõe no launcher os bots Frogbot por quantidade ou preenchimento, habilidade
  1-20, equipe, arma e vida; habilita o subsistema antes do mapa e só aceita
  mapas com uma das 77 rotas oficiais empacotadas;
- declara a composição de equipes de cada modo no catálogo: `3on3` forma duas
  equipes de três, enquanto `2on2on2` forma três equipes de dois; modos com
  elenco fixo oferecem exatamente as vagas restantes e distribuem os bots para
  completar cada equipe;
- oferece identidades Frogbot em três perfis: padrão KTX sem customização, catálogo
  One Piece x86QW embaralhado por lançamento e lista pessoal preservada pelo
  instalador; mantém o prefixo `/` e deixa camisa/calça sob controle do KTX;
- filtra Race pelas 54 rotas oficiais e expõe corrida solo, simultânea ou em
  match, os três sistemas de pontuação, pacemaker e ocultação de corredores;
- habilita CTF com os seis ENTs oficiais da rotação KTX (`e2m2`, `e1m5`,
  `e1m3`, `e2m5`, `e1m4` e `e3m3`), cada um validado com uma bandeira por
  equipe, e configura o carregamento dos ENTs antes de abrir o mapa;
- expõe no CTF os cinco estilos de gancho, gancho desligado, runas, troca de
  equipes e spawn baseado na base; Race e CTF recusam bots como exige o QVM;
- ao iniciar KTX, aplica exclusivamente ao perfil KTX o mapa de teclas
  competitivo do nQuake, incluindo quick weapons, mensagens de equipe, timers
  e comandos da partida;
- reserva `F5`, `F6`, `F11` e, conforme o modo, `H`, `I`, `M`, `X` e `Z` para
  ações contextuais; `INS`, `DEL`, `HOME` e `END` gerenciam sessões Frogbot;
  carrega o arquivo pessoal ao fim do perfil e reaplica depois dele somente o
  bind universal `F12` para sair, preservando todos os demais controles;
- mantém os exemplos do arquivo pessoal independentes de aliases internos;
- substitui a mensagem genérica do nQuake pela ajuda contextual composta apenas
  por teclas realmente vinculadas;
- apresenta em `F10`, em blocos coloridos e multilinha, todos os controles do
  mapa nQuake usado pelo KTX, abrindo o console para que a referência completa
  permaneça visível;
- registra o preset escolhido no launcher e oferece `ktx_mode` para confirmá-lo
  novamente no console e ao final da ajuda de `F10`;
- imprime automaticamente o plano de teclas do modo assim que o mapa abre;
- mantém os comandos oficiais no catálogo como dados de validação, mas mostra ao
  jogador somente as teclas que os executam; o `F10` combina o mapa comum com
  o plano do modo ativo;
- compõe a ajuda contextual por aliases curtos, eliminando 24 CFGs duplicados;
- padroniza em 26 caracteres a coluna de teclas e comandos e amplia o console
  para 80% da tela quando a ajuda é solicitada;
- aplica o perfil KTX no evento de entrada correspondente ao usermode, depois
  que o QVM conclui a conexão local, tornando os binds e o `F10` determinísticos;
- força a conexão local inicial como jogador; isso evita o caminho de espectador
  incompatível entre o QVM atual e o servidor integrado do ezQuake 3.6.9;
- aplica os valores de timing e salto do KTX somente ao KTX e restaura o padrão
  nQuake ao abrir outro mod, impedindo o vazamento pelo `config.cfg` pessoal;
- preserva a ordem determinística de pacotes.
- preserva integralmente as skins e cores relacionais definidas pelo KTX e pela
  configuração pessoal, inclusive nas sessões que contêm Frogbots;
- não envia cores individuais dos perfis de nomes, mantendo camisa, calça e
  cores de equipe sob as regras nativas do KTX;
- organiza a camada mantida pelo projeto em `catalog/`, `config/`, `runtime/`,
  `source/` e `policy/`, sem alterar os caminhos instalados.

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
- 31/07/2026: CTF iniciou no ezQuake 3.6.9 em janela `1280x720`, carregou
  `e2m2`, executou `configs/usermodes/ctf/default.cfg` e entrou em `standby`
  com um jogador e 16 vagas. O teste usou `cfg_save_onquit 0` e preservou o
  hash da configuração pessoal.
- 01/08/2026: os 24 modos foram abertos no ezQuake 3.6.9 em janela
  `1280x720`; os 22 modos compatíveis com Frogbot receberam o bot escolhido
  durante a primeira entrada. CTF e Race permaneceram sem bots, conforme o
  contrato do QVM. Practice deixou de aguardar uma segunda entrada inexistente.

## Correção encaminhada ao cliente

- o ruído de registros duplicados após `vid_restart` não pertence ao KTX; a
  correção genérica foi enviada ao ezQuake no
  [PR #1150](https://github.com/QW-Group/ezquake-source/pull/1150).

## Deliberadamente não alterado

- nenhum modo, votação, arma, regra, bot ou mapa foi criado no gamecode; as
  extensões apenas leem a aparência declarativa e corrigem o balanceamento
  automático nos usermodes nativos de duas ou três equipes;
- o launcher apenas expõe usermodes e comandos já existentes no KTX 1.47.
