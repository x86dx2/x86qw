# Product

## Register

product

## Users

Pessoas descobrindo QuakeWorld e jogadores veteranos que desejam instalar,
atualizar, jogar e hospedar partidas sem memorizar a CLI. O fluxo principal deve
orientar iniciantes e continuar rápido para quem já conhece jogos, modos, mapas e
serviços.

## Product Purpose

Oferecer uma distribuição QuakeWorld moderna, reproduzível e auditável, com um
app de terminal que revela as opções relevantes no momento certo. O app é uma
porta de entrada para os mesmos comandos automatizáveis; ele não substitui nem
altera a semântica das flags públicas, e as ações que ele não faz sozinho rodam
os comandos `x86qw` equivalentes.

## Brand Personality

Direta, competitiva e confiável. A interface deve transmitir a velocidade do jogo
e a precisão de uma ferramenta bem mantida, sem sacrificar legibilidade.

## Anti-references

Não imitar launchers gráficos dentro do terminal, menus retrô decorativos,
estética gamer neon, árvores profundas sem contexto ou assistentes que escondem
as flags efetivamente usadas. Não exigir mouse nem depender de cor para comunicar
estado.

## Design Principles

- Começar pela intenção: no instalador, jogar, hospedar ou os dois; no app,
  Jogar, Servidores e Hospedar antes da manutenção.
- Usar divulgação progressiva: perguntar somente o que se aplica à escolha atual.
- Uma tela por vez, em sanfona, com o rodapé fixo dizendo as teclas daquele
  estado; nada muda de lugar enquanto a tela se atualiza.
- Diálogos usam espaço para mudar, Enter para confirmar e Esc para cancelar.
- Mostrar o que vai acontecer e pedir confirmação antes de instalar, atualizar,
  desinstalar ou encerrar uma stack de hospedagem.
- Permitir consultar a stack ativa, seus endpoints e parâmetros não sensíveis
  sem interromper nem alterar os serviços, e oferecer uma ação separada e
  confirmada para encerrá-la.
- Permitir primeiro ou segundo plano sem criar dois modelos de lifecycle: ambos
  usam o mesmo lock, journal, readiness e cleanup.
- Mostrar o comando e a receita equivalentes ao que foi escolhido, sem senhas.
- Preservar os comandos existentes como contrato estável para usuários avançados e automação.

## Accessibility & Inclusion

Garantir operação completa por teclado, foco visível, informação independente de
cor, suporte a `NO_COLOR` e a terminais sem glifos Unicode (`X86QW_GLYPHS=ascii`),
e português ou inglês conforme o sistema. Sem terminal, a CLI imprime a ajuda e
cada comando diz o que precisa. A interface deve funcionar em terminais Unix e
Windows e permanecer legível em larguras reduzidas.
