# Product

## Register

brand

## Users

People discovering QuakeWorld and veteran players who want a current,
reliable, cross-platform way to install, update and preserve the game. Newcomers
need to understand quickly what the project is and how to start; those who already
play need to recognize technical transparency, control and respect for the ecosystem.

## Product Purpose

Spread QuakeWorld and present x86QW as a modern, reproducible and auditable
distribution. The site should turn curiosity into trust:
show the games first, make the real state of the distribution visible and
lead the visitor to the install without hiding provenance, actual support
or limits.

## Brand Personality

Direct, precise and trustworthy. The voice is objective, with the energy of a
well-kept tool and the clarity of an inventory you can check. The result should
feel contemporary without erasing QuakeWorld's history.

## Anti-references

Do not look like heavy pixel nostalgia, neon/cyberpunk gamer aesthetics or a
generic SaaS landing page. Also avoid ornamental futurism, promotional jargon,
interfaces full of identical cards, arena/starting-grid metaphors and any attempt
to hide platform or validation limits.

## Public facts

Version, counts, commands, games, runtimes and platforms come from the generated
projection `public/api/v1/product.json`. Its source is the canonical set in
`maintenance/inventory/` and the package catalog, including the `x86qw`
CLI binaries.
The site's text must not keep diverging numbers: a Go test compares the
counts and the CLI version quoted on the page with that projection and the catalog,
and the full validation rejects an outdated JSON.

The current product offers five games, KTX with modes and Frogbots, hosting with
MVDSV, the QTV relay and the QWFWD proxy. The macOS client is universal; the three services
on macOS are arm64. Linux amd64 and Windows x64 have the client and the services.

## Design Principles

- Start with the game: the first screen names the titles, the app and the command.
  Infrastructure comes after the evidence that this plays.
- Highlight concrete advantages: five games in one tree, `play` / `host` / `hub`,
  a catalog with SHA-256 and TUF, stable and nightly side by side, PAKs that still
  belong to their owner.
- Prove trust: versions, hashes, licenses and catalog status are readable
  evidence, not a slogan.
- Be honest about the stage: clearly tell apart what already works, what
  is in preview or audit, and what is owner-only.
- Modernize without changing character: a current visual language that does not imitate
  retro interfaces, gamer trends or tournament posters.

## Accessibility & Inclusion

Meet WCAG 2.2 AA. Guarantee full keyboard navigation, visible focus,
a minimum contrast of 4.5:1 for regular text, semantic structure, alternative text
and information never conveyed by color alone. Respect `prefers-reduced-motion`
and keep all the content when animations are turned off.
