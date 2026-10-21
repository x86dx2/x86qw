---
name: x86QW
description: Modern, reproducible and auditable QuakeWorld for three systems.
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

**Creative North Star: "An inventory that runs"**

Someone opens x86QW in the browser, in daylight, with the terminal beside it, and
needs to decide in a few seconds whether this distribution really plays, what
lands on the disk and whether its origin can be checked. The interface behaves like a
living inventory: name, version, provenance and a command. No starting grid,
no arena, no tournament poster.

The composition is light, typographic and asymmetric only when the content asks for it.
A tungsten band keeps the visual gesture for the install command.
The rest is paper, ink and evidence.

**Key Characteristics:**

- One type family, with contrast of weight, not of costume.
- Games and versions as the poster; the command as the only dark field.
- Technical evidence presented as editorial content, never as ornament.
- Discreet corners, 1px lines and no decorative shadow.
- Motion limited to state feedback (copy, catalog).

## Colors

The palette is restrained: true white, ink with a trace of oxide, and true
oxide only for state. Neutrals inherit hue 28 at minimal chroma so they
look neither like dead gray nor like cream paper.

### Primary

- **Ink:** text, wordmark, primary button. Almost black, slightly oxide.
- **Tungsten:** the band reserved for the install command.

### Secondary

- **Oxide:** visible focus, catalog in preparation, copy confirmation.
  Never the wordmark, never decoration, never an eyebrow.

### Tertiary

- **Success / Error:** rare catalog states, always paired with text.

### Neutral

- **Paper:** main background, chroma 0.
- **Surface:** status panels, minimal chroma towards oxide.
- **Line:** 1px dividers.
- **Secondary ink:** supporting body text, contrast ≥ 4.5:1.

### Named Rules

**The Signal Rule.** Oxide communicates state, focus or a decision; it is never branding.

**The Daylight Rule.** The main content stays readable under ordinary ambient
light; the minimum contrast is 4.5:1 and priority body text aims for 7:1.

**The One Band Rule.** Only the install block uses a dark surface.

## Typography

**Family:** Atkinson Hyperlegible (with Arial and sans-serif), weights 400 and 700.

**Character:** distinct letters and digits in versions, hashes and instructions.
Hierarchy comes from size and weight, not from a second, condensed family.

### Hierarchy

- **Display** (700, `clamp(2.4rem, 5vw, 4rem)`, 1.05, `-0.03em`): H1.
- **Headline** (700, `clamp(1.85rem, 3.2vw, 2.75rem)`, 1.15): section H2.
- **Title** (700, `clamp(1.35rem, 2vw, 1.75rem)`, 1.2): game names and H3.
- **Body** (400, `1rem`, 1.6): explanations, with a maximum width between 65 and 75ch.
- **Label** (700, `0.95rem`): short signals in sentence case.

### Named Rules

**The Single Family Rule.** Display and body use Atkinson. Monospace
only in literal commands.

**The Compression Rule.** Heading letter-spacing never below `-0.04em`.
Upper case is not section grammar.

## Elevation

The system is flat by default. Depth comes from color and one-pixel dividers;
there are no shadows. An element should look interactive through state, contrast and
focus, not by floating.

### Named Rules

**The Grounded Rule.** Decorative shadows are forbidden. Borders and a change of
surface handle separation; focus uses a 3px oxide outline (white on
the dark band).

## Components

### Buttons

- **Shape:** rectangular and precise, 3px radius.
- **Primary:** ink, white text, minimum height 3.25rem, weight 700.
- **Hover / Focus:** hover darkens the ink; focus gets a 3px oxide outline.
  No `translateY`.
- **Secondary:** white, line and ink; never uses a shadow.

### Chips

- **Style:** used only for real state, with a signal dot and explicit text.
- **State:** color complements the message but never replaces the written label.

### Cards / Containers

- **Corner Style:** functional panels use 6px; large sections stay square.
- **Background:** Surface for state; tungsten only for the command.
- **Shadow Strategy:** no shadow.
- **Border:** a full 1px line only when it defines a control or a panel.

### Inputs / Fields

- **Style:** the public site has no fields yet; future fields should inherit 3px and a white surface.
- **Focus:** a 3px oxide outline with a 4px offset.

### Navigation

A white header at the top, brand on the left, the Install action and the catalog status
on the right. No section anchors that only duplicate scrolling.

### Catalog Status

A panel tied to the public catalog. It shows loading, preparation, published or error
with equivalent text; the colored dot is always redundant.

### Command Band

A full-width tungsten band. Two copyable commands, one per shell.
It is the page's only committed color field.

## Do's and Don'ts

### Do:

- **Do** start the page with the five games and the command.
- **Do** use oxide only for active information, focus and copy.
- **Do** keep headings below 4rem and letter-spacing never under `-0.04em`.
- **Do** keep visible focus, keyboard navigation and reduced motion.
- **Do** show status, license, origin and hash in direct, verifiable language.

### Don't:

- **Don't** use heavy pixel nostalgia, neon/cyberpunk gamer aesthetics, a SaaS landing page or an arena/starting-grid metaphor.
- **Don't** use gradient text, glassmorphism, diagonal stripes or glow.
- **Don't** repeat grids of identical cards with an icon, a title and a paragraph.
- **Don't** use corners above 16px, combine a border with a shadow, or paint the wordmark in the accent.
- **Don't** use color as the only indicator or hide the distribution's incomplete state.
- **Don't** use a condensed font, upper-case eyebrows on every section, or 01/02/03 numbering outside the real install flow.
