# Operating model do gauntlet

O gauntlet é o caminho mínimo para uma proposta atravessar evidência,
segurança e release. Ele não transforma o executor documental em autoridade de
produto.

## Papéis

| Papel | Responsabilidade | Pode fazer nesta issue |
| --- | --- | --- |
| Maker | prepara diff e evidências | criar/editar os artefatos locais aprovados |
| Checker | repete checks e aponta falhas | revisão independente posterior |
| Release owner | decide avanço, hold ou rollback | registrar decisão; não implícita |
| Custodian | guarda trust/recovery | ausente para produção nesta fotografia |
| Orchestrator | escolhe escopo e encadeia agentes | receber PLAN_DEVIATION/Evidence Pack |

Um mantenedor pode acumular Maker e owner-only operator, mas self-review não é
Checker independente para trust ou audiência externa.

## Passes

1. **Intake:** confirmar issue, branch, contrato e allowlist de caminhos.
2. **Baseline:** registrar status, branch, HEAD, dirty files, limitations e
   evidência anterior.
3. **Maker:** alterar somente o escopo; usar labels `VERIFIED FACT`,
   `INFERENCE`, `PROPOSAL`, `BLOCKED`.
4. **Static:** parse JSON, links relativos, `git diff --check`, allowlist e
   ausência de secrets/URLs não observadas.
5. **Semantic:** comparar hashes, gates, audiência, dependencies e claims com a
   baseline.
6. **Adversarial:** procurar claims de site/CWV/native não comprovados,
   receipt apontando RC, drift de audiência e campos incompletos.
7. **Decision:** Checker retorna findings; owner escolhe `advance`/`hold`;
   executor só corrige findings autorizados.
8. **Handoff:** Evidence Pack com baseline, changed files, diff summary,
   tests/results, limitations e deviations.

## Invariantes do gauntlet

- não criar issue remota para um item do backlog;
- não publicar código, workflow, release, trust metadata ou deploy nesta
  materialização; o commit/push da documentação desta branch só ocorre depois
  de Checker e Finalizer aprovarem o diff;
- não executar mutation externa autenticada;
- não elevar E1/E2 a E3/E4 por linguagem;
- não fechar risco por ausência de dados;
- não alterar `quake-world/` nem arquivos fora da allowlist.

## Artefato mínimo por item

Cada item do backlog deve ter title, problem, outcome, scope, non-scope,
dependencies, acceptance, gates, tests, platform, security, privacy, rollback,
docs, Maker, Checker, target version e audience. O [backlog.json](backlog.json)
é a fonte de estrutura; [ISSUE-BACKLOG.md](ISSUE-BACKLOG.md) é a leitura
humana.

## Rollback

Rollback documental é remover somente o diff desta issue em uma revisão
autorizada, preservando snapshots históricos. Rollback de produto, workflow,
release, trust ou site está fora do escopo e exige novo contrato.

## Gauntlet evidence ledger

| Evidência | Nível | Estado | Regra de uso |
| --- | --- | --- | --- |
| HEAD/main e run Validate | E1/E2 | MAIN RED | bloqueia 0A e feature work |
| focal DNS com relógio controlado | E2 local | hipótese test-only | ainda exige Windows 3.10/3.13 |
| TUF root/roles autenticadas | E1/E2 | HEALTHY no instante observado | não prova custody/recovery |
| installer GitHub/GitLab byte equality | E2 | convergente | não é rebuild E4 |
| candidato final no Apple M3 | E3 | 25/25 | não promove não-M3 |
| receipt final/public acceptance | E1/E2 | RC1 misturado | não aprova external-public |
| ownership/SBOM final | E1 | 87/87 NOASSERTION | gate 0C aberto |
| QWLeague discovery | E1 público | BLOCKED_EXTERNAL | sem scraping, auth ou claim |

Cada linha conserva commit, digest, run/endpoint, UTC e Checker. A ausência de
evidência é `BLOCKED`, nunca `PASS`.
