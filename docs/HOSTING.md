# Hospedagem dedicada, QTV e QWFWD

O x86QW distribui MVDSV `1.11+x86qw.2`, QTV
`0+025ca949aca0+x86qw.1` e QWFWD `1.30+x86qw.2` no perfil completo. Cada
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

Use `--password`, `--spectator-password` e `--rcon-password` quando necessário.
Esses valores entram em um arquivo de sessão com permissão privada, removido ao
encerrar. Caracteres que poderiam concatenar comandos são rejeitados.

O KTX distribuído inclui 77 rotas Frogbot e 54 rotas Race. No dedicado, bots
são adicionados automaticamente quando o primeiro jogador entra; habilidade,
arma, vida e limite são configurados por cvars nativas do servidor:

```sh
./x86qw.sh host ktx --mode duel --bots 1 --bot-skill 8
./x86qw.sh host ktx --mode 4on4 --fill-bots --bot-skill 6
./x86qw.sh host ktx --mode ctf --ctf-hook smooth --ctf-runes off
./x86qw.sh host ktx --mode race --race-style match --race-scoring formula1
```

Equipe fixa de bot, pacemaker e ocultação de corredores são controles do
cliente e permanecem disponíveis em `play`, não em `host`.

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
somente se realmente quiser expor a página/stream.

## QWFWD

```sh
./x86qw.sh proxy
./x86qw.sh proxy --bind 0.0.0.0 --port 30000
./x86qw.sh host ktx --mode duel --with-proxy
```

O proxy usa `127.0.0.1:30000` por padrão e não promete reduzir latência. A
configuração gerenciada não consulta masters públicos automaticamente.

## Encerramento e arquivos pessoais

Os serviços rodam em primeiro plano. `Ctrl+C` encerra filhos na ordem inversa,
aguarda a saída e força apenas processos que não responderem. A materialização
temporária de PK3 necessária ao MVDSV é removida quando permanece inalterada;
um arquivo modificado durante a sessão é preservado e reportado. Configurações
pessoais, logs e demos não são tratados como payload imutável de atualização.
