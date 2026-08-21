---
name: x86QW
description: QuakeWorld moderno, reproduzível e auditável para três sistemas.
colors:
  bg: "oklch(1 0 0)"
  surface: "oklch(0.97 0.006 28)"
  ink: "oklch(0.22 0.01 28)"
  muted: "oklch(0.42 0.012 28)"
  accent: "oklch(0.48 0.16 28)"
  band: "oklch(0.22 0.01 70)"
  band-ink: "oklch(0.97 0.005 70)"
  line: "oklch(0.88 0.008 28)"
  success: "oklch(0.48 0.14 150)"
  error: "oklch(0.48 0.18 25)"
  white: "oklch(1 0 0)"
typography:
  display:
    fontFamily: "Atkinson Hyperlegible, Arial, sans-serif"
    fontSize: "clamp(2.4rem, 5vw, 4rem)"
    fontWeight: 700
    lineHeight: 1.05
    letterSpacing: "-0.03em"
  body:
    fontFamily: "Atkinson Hyperlegible, Arial, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.6
  label:
    fontFamily: "Atkinson Hyperlegible, Arial, sans-serif"
    fontSize: "0.95rem"
    fontWeight: 700
    lineHeight: 1.3
    letterSpacing: "0"
rounded:
  control: "3px"
  panel: "6px"
  round: "50%"
spacing:
  xs: "0.75rem"
  sm: "1.25rem"
  md: "2rem"
  lg: "4rem"
  xl: "8rem"
components:
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.white}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0.82rem 1.15rem"
    height: "3.25rem"
  button-quiet:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0.82rem 1.15rem"
    height: "3.25rem"
  status-panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "1.5rem"
---

# Design System: x86QW

## Overview

**Creative North Star: "Inventário que executa"**

Uma pessoa abre o x86QW no browser, à luz do dia, com o terminal ao lado, e
precisa decidir em poucos segundos se esta distribuição joga de verdade, o que
entra no disco e se a origem dá para conferir. A interface se comporta como um
inventário vivo: nome, versão, proveniência e um comando. Sem painel de largada,
sem arena, sem cartaz de competição.

A composição é clara, tipográfica e assimétrica só quando o conteúdo pede.
Uma faixa de tungsténio reserva o gesto visual para o comando de instalação.
O restante é papel, tinta e evidência.

**Key Characteristics:**

- Uma família tipográfica com contraste de peso, não de costume.
- Jogos e versões como o cartaz; o comando como o único campo escuro.
- Evidência técnica apresentada como conteúdo editorial, nunca como ornamento.
- Cantos discretos, linhas de 1px e nenhuma sombra decorativa.
- Movimento limitado ao retorno de estado (copiar, catálogo).

## Colors

A paleta é restrained: branco real, tinta com um traço de óxido, e óxido
verdadeiro só para estado. Neutros herdam o matiz 28 em chroma mínima para
não parecer cinza morto nem papel creme.

### Primary

- **Tinta:** texto, wordmark, botão principal. Quase preta, levemente óxido.
- **Tungsténio:** faixa exclusiva do comando de instalação.

### Secondary

- **Óxido:** foco visível, catálogo em preparação, confirmação de cópia.
  Nunca wordmark, nunca decoração, nunca eyebrow.

### Tertiary

- **Sucesso / Erro:** estados raros do catálogo, sempre acompanhados por texto.

### Neutral

- **Papel:** fundo principal, chroma 0.
- **Superfície:** painéis de estado, chroma mínima rumo ao óxido.
- **Linha:** divisores de 1px.
- **Tinta secundária:** corpo auxiliar, contraste ≥ 4.5:1.

### Named Rules

**The Signal Rule.** Óxido comunica estado, foco ou decisão; nunca é marca.

**The Daylight Rule.** O conteúdo principal permanece legível sob luz ambiente
comum; contraste mínimo é 4.5:1 e texto comum prioritário busca 7:1.

**The One Band Rule.** Só o bloco de instalação usa superfície escura.

## Typography

**Family:** Atkinson Hyperlegible (com Arial e sans-serif), pesos 400 e 700.

**Character:** letras e números distintos em versões, hashes e instruções.
Hierarquia vem de tamanho e peso, não de uma segunda família condensada.

### Hierarchy

- **Display** (700, `clamp(2.4rem, 5vw, 4rem)`, 1.05, `-0.03em`): H1.
- **Headline** (700, `clamp(1.85rem, 3.2vw, 2.75rem)`, 1.15): H2 de secção.
- **Title** (700, `clamp(1.35rem, 2vw, 1.75rem)`, 1.2): nomes de jogo e H3.
- **Body** (400, `1rem`, 1.6): explicações com largura máxima entre 65 e 75ch.
- **Label** (700, `0.95rem`): sinais curtos em sentence case.

### Named Rules

**The Single Family Rule.** Display e corpo usam Atkinson. Monoespaçada
só em comandos literais.

**The Compression Rule.** Letter-spacing de títulos nunca abaixo de `-0.04em`.
Caixa alta não é gramática de secção.

## Elevation

O sistema é plano por padrão. Profundidade vem de cor e divisores de um pixel;
não há sombras. Um elemento deve parecer interativo por estado, contraste e
foco, não por flutuar.

### Named Rules

**The Grounded Rule.** Sombras decorativas são proibidas. Bordas e mudança de
superfície resolvem separação; foco usa um contorno óxido de 3px (branco sobre
a faixa escura).

## Components

### Buttons

- **Shape:** retangular e preciso, curva 3px.
- **Primary:** tinta, texto branco, altura mínima 3.25rem, peso 700.
- **Hover / Focus:** hover escurece a tinta; foco recebe contorno óxido de 3px.
  Sem `translateY`.
- **Secondary:** branco, linha e tinta; nunca usa sombra.

### Chips

- **Style:** usados apenas para estado real, com ponto de sinal e texto explícito.
- **State:** cor complementa a mensagem, mas nunca substitui o rótulo escrito.

### Cards / Containers

- **Corner Style:** painéis funcionais usam 6px; grandes seções permanecem retas.
- **Background:** Superfície para estado; tungsténio só no comando.
- **Shadow Strategy:** nenhuma sombra.
- **Border:** linha integral de 1px somente quando define um controle ou painel.

### Inputs / Fields

- **Style:** ainda não existem campos no site público; futuros campos devem herdar 3px e superfície branca.
- **Focus:** contorno óxido de 3px com offset de 4px.

### Navigation

Cabeçalho branco no topo, marca à esquerda, ação Instalar e estado do catálogo
à direita. Sem âncoras de secção que só duplicam o scroll.

### Catalog Status

Painel ligado ao catálogo público. Exibe carregando, preparação, publicado ou erro
com texto equivalente; o ponto colorido é sempre redundante.

### Command Band

Faixa de tungsténio de largura total. Dois comandos copyáveis, um por shell.
É o único campo cromático comprometido da página.

## Do's and Don'ts

### Do:

- **Do** começar a página pelos cinco jogos e pelo comando.
- **Do** usar óxido apenas para informação ativa, foco e cópia.
- **Do** manter títulos abaixo de 4rem e espaçamento de letras nunca menor que `-0.04em`.
- **Do** preservar foco visível, navegação por teclado e movimento reduzido.
- **Do** mostrar estado, licença, origem e hash com linguagem direta e verificável.

### Don't:

- **Don't** usar nostalgia pixelada excessiva, estética gamer neon/cyberpunk, landing SaaS ou metáfora de arena/largada.
- **Don't** usar gradiente em texto, glassmorphism, listras diagonais ou glow.
- **Don't** repetir grades de cartões idênticos com ícone, título e parágrafo.
- **Don't** aplicar cantos acima de 16px, combinar borda com sombra, ou pintar o wordmark de acento.
- **Don't** usar cor como único indicador ou esconder o estado incompleto da distribuição.
- **Don't** usar fonte condensada, eyebrows em caixa alta em cada secção, ou numeração 01/02/03 fora do fluxo real de instalação.
