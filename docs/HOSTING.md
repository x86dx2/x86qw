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

O KTX distribuído inclui 77 rotas Frogbot e 54 rotas Race. No dedicado, bots
são adicionados automaticamente quando o primeiro jogador entra; habilidade,
arma, vida e limite são configurados por cvars nativas do servidor:

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
`qw/x86qw-frogbot-names.json`. O prefixo `/ ` e a cor Quake são aplicados pelo
launcher. Consulte [Nomes dos Frogbots](FROGBOTS.md) para editar a lista
pessoal.
No jogo local, cada modo imprime seu plano de teclas ao abrir o mapa; sessões
com bots acrescentam `INS`/`DEL` para adicionar e remover e `HOME`/`END` para
ajustar a habilidade em torno do valor escolhido.
Com `--bot-skill random`, cada novo bot recebe independentemente uma habilidade
de 1 a 20.

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
./x86qw.sh host ktx --mode duel --with-proxy
```

O proxy usa `127.0.0.1:30000` por padrão e não promete reduzir latência. A
configuração gerenciada não consulta masters públicos automaticamente.

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
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`; falha de associação interrompe o startup.

Somente uma stack de serviços ou operação mutável pode ficar ativa por
instalação. `host`, `proxy`, `qtv`, `install`, `components`, `presets`,
`update`, `upgrade`, `repair`, `cleanup` e `uninstall` compartilham o lock
atômico `.x86qw/sessions/active.lock`, inclusive em `--dry-run`. Se o
controlador registrado estiver vivo, a segunda execução falha sem tocar
journal, processos ou arquivos. `version`, `verify`, `hub` e `play` continuam
disponíveis; `play` não participa do lock porque permanece sem mutação de
payload. O handoff de `update`/`upgrade` baixa e valida a CLI nova antes, e
somente o processo final com `--skip-cli-update` adquire o lock para modificar
o destino.

Se a identidade não puder ser comprovada, a CLI falha de forma conservadora e
orienta a inspeção do lock. Um lock abandonado só é reclamado quando PID, token
de criação e executável confirmam que o controlador anterior morreu. A
recuperação valida também o controlador gravado no próprio journal: remover
apenas `active.lock` nunca autoriza recuperar uma stack ainda viva.

Cada execução mantém um journal privado em
`.x86qw/sessions/<session-id>/session.json` (`0700` para diretórios e `0600`
para o arquivo no Unix). Ele registra processos, configurações efêmeras,
arquivos materializados, hashes não sensíveis e diretórios criados. Filhos usam grupo próprio
e registram PID, grupo, token de criação e executável. Após crash confirmado, a
recuperação encerra somente o processo cuja identidade corresponda exatamente;
PID reutilizado é preservado. Identidade inconclusiva bloqueia a nova stack e a
remoção de arquivos dos quais o processo possa depender.

Temporários não sensíveis criados pela sessão são removidos quando ainda
coincidem com o hash; modificados e dados preexistentes são preservados e
reportados. Configurações efêmeras com senhas são classificadas como sensíveis
e removidas por unlink no encerramento ou recuperação mesmo quando modificadas,
sem hash, backup ou conteúdo no journal. Se o caminho tiver sido substituído
por diretório ou arquivo especial, ele é preservado e a finalização falha sem
remoção recursiva. Symlink é removido sem tocar seu alvo. Isso é remoção lógica
e não promessa de apagamento físico no dispositivo.

## Limites de PK3/ZIP

A materialização dedicada interpreta nomes internos sempre com semântica
POSIX, independentemente do sistema hospedeiro. Ela rejeita traversal,
symlink, membro especial, drive, barra invertida, controles, nomes reservados
do Windows, colisões por caixa ou Unicode e caminhos incompatíveis com
Windows. Os limites atuais, medidos com folga sobre os pacotes distribuídos,
são:

- 4.096 membros;
- 128 MiB por membro;
- 512 MiB descompactados no total;
- 16 níveis de diretório;
- 240 caracteres por caminho;
- razão máxima de compressão de 500 para 1.

A extração usa arquivo temporário, hash SHA-256 e rename atômico. Arquivo
pessoal diferente nunca é sobrescrito. Configurações pessoais, logs e demos
não são tratados como payload imutável de atualização.
