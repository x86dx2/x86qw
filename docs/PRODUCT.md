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
- Mostrar um resumo verificável e pedir confirmação antes de iniciar gameplay,
  conectar pelo Hub, iniciar um serviço isolado ou subir uma stack de hospedagem.
- Preservar o resultado de cada ação até confirmação do usuário, antes de
  redesenhar o menu que a iniciou.
- Permitir consultar a stack ativa, seus endpoints e parâmetros não sensíveis
  sem interromper nem alterar os serviços, e oferecer uma ação separada e
  confirmada para encerrá-la coordenadamente.
- Permitir primeiro ou segundo plano sem criar dois modelos de lifecycle: ambos
  usam o mesmo lock, journal, readiness e cleanup.
- Oferecer um caminho rápido com padrões seguros e um caminho avançado sem
  misturar decisões de gameplay e infraestrutura.
- Preservar os comandos existentes como contrato estável para usuários avançados e automação.

## Accessibility & Inclusion

Garantir operação completa por teclado, fallback numerado sem TTY, foco textual
visível, informação independente de cor, suporte a `NO_COLOR` e linguagem em
português. A interface deve funcionar em terminais Unix e Windows e permanecer
legível em larguras reduzidas.
