---
name: x86QW
description: QuakeWorld moderno, reproduzível e auditável para três sistemas.
colors:
  bg: "oklch(1 0 0)"
  surface: "oklch(0.96 0.008 268.5)"
  surface-strong: "oklch(0.91 0.018 268.5)"
  ink: "oklch(0.18 0.025 268.5)"
  muted: "oklch(0.43 0.035 268.5)"
  primary: "oklch(0.43 0.15 268.5)"
  primary-deep: "oklch(0.27 0.105 268.5)"
  primary-light: "oklch(0.82 0.075 268.5)"
  accent: "oklch(0.58 0.20 34)"
  accent-deep: "oklch(0.47 0.17 34)"
  accent-on-dark: "oklch(0.68 0.18 34)"
  accent-pale: "oklch(0.94 0.035 34)"
  line: "oklch(0.84 0.018 268.5)"
  arena-grid: "oklch(0.72 0.08 268.5 / 0.2)"
  arena-floor: "oklch(0.36 0.13 268.5 / 0.78)"
  success: "oklch(0.52 0.16 150)"
  error: "oklch(0.48 0.18 25)"
  white: "oklch(1 0 0)"
typography:
  display:
    fontFamily: "Barlow Condensed, Arial Narrow, sans-serif"
    fontSize: "clamp(4rem, 8vw, 6rem)"
    fontWeight: 800
    lineHeight: 0.94
    letterSpacing: "-0.025em"
  body:
    fontFamily: "Atkinson Hyperlegible, Arial, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.6
  label:
    fontFamily: "Barlow Condensed, Arial Narrow, sans-serif"
    fontSize: "1rem"
    fontWeight: 800
    lineHeight: 1.2
    letterSpacing: "0.06em"
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
    backgroundColor: "{colors.primary}"
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

**Creative North Star: "Painel de Largada"**

Uma pessoa nova ou veterana abre o x86QW em um notebook ou telefone, sob luz
ambiente comum, e precisa decidir em poucos segundos se essa distribuição merece
confiança. A interface se comporta como um painel de largada: ritmo forte,
informação inequívoca e sinais que existem para orientar, não para decorar.

A composição alterna campos claros de leitura com planos índigo comprometidos,
tipografia condensada de competição e coral de sinal. Ela rejeita nostalgia
pixelada excessiva, estética gamer neon/cyberpunk e landing page SaaS genérica.

**Key Characteristics:**

- Escala tipográfica decidida e leitura imediata.
- Assimetria controlada, com alternância entre informação e sinal visual.
- Evidência técnica apresentada como conteúdo editorial, nunca como ornamento.
- Cantos discretos, linhas estruturais e nenhuma sombra decorativa.
- Movimento limitado ao percurso do sinal e ao retorno de estado.

## Colors

A paleta combina o Índigo de Largada com Coral de Sinal sobre branco real; os
neutros herdam discretamente o matiz índigo para manter unidade sem parecer bege.

### Primary

- **Índigo de Largada:** ocupa o campo visual da arena, botões principais e títulos decisivos.
- **Índigo Profundo:** sustenta áreas de confiança, texto sobre coral e camadas escuras.
- **Índigo de Telemetria:** mantém texto secundário legível sobre campos escuros.

### Secondary

- **Coral de Sinal:** marca estado, percurso, chamadas finais e mudanças importantes.
- **Coral de Leitura:** variante clara exclusiva para rótulos pequenos sobre índigo profundo.
- **Coral de Contexto:** fundo pálido para a entrada destinada a novos jogadores.

### Tertiary

- **Arena Grid / Arena Floor:** geometria interna exclusiva do visual do hero.
- **Sucesso / Erro:** estados raros do catálogo, sempre acompanhados por texto.

### Neutral

- **Branco de Campo:** fundo principal sem tingimento ou textura.
- **Tinta Fria:** texto principal quase preto com leve parentesco ao índigo.
- **Superfície de Inventário:** painéis de estado e controles discretos.
- **Linha de Medição:** divisores, matrizes e limites funcionais.

### Named Rules

**The Signal Rule.** Coral sempre comunica estado, percurso ou decisão; nunca é
espalhado como decoração.

**The Daylight Rule.** O conteúdo principal permanece legível sob luz ambiente
comum; contraste mínimo é 4.5:1 e texto comum prioritário busca 7:1.

## Typography

**Display Font:** Barlow Condensed (com Arial Narrow e sans-serif)

**Body Font:** Atkinson Hyperlegible (com Arial e sans-serif)

**Character:** Barlow Condensed fornece velocidade e compactação de uma placa de
competição; Atkinson Hyperlegible mantém letras e números distintos em textos,
versões, hashes e instruções.

### Hierarchy

- **Display** (800, `clamp(4rem, 8vw, 6rem)`, 0.94): manchetes e decisões de uma linha visual.
- **Headline** (600–800, `clamp(3.1rem, 6vw, 5.5rem)`, 0.94): títulos de seção com contraste de peso.
- **Title** (800, `clamp(2.4rem, 4vw, 4rem)`, 1): divisões de público e blocos de contexto.
- **Body** (400, `1rem`, 1.6): explicações com largura máxima entre 65 e 75 caracteres.
- **Label** (800, `1rem`, `0.06em`): sinais curtos; caixa alta é reservada a poucos marcadores.

### Named Rules

**The Compression Rule.** A fonte condensada serve hierarquia e sinalização;
parágrafos e instruções nunca usam a fonte de display.

## Elevation

O sistema é plano por padrão. Profundidade vem de cor, sobreposição geométrica e
divisores de um pixel; não há sombras. Um elemento deve parecer interativo por
estado, contraste e foco, não por flutuar artificialmente sobre a página.

### Named Rules

**The Grounded Rule.** Sombras decorativas são proibidas. Bordas e mudança de
superfície resolvem separação; foco usa um contorno coral de 3px.

## Components

### Buttons

- **Shape:** retangular e preciso, com curva mínima (3px).
- **Primary:** Índigo de Largada, texto branco, altura mínima de 3.25rem e peso 700.
- **Hover / Focus:** escurece para Índigo Profundo e sobe 2px; foco recebe contorno coral de 3px.
- **Secondary:** branco, linha de medição e tinta fria; nunca usa sombra.

### Chips

- **Style:** usados apenas para estado real, com ponto de sinal e texto explícito.
- **State:** cor complementa a mensagem, mas nunca substitui o rótulo escrito.

### Cards / Containers

- **Corner Style:** painéis funcionais usam 6px; grandes seções permanecem retas.
- **Background:** Superfície de Inventário para estado, índigo para confiança e coral pálido para entrada.
- **Shadow Strategy:** nenhuma sombra.
- **Border:** linha integral de 1px somente quando define um controle ou painel.
- **Internal Padding:** 1.5rem em painéis; seções usam espaçamento fluido.

### Inputs / Fields

- **Style:** ainda não existem campos no site público; futuros campos devem herdar 3px e superfície branca.
- **Focus:** contorno coral de 3px com offset de 4px.
- **Error / Disabled:** texto obrigatório acompanha cor e o contraste permanece AA.

### Navigation

Cabeçalho branco fixo no topo, marca à esquerda, links centrais e estado à direita.
Em telas estreitas, os links de seção saem e o acesso ao estado permanece visível.

### Arena Signal

Visualização geométrica exclusiva do hero. Um percurso coral liga origem, hash e
spawn sobre um mapa abstrato; a animação some por completo em movimento reduzido.

### Catalog Status

Painel ligado ao catálogo público. Exibe carregando, preparação, publicado ou erro
com texto equivalente; o ponto colorido é sempre redundante.

## Do's and Don'ts

### Do:

- **Do** usar Índigo de Largada em campos grandes e Coral de Sinal apenas para informação ativa.
- **Do** manter títulos condensados abaixo de 6rem e espaçamento de letras nunca menor que `-0.04em`.
- **Do** preservar foco visível, navegação por teclado e movimento reduzido em toda interação.
- **Do** mostrar estado, licença, origem e hash com linguagem direta e verificável.
- **Do** variar o ritmo entre seções claras, campos cromáticos e matrizes funcionais.

### Don't:

- **Don't** usar nostalgia pixelada excessiva, estética gamer neon/cyberpunk ou landing page SaaS genérica.
- **Don't** usar gradiente em texto, glassmorphism, listras diagonais ou glow.
- **Don't** repetir grades de cartões idênticos com ícone, título e parágrafo.
- **Don't** aplicar cantos acima de 16px em painéis ou combinar borda com sombra ampla.
- **Don't** usar cor como único indicador ou esconder o estado ainda incompleto da distribuição.
