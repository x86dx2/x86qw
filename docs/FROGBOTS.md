# Nomes dos Frogbots

O x86QW oferece três perfis de identidade para os Frogbots do KTX. O perfil é
escolhido no menu ou com `--bot-names`; nomes são dados declarativos e
não alteram regras, atributos ou inteligência dos bots.

| Perfil | Comportamento |
|---|---|
| `default` — KTX Default | Não define identidade; o KTX conserva nomes e cores originais. |
| `x86qw` — x86QW aleatório | Embaralha uma vez por lançamento os nomes de personagens One Piece. |
| `personal` | Usa, na ordem declarada, os nomes da lista pessoal da instalação. |

Exemplos:

```sh
./x86qw.sh play ktx --mode duel --map dm6 --bots 1 --bot-names x86qw
./x86qw.sh play ktx --mode 2on2 --map dm6 --bots 2 --bot-names personal
./x86qw.sh host ktx --mode 2on2 --map dm6 --bots 2 --bot-names x86qw
```

O menu interativo destaca `x86QW aleatório` inicialmente; `KTX Default` continua
disponível para usar somente as identidades originais. Na CLI, `default` permanece o
padrão por compatibilidade, portanto uma execução que não informe `--bot-names`
fica sem customização.

## Lista x86QW

A fonte canônica fica em:

```text
dist/mods/ktx/1.47/x86qw/catalog/frogbots/names.json
```

Os dez primeiros nomes são os integrantes do Bando do Chapéu de Palha:
Luffy, Zoro, Nami, Usopp, Sanji, Chopper, Robin, Franky, Brook e Jinbe. Essa
composição segue o [elenco oficial de personagens de One Piece][straw-hats].

Os nomes seguintes reúnem figuras apresentadas pela obra como lendas, grandes
potências ou detentores de títulos relevantes. A seleção é editorial — não é
um ranking oficial de força. Entre as referências usadas estão Gol D. Roger,
o Rei dos Piratas; Edward Newgate, apresentado como o homem mais forte do
mundo; Shanks e Marshall D. Teach, Imperadores; e Dracule Mihawk, o espadachim
mais forte do mundo. Consulte as páginas oficiais de [Roger][roger],
[Whitebeard][whitebeard], [Shanks][shanks], [Blackbeard][blackbeard] e
[Mihawk][mihawk].

## Lista pessoal

O bootstrap cria, sem sobrescrever edições futuras:

```text
quake-world/qw/x86qw-frogbot-names.json
```

Edite somente a lista `characters`, mantendo o restante do contrato:

```json
{
  "format": 1,
  "game": "ktx",
  "profile": "personal",
  "prefix": "/",
  "color": "quake-high-bit",
  "characters": [
    {"name": "Luffy"},
    {"name": "Zoro"}
  ]
}
```

Cada nome aceita de 1 a 13 caracteres ASCII: letras, números, espaço, ponto,
apóstrofo e hífen. A comparação ignora maiúsculas e minúsculas, portanto não
pode haver duplicatas como `Luffy` e `luffy`. A lista precisa conter ao menos
tantos nomes quanto a quantidade de bots solicitada; `--fill-bots` usa até oito
nos modos abertos e completa a lotação declarada nos modos fixos. Listas antigas
formadas apenas por strings em `names` continuam aceitas.
Cada item aceita somente `name`. Campos de aparência são rejeitados para que
nenhuma opção pareça surtir efeito quando, por contrato, camisa, calça e cores
de equipe pertencem às regras e configurações padrão do KTX.

Não escreva `/` nem códigos de cor nos valores. O launcher acrescenta
automaticamente o prefixo `/` e converte cada caractere para a variante
colorida clássica do Quake. O formato RGB `&cRGB` não é usado porque o
[ezQuake não o permite em nomes de jogadores][ezquake-charsets] por
compatibilidade com clientes QuakeWorld antigos.

Os nomes genéricos, aliados e inimigos recebem as três famílias de cvars
`k_fb_name_*`, mas o mesmo índice conserva a mesma identidade em todas elas.
Assim, a classificação relacional do KTX não troca nem duplica o nome do bot.
O launcher não envia cvars de aparência e a extensão QVM x86QW não registra
cvars próprias de camisa ou calça; o KTX conserva integralmente suas regras de
cores. No perfil
aleatório, o sorteio é feito uma vez antes da abertura do runtime; reiniciar o
jogo produz outra ordem. No perfil pessoal, a ordem do JSON é preservada.

O launcher não altera `teamskin`, `enemyskin`, `teamcolor`, `enemycolor` nem as
variantes `r_*skincolor`, mesmo quando a seleção contém Frogbots. A renderização
segue o perfil padrão do KTX e as configurações pessoais do jogador, da mesma
forma em partidas locais e servidores reais.

No jogo local, o launcher grava essas cvars em um CFG de sessão privado
(`0600` no Unix), manda o ezQuake carregá-lo antes do mapa e remove o arquivo
logo após a inicialização. O arquivo é único por processo, não altera a lista
pessoal e evita os limites de tamanho e de aliases do console. No servidor
dedicado, as mesmas escolhas entram na configuração efêmera já protegida pelo
journal da sessão.

Nos modos com quantidade fixa de jogadores, o menu oferece somente completar
as vagas restantes depois do jogador humano. Duel adiciona um bot; 2on2 adiciona
três; 3on3 adiciona cinco; 2on2on2 adiciona cinco; 4on4 adiciona sete. A CLI
aplica a mesma validação.
O launcher põe o jogador no primeiro time declarado e deixa o QVM executar a
distribuição automática nativa. O patch reproduzível usa o índice global do bot
ao escolher os nomes aliados e inimigos, evitando repetir identidades quando o
usermode possui três equipes. Uma equipe explícita na CLI continua sendo
respeitada tanto na abertura quanto nos bots inseridos depois com `INS`.

`3on3` mantém o significado convencional do KTX: duas equipes com três
jogadores. `2on2on2` é a formação de três equipes com dois jogadores. Os aliases
`3v3` e `2v2v2` continuam aceitos pela CLI. `3on3on3` e `4on4on4` mantêm três
equipes de três e quatro jogadores. A composição fica em `catalog/modes.json`, no campo `bot_teams`, e
é consumida pelo menu, pelo launcher local e pelo servidor dedicado.

Modos abertos, como FFA e Practice, conservam as opções de preenchimento e
quantidade personalizada. A entrada de vários bots é espaçada por frames do servidor.
Escolha **Aleatória** no menu, ou use `--bot-skill random`, para sortear uma
habilidade independente de 1 a 20 a cada bot inserido, inclusive por `fill`.

## Mapas e recursos

O menu não confunde “mapa instalado” com “mapa utilizável pela configuração
atual”. A lista exibida é a interseção entre os BSPs presentes e os recursos
declarados pelo modo:

- com Frogbots, o mapa precisa de `qw/bots/maps/<mapa>.bot`;
- CTF precisa de `id1/maps/ctf/<mapa>.ent`;
- Race precisa de `qw/race/routes/<mapa>.route`;
- sem bots, um modo comum continua oferecendo mapas que não possuem rota bot.

O pacote KTX contém 77 rotas Frogbot e 54 arquivos Race. Esses números não são
tratados como uma lista de BSPs: o launcher também exige que o mapa esteja
instalado. O CTF oferece exatamente os seis mapas para os quais o componente
instala ENTs. O ToT oferece `dm4`, `e1m2` e `schloss`, os três mapas com
configuração específica no upstream; `dm4` é o padrão por estar disponível no
perfil Essential.

Rotas pessoais regulares podem ser acrescentadas sem reconstruir o componente:

```text
quake-world/qw/bots/maps/meumapa.bot
quake-world/qw/race/routes/meumapa.route
quake-world/id1/maps/ctf/meumapa.ent
```

O nome do arquivo deve corresponder ao BSP. Symlinks e nomes inseguros não são
incorporados ao catálogo de sessão. O validador do projeto inspeciona ainda as
diretivas e a estrutura das rotas oficiais antes de gerar um pacote.

As opções `--bot-weapon`, `--bot-health` e
`--[no-]bot-break-on-death` pertencem exclusivamente ao ThunderWalker ToT.
Quando não são informadas, prevalece a configuração específica do mapa; o menu
permite escolher explicitamente o valor padrão, ativado ou desativado.

## Teclas da sessão

Cada modo KTX declara seu próprio plano de teclas. O resumo é impresso
automaticamente assim que o mapa abre e pode ser reapresentado com `F10`:

- `F5`, `F6` e `F11`: ações principais do modo; no Duel correspondem a
  ready, break e regras;
- `H`, `I`, `M`, `X` e `Z`: ações complementares somente quando o modo precisa;
  Race usa fila, tempos, rota e câmera, enquanto CTF usa estado e descarte de
  bandeira ou runa;
- `INS` e `DEL`: adicionar e remover Frogbot quando a sessão começou com bots;
  `INS` respeita a lotação do modo e `DEL` libera novamente uma vaga;
- `HOME` e `END`: diminuir ou aumentar cumulativamente, entre 1 e 20, a
  habilidade dos próximos bots. Em habilidade aleatória, as teclas apenas
  confirmam que o sorteio individual continua ativo;
- `F12`: fechar diretamente o QuakeWorld.

Os binds gerenciados são carregados antes de `x86qw-ktx-user.cfg`. Assim, uma
configuração pessoal pode sobrescrevê-los; o launcher muda apenas os aliases
contextuais e não reescreve o arquivo pessoal.

Em hospedagem ligada a um endereço externo, uma sessão com bots força
`k_fb_admin_only 1` depois da configuração pessoal. Isso impede que jogadores
remotos alterem o roster Frogbot; hosts apenas em loopback conservam o
comportamento padrão do KTX.

[straw-hats]: https://one-piece.com/character/luffy/index.html
[roger]: https://one-piece.com/character/Gold_Roger/index.html
[whitebeard]: https://one-piece.com/character/edward_newgate/index.html
[shanks]: https://one-piece.com/character/Shanks/index.html
[blackbeard]: https://one-piece.com/character/Marshall_D_Teech/index.html
[mihawk]: https://one-piece.com/character/Dracule_Mihawk/index.html
[ezquake-charsets]: https://ezquake.com/docs/charsets.html
