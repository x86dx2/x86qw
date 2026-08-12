# Hospedagem dedicada, QTV e QWFWD

O x86QW distribui MVDSV `1.11+x86qw.3`, QTV
`0+025ca949aca0+x86qw.2` e QWFWD `1.30+x86qw.3` no perfil completo. Cada
runtime possui pacote, recibo e verificação próprios. Os comandos não baixam
conteúdo: se faltar um componente, a CLI orienta executar novamente o
instalador e selecionar o perfil completo ou o componente correspondente.

## Servidor dedicado

Sem argumentos, `host` lista os mesmos jogos instalados apresentados por
`play`, mas inicia somente o MVDSV. Também é possível escolher diretamente:

```sh
./x86qw.sh host
./x86qw.sh host ktx --mode duel --map dm6
./x86qw.sh host final-arena --map 23ar-a
./x86qw.sh host pro-x --map proxmap1
./x86qw.sh host team-fortress --map 2fort5r
./x86qw.sh host td2 --map dm6
```

No menu principal, a hospedagem pergunta primeiro jogo, modo/regras e mapa. Em
seguida há dois caminhos:

- **Rápido local:** `127.0.0.1:28501`, 16 clientes, MVD automático e sem QTV ou
  QWFWD; é o caminho seguro para treino, teste e rede local no mesmo computador;
- **Avançado:** interfaces, portas, capacidade, MVD, QTV, QWFWD e entrada oculta
  de senhas.

Antes de adquirir o lock ou iniciar processos, o menu apresenta um resumo e um
comando equivalente sem valores secretos. A seta esquerda volta uma etapa;
recusar a confirmação encerra o fluxo sem iniciar MVDSV ou serviços.
QTV e QWFWD isolados também apresentam resumo, comando seguro e confirmação;
voltar reabre sua configuração. Depois de executar ou consultar o estado, a
saída permanece visível até Enter e o navegador retorna ao submenu Serviços.

Cada jogo utiliza seu gamecode, configuração pessoal e mapas próprios. Como o
MVDSV não lê PK3, cargas como KTX e Final Arena são materializadas somente
durante a sessão e removidas ao encerrar.

O bind padrão é `127.0.0.1:28501`. Para aceitar conexões externas, escolha
deliberadamente a interface e ajuste firewall/NAT fora do x86QW:

```sh
./x86qw.sh host ktx --bind 0.0.0.0 --port 28501 --mode ctf --map e2m2
```

As opções legadas `--password`, `--spectator-password`, `--rcon-password` e
`--qtv-password` continuam aceitas, mas o valor pode permanecer no histórico
do shell ou na listagem de processos da própria CLI. Prefira entrada sem eco:

```sh
./x86qw.sh host ktx --prompt-password --prompt-rcon-password
./x86qw.sh host ktx --password-file ~/.config/x86qw/player-password
./x86qw.sh qtv --prompt-qtv-password --upstream quake.example:28501
```

Também existem `--spectator-password-file`, `--rcon-password-file` e
`--qtv-password-file`. No Unix, o arquivo deve ser regular, não pode ser
symlink e precisa estar restrito ao proprietário (`chmod 600`). Uma única
quebra de linha final é removida; conteúdo multilinha é recusado. Senhas nunca
entram nos argumentos do processo filho nem nos logs detalhados. Bind externo
sem senha produz um alerta explícito, sem bloquear uma escolha intencional.

O KTX distribuído inclui 77 rotas Frogbot e 54 rotas Race. O menu cruza esses
recursos com os BSPs instalados; rotas pessoais regulares nos diretórios
documentados em [Nomes dos Frogbots](FROGBOTS.md) também são reconhecidas. No
dedicado, bots são adicionados automaticamente quando o primeiro jogador
entra; habilidade e limite são configurados por cvars nativas do servidor.
Arma, vida e interrupção por morte pertencem exclusivamente ao ToT:

```sh
./x86qw.sh host ktx --mode duel --bots 1 --bot-skill 8 --bot-names x86qw
./x86qw.sh host ktx --mode 4on4 --fill-bots --bot-skill 6
./x86qw.sh host ktx --mode ffa --fill-bots --bot-skill random
./x86qw.sh host ktx --mode ctf --ctf-hook smooth --ctf-runes off
./x86qw.sh host ktx --mode race --race-style match --race-scoring formula1
```

Equipe fixa de bot, pacemaker e ocultação de corredores são controles do
cliente e permanecem disponíveis em `play`, não em `host`.

O dedicado aceita os mesmos três perfis de nome: `default` não customiza o
KTX, `x86qw` sorteia a lista One Piece da distribuição e `personal` lê
`qw/x86qw-frogbot-names.json`. O prefixo `/` e a cor Quake são aplicados pelo
launcher. Consulte [Nomes dos Frogbots](FROGBOTS.md) para editar a lista
pessoal.
No jogo local, cada modo imprime seu plano de teclas ao abrir o mapa; sessões
com bots acrescentam `INS`/`DEL` para adicionar e remover e `HOME`/`END` para
ajustar cumulativamente a habilidade dos próximos bots. A lotação declarada do
modo continua valendo durante toda a sessão.
Com `--bot-skill random`, cada novo bot recebe independentemente uma habilidade
de 1 a 20.

Quando o bind do host é externo e há bots, o x86QW reaplica
`k_fb_admin_only 1` depois da configuração pessoal. Jogadores remotos não
podem alterar o roster; um host restrito a loopback conserva o padrão KTX.

## QTV

Para iniciar QTV junto ao dedicado:

```sh
./x86qw.sh host ktx --mode 4on4 --map dm3 --with-qtv
```

O HTTP fica em `127.0.0.1:28000` por padrão. Também é possível iniciar QTV
separadamente:

```sh
./x86qw.sh qtv --upstream 127.0.0.1:28501
```

Upload HTTP permanece desativado no perfil gerenciado. Use `--bind 0.0.0.0`
somente se realmente quiser expor a página/stream. Em qualquer bind não
loopback, a CLI alerta que a interface HTTP/QTV ficará exposta e que a senha do
upstream não autentica o acesso HTTP. O alerta aparece mesmo quando
`--qtv-password` foi informado.

Ao compor `host --with-qtv`, o upstream usa um endereço alcançável do bind do
MVDSV: wildcard IPv4 vira `127.0.0.1`, wildcard IPv6 vira `[::1]`, endereços
específicos são preservados e IPv6 é sempre formatado entre colchetes.

Upstreams aceitam apenas `IPv4:porta`, `hostname:porta` ou `[IPv6]:porta`.
Espaços, controles, comandos concatenados, portas fora da faixa e IPv6 sem
colchetes são recusados.

## QWFWD

```sh
./x86qw.sh proxy
./x86qw.sh proxy --bind 0.0.0.0 --port 30000
./x86qw.sh proxy --background
./x86qw.sh host ktx --mode duel --with-proxy
```

O proxy usa `127.0.0.1:30000` por padrão e não promete reduzir latência. A
configuração gerenciada não consulta masters públicos automaticamente.

## Visualizar a stack ativa

Serviços podem acompanhar o terminal ou ser destacados com `--background`.
Para consultar qualquer stack ativa, execute:

```sh
./x86qw.sh status
./x86qw.sh status --stop
```

A mesma consulta fica em **Serviços → Visualizar serviços ativos** no navegador
interativo. A saída reúne a operação e a sessão ativas e, para cada MVDSV, QTV
ou QWFWD registrado, mostra estado da identidade, PID, runtime, executável,
endpoint e parâmetros efetivos como jogo, modo, mapa, bots, bind, portas,
clientes, upstream e gravação MVD. Valores de senha nunca são persistidos nessa
visão; ela informa somente quais classes de segredo foram configuradas. A área
**Serviços → Encerrar serviços ativos** pede confirmação e envia ao controlador
identificado um pedido privado de shutdown coordenado.

`status` sem opções é estritamente somente leitura: não adquire o lock, não
recupera sessão, não encerra processo e não altera arquivos. `status --stop`
é a ação explícita de encerramento; `--yes` permite automação. Um journal
inconclusivo ou sem lock é preservado para inspeção, sem sinalizar PID por
inferência.

Ao destacar uma stack, os valores de senha resolvidos são enviados uma única
vez ao novo controlador por pipe anônimo. Eles não entram nos argumentos, no
journal, no status nem no log. Os runtimes recebem `stdin` fechado em
`DEVNULL`; segredos continuam materializados somente nas configurações
efêmeras privadas já cobertas pelo cleanup da sessão.

## Encerramento e arquivos pessoais

Antes de criar qualquer processo, a CLI detecta portas duplicadas, tenta os
binds solicitados e valida diretórios, executáveis, configurações e
componentes. Ao compor os três serviços, a ordem é MVDSV, readiness por
`status`, configuração pós-map via RCON local, QTV com readiness HTTP e
upstream, e por último QWFWD com prova de vida e ocupação da porta. Falha em
qualquer etapa encerra os processos já iniciados na ordem inversa.

Os serviços rodam em primeiro plano. `Ctrl+C`, `SIGINT`, `SIGTERM`, erro de
startup e encerramento normal encerram os filhos na ordem inversa, aguardam a
saída e forçam apenas processos que não responderem. No Unix, a CLI verifica o
grupo completo depois de `SIGTERM` e aplica `SIGKILL` se um descendente
permanecer. No Windows, todos os processos entram em um Job Object com
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. No código corretivo da PR 4, cada processo
nasce suspenso, entra no Job Object e só então tem sua thread inicial retomada;
falha de associação encerra o líder ainda suspenso, sem executar o runtime.
Os casos Win32 registrados na release pública são históricos; no fluxo atual,
MVDSV, QTV e QWFWD não executam smokes nativos e seus contratos permanecem
disponíveis para compatibilidade.

Somente uma stack de serviços ou operação mutável pode ficar ativa por
instalação. `host`, `proxy`, `qtv`, `install`, `components`, `presets`,
`update`, `upgrade`, `repair`, `cleanup` e `uninstall` compartilham o lock
atômico `.x86qw/sessions/active.lock`, inclusive em `--dry-run`. Se o
controlador registrado estiver vivo, a segunda execução falha sem tocar
journal, processos ou arquivos. `status`, `version`, `verify`, `hub` e `play` continuam
disponíveis; `play` não participa do lock porque permanece sem mutação de
payload. O handoff de `update`/`upgrade` baixa e valida a CLI nova antes, e
somente o processo final com `--skip-cli-update` adquire o lock para modificar
o destino.

Se a identidade não puder ser comprovada, a CLI falha de forma conservadora e
orienta a inspeção do lock. Um lock abandonado só é reclamado quando PID, token
de criação e executável confirmam que o controlador anterior morreu. A
recuperação valida também o controlador gravado no próprio journal: remover
apenas `active.lock` nunca autoriza recuperar uma stack ainda viva.
Na PR 4, observação, reclamação e substituição de um lock abandonado também são
serializadas por instalação (`flock` no POSIX e mutex global privado no
Windows), eliminando a disputa em que duas CLIs poderiam reclamar o mesmo lock.
A serialização e a DACL do mutex foram exercitadas nos dois jobs Windows;
operação real dos serviços continua sendo evidência separada.

Cada execução mantém um journal privado em
`.x86qw/sessions/<session-id>/session.json` (`0700` para diretórios e `0600`
para o arquivo no Unix). Ele registra processos, configurações efêmeras,
arquivos materializados, hashes não sensíveis e diretórios criados. Filhos usam grupo próprio
e registram PID, grupo, token de criação e executável. Após crash confirmado, a
recuperação encerra somente o processo cuja identidade corresponda exatamente;
PID reutilizado é preservado. Identidade inconclusiva bloqueia a nova stack e a
remoção de arquivos dos quais o processo possa depender.

No código corretivo da PR 4, `.x86qw/`, sessões, locks, journals, pedidos de
parada, logs e configurações sensíveis usam no Windows uma DACL protegida com
acesso somente ao usuário atual e a `LOCAL SYSTEM`. O objeto nasce privado,
antes da primeira escrita; um volume ou ACL inconclusivo faz a operação falhar
fechada. Arquivos externos de senha são apenas validados e lidos pelo mesmo
handle, nunca reconfigurados pelo x86QW. A matriz nativa Windows registrada na
release pública `0.7.3` é evidência histórica do contrato; o fluxo atual é
operado somente no Mac/local, sem runner Windows e sem smoke nativo obrigatório.
A mudança foi publicada na release pública `0.7.3`. Consulte o
[ADR 0003](adr/0003-dacl-privada-windows.md).

Arquivos materializados e temporários não sensíveis atuais registram também o
tamanho e a identidade exatos. A recuperação nunca calcula hash além desse
limite; um journal legado incompleto preserva o arquivo e solicita inspeção. No
POSIX, a remoção usa rename exclusivo e atômico para uma quarentena, repete
tamanho, identidade e hash pelo descritor aberto e restaura o nome quando
encontra alteração. Isso cobre edições e substituições concorrentes pelo nome
público; Linux e macOS possuem adaptações nativas e outro POSIX falha fechado.
Não constitui isolamento contra código já executando sob o mesmo usuário com
descritor gravável, `mmap` obtido antes da quarentena ou acesso direto ao seu
nome aleatório; os processos controlados pelo x86QW são encerrados antes dessa
etapa.

Temporários não sensíveis criados pela sessão são removidos quando ainda
coincidem com o hash; modificados e dados preexistentes são preservados e
reportados. Configurações efêmeras com senhas são classificadas como sensíveis
e removidas por unlink no encerramento ou recuperação mesmo quando modificadas,
sem hash, backup ou conteúdo no journal. Se o caminho tiver sido substituído
por diretório ou arquivo especial, ele é preservado e a finalização falha sem
remoção recursiva. Symlink é removido sem tocar seu alvo. Isso é remoção lógica
e não promessa de apagamento físico no dispositivo.

## Limites de ZIP, PK3 e PYZ

O candidato corretivo da issue #49 aplica uma única fronteira ao instalador,
gameplay, serviços, ferramentas e bundles. Todo arquivo é percorrido antes da
primeira leitura entregue ao consumidor ou do primeiro write de extração. O
snapshot privado e limitado faz parte do preflight. Nomes internos
usam semântica POSIX, independentemente do sistema hospedeiro. Traversal,
absolutos, drives, barra invertida, controles, caracteres Win32 proibidos
(`<`, `>`, `"`, `|`, `?`, `*`), nomes reservados Windows,
trailing ponto/espaço, links, membros especiais e colisões exatas, por caixa,
Unicode ou prefixo são recusados.

Somente Stored e Deflate são aceitos. Os limites, medidos com folga sobre os 59
ZIP/PK3/PYZ inventariados no início da mudança, são:

- 4.096 membros;
- 512 MiB para o arquivo-fonte compactado;
- 32 MiB para os metadados do diretório central;
- 128 MiB por membro;
- 512 MiB descompactados no total;
- 16 níveis de diretório;
- 240 unidades UTF-16 por caminho;
- razão máxima de compressão de 500 para 1.

A fonte é copiada com limite estrito para um snapshot privado em disco. Um
pre-scan estrutural limita e conta ali os registros reais do diretório central
antes de `zipfile` criar `ZipInfo`, portanto o EOCD não pode subestimar membros
e uma troca concorrente da fonte não muda os bytes entre as etapas. O scan
valida envelope, tamanho real, CRC e SHA-256 de todos os membros e liga o plano
à identidade e ao SHA-256 da fonte. A extração revalida integralmente o plano
antes do rename, usa diretório irmão privado, aplica somente `0644` ou `0755` e
sincroniza arquivos e diretórios. Falha antes da promoção remove somente o
staging comprovado. A confirmação da identidade publicada é o commit; resultado
de promoção inconclusivo é preservado. Falha posterior preserva o destino
completo e conteúdo pessoal concorrente. Destino anterior nunca é substituído.

Na materialização dedicada, o PK3 passa primeiro por essa extração canônica e
só depois é copiado de forma atômica e journalizada para o contexto do MVDSV.
Arquivo pessoal diferente nunca é sobrescrito. Configurações pessoais, logs e
demos não são tratados como payload imutável de atualização. No Windows,
ancestrais permanecem abertos sem compartilhamento de remoção, reparse points
são recusados e criação, identidade e limpeza usam handles Win32; o arquivo
confirmado é removido pelo próprio handle, nunca por `DeleteFileW` aplicado a
um nome reaberto. Consulte o
[ADR da fronteira de arquivos](adr/0002-fronteira-unica-de-arquivos.md).

A fronteira canônica foi publicada na release pública `0.7.3`; o fluxo atual
usa a validação Mac/local e não inclui smokes nativos dos runtimes.
