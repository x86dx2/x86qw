# Nomes dos Frogbots

O x86QW oferece três perfis de identidade para os Frogbots do KTX. O perfil é
escolhido no menu ou com `--bot-names`; nomes e cores são dados declarativos e
não alteram regras, atributos ou inteligência dos bots.

| Perfil | Comportamento |
|---|---|
| `default` — KTX Default | Não define identidade; o KTX conserva nomes e cores originais. |
| `x86qw` — x86QW aleatório | Embaralha uma vez por lançamento personagens One Piece com camisa e calça correspondentes. |
| `personal` | Usa, na ordem declarada, nomes e cores da lista pessoal da instalação. |

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
    {"name": "Luffy", "top_color": 4, "bottom_color": 13, "palette": "vermelho e azul"},
    {"name": "Zoro", "top_color": 3, "bottom_color": 3, "palette": "verde"}
  ]
}
```

Cada nome aceita de 1 a 13 caracteres ASCII: letras, números, espaço, ponto,
apóstrofo e hífen. A comparação ignora maiúsculas e minúsculas, portanto não
pode haver duplicatas como `Luffy` e `luffy`. A lista precisa conter ao menos
tantos nomes quanto a quantidade de bots solicitada; `--fill-bots` pode usar
oito. `top_color` é a camisa e `bottom_color` é a calça no índice clássico do
Quake, ambos entre 0 e 13. O campo `palette` é uma anotação humana opcional.
Listas antigas formadas apenas por strings em `names` continuam aceitas e usam
as cores padrão do KTX.

Não escreva `/` nem códigos de cor nos valores. O launcher acrescenta
automaticamente o prefixo `/ ` e converte cada caractere para a variante
colorida clássica do Quake. O formato RGB `&cRGB` não é usado porque o
[ezQuake não o permite em nomes de jogadores][ezquake-charsets] por
compatibilidade com clientes QuakeWorld antigos.

Os nomes genéricos, aliados e inimigos recebem sequências separadas de cvars
`k_fb_name_*`, `k_fb_topcolor_*` e `k_fb_bottomcolor_*`. O QVM KTX 1.47 recebe
uma extensão mínima e reprodutível que cacheia essas identidades; sem as cvars,
ele conserva integralmente o sorteio de cores e os nomes originais. No perfil
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
três; 3on3 adiciona cinco; 4on4 adiciona sete. A CLI aplica a mesma validação.
O launcher põe o jogador no primeiro time declarado e distribui cada bot na
equipe menos populosa até completar o elenco.

Os nomes seguem a semântica original do KTX: `3on3` são duas equipes de três
jogadores; três equipes de dois jogadores correspondem a `2on2on2`. Da mesma
forma, `3on3on3` e `4on4on4` têm três equipes de três e quatro jogadores. A
composição fica em `catalog/modes.json`, no campo `bot_teams`, e é consumida
pelo menu, pelo launcher local e pelo servidor dedicado.

Modos abertos, como FFA e Practice, conservam as opções de preenchimento e
quantidade personalizada. A entrada de vários bots é espaçada por frames do servidor.
Escolha **Aleatória** no menu, ou use `--bot-skill random`, para sortear uma
habilidade independente de 1 a 20 a cada bot inserido, inclusive por `fill`.

## Teclas da sessão

Cada modo KTX declara seu próprio plano de teclas. O resumo é impresso
automaticamente assim que o mapa abre e pode ser reapresentado com `F10`:

- `F5`, `F6` e `F11`: ações principais do modo; no Duel correspondem a
  ready, break e regras;
- `H`, `I`, `M`, `X` e `Z`: ações complementares somente quando o modo precisa;
  Race usa fila, tempos, rota e câmera, enquanto CTF usa estado e descarte de
  bandeira ou runa;
- `INS` e `DEL`: adicionar e remover Frogbot quando a sessão começou com bots;
- `HOME` e `END`: aplicar habilidade um nível abaixo ou acima da escolhida no
  launcher.
- `F12`: fechar diretamente o QuakeWorld.

Os binds gerenciados são carregados antes de `x86qw-ktx-user.cfg`. Assim, uma
configuração pessoal pode sobrescrevê-los; o launcher muda apenas os aliases
contextuais e não reescreve o arquivo pessoal.

[straw-hats]: https://one-piece.com/character/luffy/index.html
[roger]: https://one-piece.com/character/Gold_Roger/index.html
[whitebeard]: https://one-piece.com/character/edward_newgate/index.html
[shanks]: https://one-piece.com/character/Shanks/index.html
[blackbeard]: https://one-piece.com/character/Marshall_D_Teech/index.html
[mihawk]: https://one-piece.com/character/Dracule_Mihawk/index.html
[ezquake-charsets]: https://ezquake.com/docs/charsets.html
