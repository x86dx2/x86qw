# Issue backlog proposto — M3-first

Este backlog é local e executável por um mantenedor único. Ele separa o que estabiliza a instalação owner-only no Apple M3 do que só faz sentido quando houver audiência externa. Nenhum item abaixo implica criação automática de issue remota.

## Top 20 priorizado

| ID | Classe | Título | Dependências | Versão/audiência |
| --- | --- | --- | --- | --- |
| S0-001 | NOW/P0 | Manter main verde e preservar o contrato DNS determinístico | nenhuma | sem release se test-only / owner-only |
| S0-002 | DONE/P1 | Verificar convergência da projeção site, bootstraps, product e release-truth | S0-001 | documental/deploy protegido / owner-only (receipt 31965901520) |
| S0-003 | DONE/P1 | Aceitação do candidato exato no Apple M3 | S0-001, S0-002 | 1.0.x se bytes mudarem / owner-only (E3 25/25) |
| S0-004 | NOW/P1 | Renovação e monitor técnico da lease TUF | nenhuma | operação contínua / owner-only |
| S0-005 | NEXT/P1 | Observação operacional owner-only e registro de incidentes | S0-003, S0-004 | 1.0.x / owner-only (CLI 1.0.2 zipapp `cd37e868…`; host/ui PASS 2026-08-17T01:53Z; `uninstall` conservador PASS 2026-08-17T02:09Z em `~/Games/x86qw` — PAKs/pessoais preservados, cache Darwin intacto; `--purge` não exercitado; 25/25 do digest 1.0.2 `35e77723…`) |
| FUNC-001 | DONE/P1 | Implementar `x86qw doctor` read-only | S0-003 | 1.0.x / owner-only (PR #191) |
| FUNC-002 | DONE/P1 | Gerar bundle de diagnóstico sanitizado e revisável | FUNC-001 | 1.0.x / owner-only (PR #191) |
| FUNC-003 | DONE/P1 | Separar perfis, defaults, cache e dados pessoais | S0-003 | 1.0.x / owner-only (PR #193) |
| FUNC-004 | DONE/P1 | Favoritos e recentes locais com origem e freshness | FUNC-003 | 1.0.x / owner-only (PR #194) |
| FUNC-005 | DONE/P1 | Descoberta e entrada em partidas com fallback local | FUNC-004 | 1.0.x / owner-only (PR #194) |
| FUNC-006 | DONE/P1 | Presets declarativos de hospedagem local | S0-003 | 1.0.x / owner-only (PR #195) |
| FUNC-007 | DONE/P1 | Readiness, logs, stop e crash recovery de servidor | FUNC-006 | 1.0.x / owner-only (`status` / `--stop` / journal) |
| FUNC-008 | DONE/P2 | UI local read-only sobre os contratos do runtime | FUNC-001, FUNC-005 | 1.0.x / owner-only (PR #199) |
| OPS-001 | LATER/P1 | Custódia independente, backup e RTO TUF | S0-004 | external-public only |
| OPS-002 | LATER/P1 | Soak de sete dias do candidato external-public exato | decisão EP-0 | external-public only |
| OPS-003 | LATER/P1 | Aceitação por usuário não mantenedor | OPS-002 | external-public only |
| OPS-004 | LATER/P1 | Migração histórica real | promessa de upgrade antigo | external-public only, condicional |
| OPS-005 | LATER/P1 | Evidência nativa por plataforma adicional | laboratório nativo | external-public por plataforma |
| EXT-001 | BLOCKED_EXTERNAL | RFC e adapter oficial QWLeague | documentação/autorização oficial | opcional / 1.3 |

## Ordem de execução

1. S0 está PASS no M3; manter main verde e a lease TUF.
2. F1–F3 e FUNC-008 entram na fonte **1.0.2** (sem linha 1.1 nesta fase).
3. A `1.0.1` e a `0.7.13` deixam de ser current; permanecem históricas no catálogo.
4. Continuar a observação owner-only (S0-005) sem transformá-la em soak.
5. Só reabrir OPS-001 a OPS-005 quando a audiência mudar para external-public.

## Regra sobre migração

A migração de 0.7.13 não é requisito da primeira publicação aberta se essa publicação declarar uma baseline nova e instalação limpa. Ela volta ao backlog somente se o produto prometer atualizar instalações históricas ou preservar dados de terceiros. O dado pessoal do próprio mantenedor continua protegido em qualquer upgrade que for oferecido.

## Disposição das issues existentes

- `#143`: histórico RC; não é aprovação para bytes novos.
- `#146`: estacionar como migração condicional external-public.
- `#148`: manter como custódia, backup, RTO e sucessão external-public; o drill técnico local já existe.
- `#150`: somente após preservar refs de evidência.
- `#151`: decisão futura de imutabilidade host-level; não bloqueia funcionalidades M3.
- `#152`: alerta histórico encerrado; manter monitor técnico recorrente.
- `#175`: usar para observação owner-only no M3, sem transformá-la em soak externo.

## Definition of Ready

Cada issue concreta precisa declarar outcome, não escopo, dependências, plataforma, segurança, privacidade, testes determinísticos, rollback, docs, Maker, Checker, versão e audiência. Implementação, publicação e promoção de audiência ficam separadas.

## Definition of Done comum

- evidência ligada ao commit, run, digest ou endpoint correto;
- Checker tenta refutar o resultado;
- nenhum claim acima da evidência nativa;
- rollback diagnosticável;
- dados pessoais separados de defaults gerenciados e cache;
- indisponibilidade externa não impede instalação, jogo local ou hosting;
- main verde sem depender de rerun;
- release sem mudança artificial quando somente teste ou documentação mudou.
