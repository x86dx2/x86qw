# Roadmap do x86QW

## Modo operacional vigente

O projeto está em `owner-only`: um usuário, um mantenedor e reinstalação limpa
permitida. A política está formalizada no
[`ADR 0008`](adr/0008-owner-only-release-gates.md). Migração histórica, soak
para usuários externos e operação TUF sustentável continuam no roadmap, mas
não bloqueiam a primeira instalação desta fase.

Este é o índice estratégico da jornada da baseline pública `0.7.13` até
`1.0.0`. O estado operacional presente está em
[PROJECT-STATUS.md](PROJECT-STATUS.md); a execução detalhada, a auditoria das
branches e os gates estão em
[implementation/stabilization-1.0-plan.md](implementation/stabilization-1.0-plan.md).
As notas de release históricas, o RC público e o rascunho de `1.0.0` ficam em
[releases/](releases/).

## Autoridade e baseline

`origin/main` é a linha canônica. A baseline estável pública é
`x86qw-installer-0.7.13`, preparada no commit
`04a55aed8711ec5466dc70f0e33a591d92e07ccb`; a correção posterior em `main`
alinha os metadados compartilhados. A baseline preserva os bundles anteriores,
os cinco jogos, os runtimes declarados e a distinção entre contratos portáveis e
execução nativa. O Release Candidate público separado é
`x86qw-installer-1.0.0-rc.1`; a release final owner-only
`x86qw-installer-1.0.0` também está publicada e pode ser usada pelo
mantenedor. O soak externo permanece estacionado e só volta a ser gate quando
a audiência mudar para `external-public`.
O checkpoint
`codex/stabilize-1.0@30e9d5b` é somente material de extração; não é merge,
aprovação nem release.

O roadmap principal responde “em que ordem e sob quais gates”. O status
responde “o que é verdade agora”. O plano detalhado responde “como cada fase
será extraída, testada e aprovada”. Nenhum documento autoriza publicação por si
só.

## Jornada até 1.0

1. **PR A — verdade de plataforma:** separar artefato, suporte e validação;
   classificar estados de plataforma e preservar a CI portável. Publicar a
   `0.7.4` somente em uma PR de release posterior.
2. **PR B — governança (`#55`):** consolidar licença, avisos, ownership,
   Dependabot, lockfile, threat model e runbooks.
3. **PR C — contratos (`#53`):** congelar SemVer, schemas, envelopes JSON,
   redaction e códigos; alvo sugerido `0.8.0`.
4. **PR D — migração (`#46`):** capacidade preservada para a transição
   `external-public`, cobrindo os estados publicados `0.7.0–0.7.13`; não é
   gate da primeira instalação `owner-only`.
5. **PRs E1/E2 — trust (`#48`):** aprovar a arquitetura e só depois
   implementar a cadeia de confiança; alvo sugerido `0.9.0`.
6. **PR F — candidato imutável (`#51`):** construir uma vez, fixar Actions,
   conferir ownership/SBOM/provenance/mirrors e falhar fechado sem evidência.
7. **RC `1.0.0-rc.1` — concluído:** candidato imutável executado no M3,
   evidência assinada, publicação GitHub/GitLab e metadata-last verificados.
8. **Aceitação owner-only — concluída:** instalação limpa no M3, lifecycle
   descartável, Frogbot, update, repair, purge e aceitação pública protegida
   pelo endpoint; evidência M3 e candidato imutável estão vinculados aos runs
   registrados. A evidência durável foi materializada pela promoção final.
9. **PR H — `1.0.0` owner-only — concluído:** o candidato final foi promovido
   sem rebuild, com mirrors convergentes, assets de evidência duráveis,
   metadata consistente e aceitação pública no M3. O registro está em
   [`1.0.0-owner-only-publication-2026-08-15.md`](releases/1.0.0-owner-only-publication-2026-08-15.md).
10. **Transição external-public:** quando declarada pelo mantenedor, reativar
    migração, aceitação de usuários externos, soak protegido e operação TUF
    sustentável antes da promoção pública.

Cada item exige issue antes da branch, uma frente estrutural por vez e release
separada da implementação. O plano é a autoridade para dependências, extração
seletiva e limites de aprovação.

## Gates comuns

- suporte `supported`/`conditional` só com evidência nativa do candidato exato;
  `preview` não significa smoke executado;
- Linux, Windows, macOS Intel e nightly começam como `preview`; o stable
  macOS pode permanecer `conditional` por assinatura/notarização;
- workflows portáveis e o executor M3 são gates separados; jobs portáveis não
  são smokes nativos;
- trust de produção, evidência M3, hashes, SBOM, provenance e mirrors são
  gates independentes e falham fechados;
- o RC publicado não autoriza automaticamente a promoção final; qualquer
  alteração nos bytes de produto exige um novo candidato. O reinício do soak
  aplica quando a audiência for `external-public`;
- `owner-only` exige instalação limpa e não exige migração histórica;
- `external-public` exige explicitamente migração, soak e operação TUF
  sustentável;
- nenhuma promoção ou publicação `1.0` ocorre antes dos gates; releases
  corretivas permanecem separadas das PRs de implementação.

## Depois de 1.0

Central de demos, validação formal de MVD/QWD, treinamento como comando,
clientes ou engines novos, mods/mapas externos, serviços persistentes do
sistema e perfis operacionais adicionais exigem propostas próprias, artefatos,
contratos, migração, testes, evidência de plataforma e aprovação de release.
Eles não entram como efeito colateral de `play`, `host`, `update` ou `upgrade`.

Para a visão de longo prazo do ecossistema, consulte
[ROADMAP-QUAKE-ECOSYSTEM.md](ROADMAP-QUAKE-ECOSYSTEM.md). Para o estado de hoje,
consulte [PROJECT-STATUS.md](PROJECT-STATUS.md).
