---
name: RC soak
about: Registrar o período de uso e a aceitação operacional de um release candidate
title: "[P1] RC soak — x86QW 1.0.0-rc.x"
labels: "release"
assignees: ""
---

## Identidade

- release: `x86qw-installer-1.0.0-rc.x`
- commit do produto: `<SHA completo>`
- início UTC: `<YYYY-MM-DD>`
- período mínimo recomendado: sete dias completos
- registro: `docs/releases/<versão>-soak.md`
- issue criada para este ciclo: `<número>`

## Checklist

- [ ] aceitação pública pelo endpoint no Apple M3;
- [ ] migração real 0.7.13 → RC;
- [ ] Frogbot real;
- [ ] update/upgrade apply;
- [ ] repair por corrupção;
- [ ] purge;
- [ ] TUF sem expiração e drill de recuperação;
- [ ] sete dias completos sem P0/P1;
- [ ] recibos e hashes públicos anexados;
- [ ] aprovação humana para encerrar.

## Diário

| UTC | Host | Fluxo | Resultado | Link da evidência |
|---|---|---|---|---|
| | | | | |

## Incidentes e reinícios

Registre aqui qualquer P0/P1, mudança de bytes, `rc.2` ou reinício do período.
