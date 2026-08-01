# Product

## Register

product

## Users

Pessoas descobrindo QuakeWorld e jogadores veteranos que desejam instalar,
atualizar, jogar e hospedar partidas sem memorizar a CLI. O fluxo principal deve
orientar iniciantes e continuar rápido para quem já conhece jogos, modos, mapas e
serviços.

## Product Purpose

Oferecer uma distribuição QuakeWorld moderna, reproduzível e auditável, com uma
interface de terminal que revela as opções relevantes no momento certo. O menu é
uma porta de entrada para os mesmos comandos automatizáveis; ele não substitui nem
altera a semântica das flags públicas.

## Brand Personality

Direta, competitiva e confiável. A interface deve transmitir a velocidade do jogo
e a precisão de uma ferramenta bem mantida, sem sacrificar legibilidade.

## Anti-references

Não imitar launchers gráficos dentro do terminal, menus retrô decorativos,
estética gamer neon, árvores profundas sem contexto ou assistentes que escondem
as flags efetivamente usadas. Não exigir mouse nem depender de cor para comunicar
estado.

## Design Principles

- Começar pela intenção: jogar, encontrar servidor, hospedar ou gerenciar.
- Usar divulgação progressiva: perguntar somente o que se aplica à escolha atual.
- Manter contexto visível por breadcrumbs, descrições e valores padrão claros.
- Oferecer busca e atalhos sem prejudicar a navegação básica por teclado.
- Preservar os comandos existentes como contrato estável para usuários avançados e automação.

## Accessibility & Inclusion

Garantir operação completa por teclado, fallback numerado sem TTY, foco textual
visível, informação independente de cor, suporte a `NO_COLOR` e linguagem em
português. A interface deve funcionar em terminais Unix e Windows e permanecer
legível em larguras reduzidas.
