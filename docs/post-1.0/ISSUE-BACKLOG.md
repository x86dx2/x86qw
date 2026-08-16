# Issue backlog proposto — M3-first

Este backlog é local e executável por um mantenedor único. Ele separa o que estabiliza a instalação owner-only no Apple M3 do que só faz sentido quando houver audiência externa. Nenhum item abaixo implica criação automática de issue remota.

## Top 20 priorizado

| ID | Classe | Título | Dependências | Versão/audiência |
| --- | --- | --- | --- | --- |
| S0-001 | NOW/P0 | Manter main verde e preservar o contrato DNS determinístico | nenhuma | sem release se test-only / owner-only |
| S0-002 | NOW/P1 | Verificar convergência da projeção site, bootstraps, product e release-truth | S0-001 | documental/deploy protegido / owner-only |
| S0-003 | NOW/P1 | Aceitação do candidato exato no Apple M3 | S0-001, S0-002 | 1.0.x se bytes mudarem / owner-only |
| S0-004 | NOW/P1 | Renovação e monitor técnico da lease TUF | nenhuma | operação contínua / owner-only |
| S0-005 | NEXT/P1 | Observação operacional owner-only e registro de incidentes | S0-003, S0-004 | 1.0.x / owner-only |
| FUNC-001 | NOW/P1 | Implementar `x86qw doctor` read-only | S0-003 | 1.1 / owner-only (PR #191) |
| FUNC-002 | NOW/P1 | Gerar bundle de diagnóstico sanitizado e revisável | FUNC-001 | 1.1 / owner-only (PR #191) |
| FUNC-003 | NOW/P1 | Separar perfis, defaults, cache e dados pessoais | S0-003 | 1.1 / owner-only |
| FUNC-004 | NEXT/P1 | Favoritos e recentes locais com origem e freshness | FUNC-003 | 1.1 / owner-only |
| FUNC-005 | NEXT/P1 | Descoberta e entrada em partidas com fallback local | FUNC-004 | 1.1 / owner-only |
| FUNC-006 | NEXT/P1 | Presets declarativos de hospedagem local | S0-003 | 1.2 / owner-only |
| FUNC-007 | NEXT/P1 | Readiness, logs, stop e crash recovery de servidor | FUNC-006 | 1.2 / owner-only |
| FUNC-008 | LATER/P2 | UI local read-only sobre os contratos do runtime | FUNC-001, FUNC-005 | 1.2 / owner-only |
| OPS-001 | LATER/P1 | Custódia independente, backup e RTO TUF | S0-004 | external-public only |
| OPS-002 | LATER/P1 | Soak de sete dias do candidato external-public exato | decisão EP-0 | external-public only |
| OPS-003 | LATER/P1 | Aceitação por usuário não mantenedor | OPS-002 | external-public only |
| OPS-004 | LATER/P1 | Migração histórica real | promessa de upgrade antigo | external-public only, condicional |
| OPS-005 | LATER/P1 | Evidência nativa por plataforma adicional | laboratório nativo | external-public por plataforma |
| EXT-001 | BLOCKED_EXTERNAL | RFC e adapter oficial QWLeague | documentação/autorização oficial | opcional / 1.3 |

## Ordem de execução

1. Fechar S0 no M3.
2. Iniciar FUNC-001 e FUNC-002 em pequenas PRs, mantendo main verde.
3. Seguir para perfis, descoberta e hospedagem local.
4. Só reabrir OPS-001 a OPS-005 quando a audiência mudar para external-public.

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
