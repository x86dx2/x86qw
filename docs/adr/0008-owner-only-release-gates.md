# ADR 0008 — Gates da fase owner-only

**Status:** aceito

**Data:** 2026-08-14

**Decisor:** mantenedor único do x86QW (`x86dx2`)

## Contexto

O x86QW está nascendo do zero e, nesta fase, possui um único usuário e um único
mantenedor. O usuário pode apagar o destino completo e instalar novamente. Não
existe ainda compromisso de preservar instalações, dados ou configurações de
usuários externos.

O repositório já contém contratos de migração, fixtures históricas e o comando
`migrate`. Esses componentes continuam disponíveis, mas não devem transformar
uma compatibilidade histórica ainda não necessária em bloqueio para a primeira
instalação do produto.

## Decisão

O ciclo atual opera em `release_audience=owner-only`.

O gate de aceitação obrigatório deste ciclo comprova:

- instalação limpa em destino descartável;
- `version`, `changes`, `verify` e lifecycle do launcher;
- update aplicado e idempotente em destino descartável;
- uninstall conservador e purge explícito;
- evidência nativa Apple M3 do candidato exato;
- contratos portáveis, build-once, bytes imutáveis, ownership, SBOM e
  provenance;
- convergência dos assets publicados quando uma release for publicada.

O gate `single-user` não exige:

- instalação de `0.7.13`;
- migração histórica ou preservação de dados de uma instalação antiga;
- soak de sete dias voltado a usuários externos;
- drill de operação TUF de produção para disponibilidade contínua;
- aceitação por usuários externos.

O código de migração e suas fixtures ficam preservados como capacidade
`post-public`, e não como requisito da versão owner-only.

## Transição para usuários externos

Quando o mantenedor declarar que o produto será liberado para usuários
externos, a audiência deve mudar explicitamente para
`release_audience=external-public`. A partir dessa decisão, o workflow exige:

1. aceitação com `acceptance_scope=external-users`;
2. migração real da baseline suportada;
3. soak protegido do candidato;
4. operação TUF sustentável e recuperação comprovada;
5. evidência pública durável vinculada aos bytes externos.

A mudança de audiência é uma decisão de release e deve ser registrada em uma
nova ata/PR antes da promoção. Um valor omitido continua sendo `owner-only` no
workflow, mas a promoção ainda precisa dos gates próprios desse modo.

## Riscos aceitos

No modo owner-only, uma instalação antiga pode ser descartada em vez de
migrada. Isso é uma limitação deliberada do produto nesta fase, não uma falha
oculta. A documentação não deve anunciar compatibilidade histórica enquanto a
transição para `external-public` não for registrada.

## Evidência e auditoria

Cada promoção registra no recibo durável:

- `release_audience`;
- commit e identidade do candidato;
- escopo da aceitação;
- evidência M3 e root de evidência;
- assets e mirrors;
- gates externos, quando aplicáveis.

O recibo `owner-only` não contém handoffs de soak ou operação TUF externa. O
recibo `external-public` continua exigindo esses handoffs e a divergência de
bytes entre o candidato final e o RC sob soak.
