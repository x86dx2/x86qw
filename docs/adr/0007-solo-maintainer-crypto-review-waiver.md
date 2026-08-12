# ADR 0007 — Waiver de revisão criptográfica externa no maintainer único

- **Estado:** aprovado pelo proprietário; waiver operacional; não é revisão independente
- **Data:** 2026-08-07
- **Issue:** #48
- **Escopo:** permitir a continuidade documentada de E1/E2 quando o repositório tem
  um único proprietário e não existe um segundo revisor elegível

## Contexto

O plano de estabilização exige revisão criptográfica independente para trust e
aprovação humana explícita para a promoção final. O repositório
`x86dx2/x86qw` é mantido por um único proprietário, `x86dx2`, que também é o
autor das PRs. O GitHub não permite que o autor aprove a própria PR, e não há
outro colaborador, equipe ou `CODEOWNERS` disponível neste momento.

A ausência de um segundo revisor é uma limitação operacional real. Ela não deve
ser convertida em uma review GitHub fictícia, em uma conta auxiliar ou em uma
alegação de revisão independente.

## Decisão

O proprietário aprova explicitamente este waiver e aceita o risco de revisão
independente ausente para permitir a continuidade do plano. A exceção tem os
seguintes limites:

1. A aprovação do proprietário é registrada em comentários nas PRs e neste ADR.
   Ela é uma decisão de governança, não uma revisão criptográfica independente.
2. Nenhuma parte do histórico deve declarar que houve revisão externa,
   custódia multioperador ou threshold independente.
3. O waiver não autoriza promover fixtures, chaves de teste, metadata expirada,
   RSA-PSS própria ou root de checkpoint como trust de produção.
4. A implementação deve preferir `python-tuf`/securesystemslib` e os vetores
   oficiais correspondentes; não se adiciona uma nova primitiva criptográfica
   apenas para compensar a ausência de um revisor.
5. Thresholds, rotação, expiração, rollback, freeze, equivocation e root não
   ancorado continuam sendo fail-closed e precisam de testes adversariais.
6. Uma única pessoa pode custodiar o material somente como exceção declarada.
   Múltiplas chaves controladas pela mesma pessoa não equivalem a custodians
   independentes e não elevam a garantia do threshold.
7. O waiver não substitui cerimônia de chaves, fingerprints, metadata assinada,
   evidência nativa M3, hashes, mirrors ou os demais gates de release.

## Evidência obrigatória antes de qualquer trust produtivo

Mesmo com o waiver, a promoção de trust continua condicionada a:

- dependências e versões fixadas, com hashes verificados;
- testes dos vetores oficiais e dos casos de rollback, freeze, expiração,
  equivocation e rotação;
- geração de chaves fora do workspace e registro dos fingerprints públicos;
- metadata assinada com bytes, versões e digests vinculados;
- `release-evidence.json` autenticado para o candidato exato;
- evidência M3 real e período de uso do RC;
- validação independente de cada mirror, sem overwrite ou rebuild;
- documentação coerente com o estado efetivamente publicado.

Até que esses artefatos existam, o verificador deve continuar falhando fechado e
nenhuma versão 1.0 deve ser anunciada como trust de produção.

## Revisão futura e expiração

Este waiver deve ser revisitado assim que um colaborador independente com
permissão de revisão estiver disponível. A revisão futura deve comparar a
implementação com os vetores oficiais, confirmar a custódia, validar a
cerimônia e registrar uma aprovação separada.

O waiver não transforma a política solo em estado permanente: ele é uma
exceção de maintainer único para esta execução do plano e deve ser removido ou
substituído por uma revisão externa antes de uma mudança que aumente o risco
criptográfico, de custódia ou de publicação.

## Consequências

O plano pode avançar com uma trilha de auditoria honesta e com a aprovação
explícita do único proprietário. A garantia de revisão independente permanece
ausente e é tratada como risco aceito, não como requisito cumprido. Qualquer
release que não satisfaça as evidências obrigatórias continua bloqueada por
falha fechada, mesmo que a governança solo tenha sido aprovada.
