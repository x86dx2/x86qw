# Hospedagem dedicada, QTV e QWFWD

O x86QW distribui MVDSV `1.11+x86qw.4`, QTV `0+025ca949aca0+x86qw.3` e QWFWD
`1.30+x86qw.4`. Eles entram quando a instalação hospeda (intenção Hospedar ou
Jogar e hospedar) ou no perfil completo de quem hospeda. Cada runtime possui
pacote, recibo e verificação próprios. Os comandos não baixam conteúdo: se
faltar um componente, adicione-o em **Componentes e configuração** no app ou
com `x86qw setup`.

## Dois pontos de partida

**O servidor que o instalador monta.** Ao escolher Hospedar ou Jogar e
hospedar, o instalador grava as escolhas e a senha de rcon em `.x86qw/setup/`
e um preset por instância (`x86qw-1`, `x86qw-2`…) em
`qw/x86qw-host-presets.json`. Por padrão são duas instâncias KTX (Duel em `dm6` e
4on4 com Frogbots em `dm3`), a partir da porta UDP 27500, com QTV na 28000 e
QWFWD na 30000. Esse servidor aceita jogadores da rede em todas as interfaces;
escolha **Só nesta máquina** (ou `--local` na receita) para restringi-lo a
`127.0.0.1`. A tela **Hospedar** do app inicia e encerra a sessão em segundo
plano e mostra o que está rodando. O comando equivalente aparece no fim da
instalação por receita.

**O comando `host` direto.** Ele escolhe um jogo e sobe o MVDSV em
`127.0.0.1:28501`, acompanhando o terminal:

```sh
x86qw host ktx --mode duel --map dm6
x86qw host final-arena --map 23ar-a
x86qw host pro-x --map proxmap1
x86qw host team-fortress --map 2fort5r
x86qw host td2 --map dm6
x86qw host --menu
```

Sem jogo e sem `--mode`, o comando pede o jogo; `--menu` abre a tela Hospedar
do app. `--instance MODO:MAPA:PORTA[:bots]` acrescenta mais servidores KTX à
mesma sessão (repetível); QTV e QWFWD ficam com o primeiro:

```sh
x86qw host ktx --mode duel --map dm6 --port 27500 --instance 4on4:dm3:27501:bots
```

Layouts locais, sem senhas, ficam em `qw/x86qw-host-presets.json` e entram no
backup de perfil:

```sh
x86qw host ktx --mode duel --map dm6 --save-preset local-duel
x86qw host --preset local-duel
```

Cada jogo utiliza seu gamecode, configuração pessoal e mapas próprios. Como o
MVDSV não lê PK3, cargas como KTX e Final Arena são materializadas somente
durante a sessão e removidas ao encerrar.

## Rede

O bind padrão de `host` é `127.0.0.1:28501`. Para aceitar conexões externas,
escolha deliberadamente a interface e ajuste firewall e NAT fora do x86QW:

```sh
x86qw host ktx --bind 0.0.0.0 --port 28501 --mode ctf --map e2m2
```

## Senhas

`--password`, `--spectator-password`, `--rcon-password` e `--qtv-password`
são aceitos, mas o valor pode ficar no histórico do shell e na lista de
processos da própria CLI. Prefira entrada sem eco ou arquivo:

```sh
x86qw host ktx --prompt-password --prompt-rcon-password
x86qw host ktx --password-file ~/.config/x86qw/player-password
x86qw qtv --prompt-qtv-password --upstream quake.example:28501
```

Também existem `--spectator-password-file`, `--rcon-password-file` e
`--qtv-password-file`. A entrada sem eco precisa de um terminal. No Unix, o
arquivo deve ser regular, não pode ser symlink e precisa estar restrito ao
proprietário (`chmod 600`). Uma única quebra de linha final é removida;
conteúdo multilinha é recusado. Senhas nunca entram nos argumentos do processo
filho nem nos logs. Bind externo sem senha produz um alerta explícito, sem
bloquear uma escolha intencional.

## Frogbots

O KTX distribuído inclui 77 rotas Frogbot e 54 rotas Race. A CLI cruza esses
recursos com os BSPs instalados; rotas pessoais regulares nos diretórios
documentados em [Nomes dos Frogbots](FROGBOTS.md) também são reconhecidas. No
dedicado, bots são adicionados automaticamente quando o primeiro jogador
entra; habilidade e limite são configurados por cvars nativas do servidor.
Arma, vida e interrupção por morte pertencem exclusivamente ao ToT:

```sh
x86qw host ktx --mode duel --bots 1 --bot-skill 8 --bot-names x86qw
x86qw host ktx --mode 4on4 --fill-bots --bot-skill 6
x86qw host ktx --mode ffa --fill-bots --bot-skill random
x86qw host ktx --mode ctf --ctf-hook smooth --ctf-runes off
x86qw host ktx --mode race --race-style match --race-scoring formula1
```

Equipe fixa de bot, pacemaker e ocultação de corredores são controles do
cliente e permanecem disponíveis em `play`, não em `host`.

O dedicado aceita os mesmos quatro perfis de nome: `default` não customiza o
KTX, `x86qw` sorteia mapas e modos da distribuição, `one-piece` sorteia a
lista One Piece opcional e `personal` lê `qw/x86qw-frogbot-names.json`. O
prefixo `/` e a cor Quake são aplicados pela CLI. Com `--bot-skill random`,
cada novo bot recebe independentemente uma habilidade de 1 a 20.

Quando o bind do host é externo e há bots, o x86QW reaplica
`k_fb_admin_only 1` depois da configuração pessoal. Jogadores remotos não
podem alterar o roster; um host restrito a loopback conserva o padrão KTX.

## QTV

Para iniciar QTV junto ao dedicado:

```sh
x86qw host ktx --mode 4on4 --map dm3 --with-qtv
```

O HTTP fica em `127.0.0.1:28000` por padrão. Também é possível iniciar QTV
separadamente:

```sh
x86qw qtv --upstream 127.0.0.1:28501
```

Upload HTTP permanece desativado no perfil gerenciado. Use `--bind 0.0.0.0`
somente se realmente quiser expor a página e o stream. Em qualquer bind não
loopback, a CLI alerta que a interface HTTP/QTV ficará exposta e que a senha do
upstream não autentica o acesso HTTP, mesmo quando `--qtv-password` foi
informado.

Ao compor `host --with-qtv`, o upstream usa um endereço alcançável do bind do
MVDSV: wildcard IPv4 vira `127.0.0.1`, wildcard IPv6 vira `[::1]`, endereços
específicos são preservados e IPv6 é sempre formatado entre colchetes.
Upstreams aceitam apenas `IPv4:porta`, `hostname:porta` ou `[IPv6]:porta`.
Espaços, controles, comandos concatenados, portas fora da faixa e IPv6 sem
colchetes são recusados.

Na tela **Servidores** do app, `ctrl+w` assiste a um servidor pelo seu QTV;
na linha de comando, `x86qw open qw://N@host:porta/qtvplay` faz o mesmo.

## QWFWD

```sh
x86qw proxy
x86qw proxy --bind 0.0.0.0 --port 30000
x86qw proxy --background
x86qw host ktx --mode duel --with-proxy
```

O proxy usa `127.0.0.1:30000` por padrão e não promete reduzir latência. A
configuração gerenciada não consulta masters públicos automaticamente.

## Ver e encerrar a stack ativa

Serviços acompanham o terminal ou são destacados com `--background`. Para
consultar ou encerrar a stack:

```sh
x86qw status
x86qw status --stop
```

A tela **Hospedar** do app mostra a mesma informação. A saída reúne a operação
e a sessão ativas e, para cada MVDSV, QTV ou QWFWD registrado, mostra estado da
identidade, PID, runtime, executável, endpoint e parâmetros efetivos como jogo,
modo, mapa, bots, bind, portas, clientes, upstream e gravação MVD. Valores de
senha nunca aparecem; a visão informa somente quais classes de segredo foram
configuradas.

`status` sem opções é somente leitura: não adquire o lock, não recupera sessão,
não encerra processo e não altera arquivos. `status --stop` pergunta no
terminal e, sem terminal, exige `--yes`; ele envia ao controlador identificado
um pedido privado de encerramento coordenado. Um journal inconclusivo ou sem
lock é preservado para inspeção, sem sinalizar PID por inferência.

Ao destacar uma stack, os valores de senha resolvidos são enviados uma única
vez ao novo controlador por pipe anônimo. Eles não entram nos argumentos, no
journal, no status nem no log. Os runtimes recebem `stdin` fechado; segredos
existem somente nas configurações efêmeras privadas cobertas pelo cleanup da
sessão.

## Início, encerramento e locks

Antes de criar qualquer processo, a CLI detecta portas duplicadas, tenta os
binds solicitados e valida diretórios, executáveis, configurações e
componentes. A ordem é MVDSV, readiness por `status`, configuração pós-mapa via
RCON local, QTV com readiness HTTP e upstream e, por último, QWFWD com prova de
vida e ocupação da porta. Falha em qualquer etapa encerra os processos já
iniciados na ordem inversa.

`Ctrl+C`, `SIGINT`, `SIGTERM`, erro de startup e encerramento normal encerram
os filhos na ordem inversa, aguardam a saída e forçam apenas processos que não
responderem. No Unix, a CLI verifica o grupo completo depois de `SIGTERM` e
aplica `SIGKILL` se um descendente permanecer. No Windows, cada processo nasce
suspenso, entra num Job Object com `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` e só
então tem a thread inicial retomada; falha de associação encerra o processo
ainda suspenso, sem executar o runtime.

Somente uma stack de serviços ou operação que altera a instalação fica ativa
por vez. `host`, `qtv`, `proxy`, `setup`, `install`, `update`, `upgrade`,
`repair`, `cleanup`, `migrate` e `uninstall` compartilham o lock atômico
`.x86qw/sessions/active.lock`. Se o controlador registrado estiver vivo, a
segunda execução falha sem tocar journal, processos ou arquivos. `status`,
`version`, `verify`, `doctor`, `hub` e `play` não usam o lock; os planos com
`--dry-run` também não.

Se a identidade não puder ser comprovada, a CLI falha de forma conservadora e
orienta a inspeção do lock. Um lock abandonado só é reclamado quando PID, token
de criação e executável confirmam que o controlador anterior morreu, e a
recuperação valida também o controlador gravado no journal: remover apenas
`active.lock` nunca autoriza recuperar uma stack ainda viva. Observação,
reclamação e substituição de um lock abandonado são serializadas por
instalação (`flock` no POSIX e mutex global privado no Windows).

Cada execução mantém um journal privado em
`.x86qw/sessions/<session-id>/session.json` (`0700` para diretórios e `0600`
para o arquivo no Unix). Ele registra processos, configurações efêmeras,
arquivos materializados, hashes não sensíveis e diretórios criados. Filhos
usam grupo próprio e registram PID, grupo, token de criação e executável. Após
crash confirmado, a recuperação encerra somente o processo cuja identidade
corresponda exatamente; PID reutilizado é preservado. Identidade inconclusiva
bloqueia a nova stack e a remoção de arquivos dos quais o processo possa
depender.

No Windows, `.x86qw/`, sessões, locks, journals, pedidos de parada, logs e
configurações sensíveis usam uma DACL protegida com acesso somente ao usuário
atual e a `LOCAL SYSTEM`. O objeto nasce privado, antes da primeira escrita; um
volume ou ACL inconclusivo faz a operação falhar fechada. Arquivos externos de
senha são apenas validados e lidos pelo mesmo handle, nunca reconfigurados pelo
x86QW. Consulte o [ADR 0003](adr/0003-dacl-privada-windows.md).

Temporários não sensíveis criados pela sessão são removidos quando ainda
coincidem com o hash; modificados e dados preexistentes são preservados e
reportados. Configurações efêmeras com senhas são classificadas como sensíveis
e removidas no encerramento ou na recuperação mesmo quando modificadas, sem
hash, backup ou conteúdo no journal. Se o caminho tiver sido substituído por
diretório ou arquivo especial, ele é preservado e a finalização falha sem
remoção recursiva. Symlink é removido sem tocar seu alvo. Isso é remoção lógica
e não promessa de apagamento físico no dispositivo.

## Limites de ZIP e PK3

Toda extração passa pela mesma fronteira: o arquivo é percorrido inteiro antes
da primeira leitura entregue ao consumidor ou do primeiro write. Nomes
internos usam semântica POSIX, independentemente do sistema hospedeiro.
Traversal, caminhos absolutos, drives, barra invertida, controles, caracteres
Win32 proibidos (`<`, `>`, `"`, `|`, `?`, `*`), nomes reservados do Windows,
nomes terminados em ponto ou espaço, links, membros especiais e colisões
exatas, por caixa ou Unicode são recusados.

Somente Stored e Deflate são aceitos, com estes limites:

- 4.096 membros;
- 512 MiB para o arquivo compactado;
- 128 MiB por membro;
- 512 MiB descompactados no total;
- 16 níveis de diretório;
- 240 unidades UTF-16 por caminho;
- razão máxima de compressão de 500 para 1.

A extração confere tamanho e CRC de cada membro enquanto escreve, usa um
diretório irmão privado, aplica somente `0644` ou `0755` e promove o resultado
por rename; o destino precisa não existir antes. Falha antes da promoção remove
somente o staging.

Na materialização dedicada, o PK3 passa primeiro por essa extração e só depois
é copiado de forma atômica e registrada no journal para o contexto do MVDSV.
Arquivo pessoal diferente nunca é sobrescrito. Configurações pessoais, logs e
demos não são tratados como conteúdo gerenciado. Consulte o
[ADR da fronteira de arquivos](adr/0002-fronteira-unica-de-arquivos.md).
